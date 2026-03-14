import React, { useState } from 'react'
import { X, Save, Plug } from 'lucide-react'
import { connectionsApi } from '../services/api'

const DEFAULTS = {
  clickhouse: { port: 8123, database: 'default', username: 'default' },
  oracle: { port: 1521, database: 'ORCL', username: '' },
}

// Nettoie l'hôte : retire http(s):// et le port intégré s'il est déjà dans le champ port
function normalizeHost(raw) {
  let h = (raw || '').trim()
  h = h.replace(/^https?:\/\//i, '') // strip protocol
  h = h.split('/')[0]                 // strip path
  return h
}

export default function ConnectionModal({ connection, onClose, onSaved }) {
  const isEdit = !!connection
  const [form, setForm] = useState({
    name: connection?.name || '',
    type: connection?.type || 'clickhouse',
    host: connection?.host || '',
    port: connection?.port || DEFAULTS.clickhouse.port,
    database: connection?.database || DEFAULTS.clickhouse.database,
    username: connection?.username || DEFAULTS.clickhouse.username,
    password: '',
    description: connection?.description || '',
    extra_params: connection?.extra_params || {},
  })
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [error, setError] = useState('')

  const busy = testing || saving

  const handleTypeChange = (type) => {
    const def = DEFAULTS[type] || {}
    setForm((f) => ({ ...f, type, port: def.port || f.port, database: def.database || f.database, username: def.username || f.username }))
  }

  // Normaliser l'hôte silencieusement quand l'utilisateur quitte le champ
  const handleHostBlur = () => {
    const cleaned = normalizeHost(form.host)
    if (cleaned !== form.host) setForm(f => ({ ...f, host: cleaned }))
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    setError('')
    let tempId = null
    try {
      const payload = { ...form, host: normalizeHost(form.host) }
      let id = connection?.id
      if (!id) {
        const r = await connectionsApi.create(payload)
        tempId = r.data.id
        id = tempId
      } else {
        await connectionsApi.update(id, payload)
      }
      const tr = await connectionsApi.test(id)
      setTestResult(tr.data)
    } catch (e) {
      setTestResult({ success: false, error: e.response?.data?.detail || e.message })
    } finally {
      if (tempId) connectionsApi.delete(tempId).catch(() => {})
      setTesting(false)
    }
  }

  const handleSave = async () => {
    if (!form.name.trim()) { setError('Le nom est obligatoire'); return }
    if (!form.host.trim()) { setError("L'hôte est obligatoire"); return }
    const payload = { ...form, host: normalizeHost(form.host) }
    setSaving(true)
    setError('')
    try {
      if (isEdit) {
        await connectionsApi.update(connection.id, payload)
      } else {
        await connectionsApi.create(payload)
      }
      onSaved()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    // Clic sur l'overlay → fermer (si pas occupé)
    <div className="modal-overlay" onClick={() => !busy && onClose()}>
      {/* stopPropagation : les clics à l'intérieur de la modal ne remontent pas à l'overlay */}
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="flex items-center gap-2">
            <Plug size={18} />
            <h3>{isEdit ? `Modifier : ${connection.name}` : 'Nouvelle connexion'}</h3>
          </div>
          <button type="button" className="btn btn-icon btn-secondary" onClick={onClose} disabled={busy}>
            <X size={16} />
          </button>
        </div>

        <div className="modal-body">
          {error && (
            <div className="form-error" style={{ padding: '8px 12px', background: 'rgba(239,68,68,0.1)', borderRadius: 6 }}>
              {error}
            </div>
          )}

          <div className="form-group">
            <label className="form-label">Nom *</label>
            <input className="input" value={form.name} onChange={(e) => setForm(f => ({ ...f, name: e.target.value }))} placeholder="ex: ClickHouse Production" />
          </div>

          <div className="form-group">
            <label className="form-label">Type</label>
            <div className="flex gap-3">
              {[
                { value: 'clickhouse', label: '🟡 ClickHouse' },
                { value: 'oracle', label: '🔴 Oracle' },
              ].map((t) => (
                <div
                  key={t.value}
                  onClick={() => handleTypeChange(t.value)}
                  style={{
                    flex: 1, padding: '10px 14px', borderRadius: 8,
                    border: `1px solid ${form.type === t.value ? 'var(--accent)' : 'var(--border)'}`,
                    background: form.type === t.value ? 'rgba(99,102,241,0.1)' : 'var(--bg-primary)',
                    cursor: 'pointer', textAlign: 'center', fontWeight: 600, fontSize: 13,
                  }}
                >
                  {t.label}
                </div>
              ))}
            </div>
          </div>

          <div className="form-row">
            <div className="form-group">
              <label className="form-label">
                Hôte *
                <span style={{ fontWeight: 400, color: 'var(--text-muted)', marginLeft: 6, fontSize: 11 }}>
                  (ex: localhost ou 192.168.1.x — sans http://)
                </span>
              </label>
              <input
                className="input"
                value={form.host}
                onChange={(e) => setForm(f => ({ ...f, host: e.target.value }))}
                onBlur={handleHostBlur}
                placeholder="localhost"
              />
            </div>
            <div className="form-group">
              <label className="form-label">Port</label>
              <input className="input" type="number" value={form.port} onChange={(e) => setForm(f => ({ ...f, port: parseInt(e.target.value) }))} />
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">{form.type === 'oracle' ? 'Service Name / SID' : 'Base de données'}</label>
            <input className="input" value={form.database} onChange={(e) => setForm(f => ({ ...f, database: e.target.value }))} />
          </div>

          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Utilisateur</label>
              <input className="input" value={form.username} onChange={(e) => setForm(f => ({ ...f, username: e.target.value }))} />
            </div>
            <div className="form-group">
              <label className="form-label">Mot de passe</label>
              <input className="input" type="password" value={form.password} onChange={(e) => setForm(f => ({ ...f, password: e.target.value }))} placeholder={isEdit ? '(inchangé si vide)' : ''} />
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Description</label>
            <input className="input" value={form.description} onChange={(e) => setForm(f => ({ ...f, description: e.target.value }))} placeholder="Usage, environnement, etc." />
          </div>

          {testResult && (
            <div style={{
              padding: '10px 14px', borderRadius: 8,
              background: testResult.success ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
              border: `1px solid ${testResult.success ? 'var(--success)' : 'var(--error)'}`,
              color: testResult.success ? 'var(--success)' : 'var(--error)',
              fontSize: 13,
            }}>
              {testResult.success ? `✓ Connexion OK — ${testResult.version || ''}` : `✗ ${testResult.error}`}
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn btn-secondary" onClick={handleTest} disabled={busy}>
            {testing ? <div className="spinner" /> : null}
            Tester
          </button>
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>Annuler</button>
          <button type="button" className="btn btn-primary" onClick={handleSave} disabled={busy}>
            {saving ? <div className="spinner" /> : <Save size={15} />}
            {isEdit ? 'Sauvegarder' : 'Créer'}
          </button>
        </div>
      </div>
    </div>
  )
}
