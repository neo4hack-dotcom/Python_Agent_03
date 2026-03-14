import React, { useRef, useState } from 'react'
import { X, Download, Upload, CheckCircle, XCircle, AlertTriangle } from 'lucide-react'
import { configApi, downloadBlob } from '../services/api'

export default function ConfigModal({ onClose }) {
  const fileRef = useRef(null)
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [importMode, setImportMode] = useState('merge')
  const [includePasswords, setIncludePasswords] = useState(true)
  const [result, setResult] = useState(null) // { success, imported, skipped, errors } ou { exportError }

  // ── Export ──────────────────────────────────────────────────
  const handleExport = async () => {
    setExporting(true)
    setResult(null)
    try {
      const blob = await configApi.export(includePasswords)
      const date = new Date().toISOString().slice(0, 10)
      downloadBlob(blob, `agent-platform-config-${date}.json`)
    } catch (e) {
      setResult({ exportError: e.response?.data?.detail || e.message })
    } finally {
      setExporting(false)
    }
  }

  // ── Import ──────────────────────────────────────────────────
  const handleFileChange = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''            // reset pour permettre de re-sélectionner le même fichier
    setImporting(true)
    setResult(null)
    try {
      const text = await file.text()
      const bundle = JSON.parse(text)
      const r = await configApi.import(bundle, importMode)
      setResult(r.data)
    } catch (e) {
      if (e instanceof SyntaxError) {
        setResult({ success: false, errors: ['Fichier JSON invalide'], imported: {}, skipped: {} })
      } else {
        setResult({ success: false, errors: [e.response?.data?.detail || e.message], imported: {}, skipped: {} })
      }
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 500 }} onClick={(e) => e.stopPropagation()}>

        {/* Header */}
        <div className="modal-header">
          <div className="flex items-center gap-2">
            <Download size={18} />
            <h3>Export / Import de configuration</h3>
          </div>
          <button type="button" className="btn btn-icon btn-secondary" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        <div className="modal-body">

          {/* ── EXPORT ────────────────────────────────────────── */}
          <div className="card" style={{ gap: 12 }}>
            <div className="card-header" style={{ marginBottom: 4 }}>
              <h4 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
                <Download size={15} /> Exporter
              </h4>
            </div>
            <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0 }}>
              Télécharge un fichier JSON avec la config LLM, les connexions DB et les agents.
            </p>

            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={includePasswords}
                onChange={(e) => setIncludePasswords(e.target.checked)}
                style={{ accentColor: 'var(--accent)', width: 15, height: 15 }}
              />
              <span style={{ color: 'var(--text-secondary)' }}>Inclure les mots de passe des connexions</span>
            </label>

            <button
              type="button"
              className="btn btn-primary"
              onClick={handleExport}
              disabled={exporting}
              style={{ alignSelf: 'flex-start' }}
            >
              {exporting ? <div className="spinner" /> : <Download size={15} />}
              Télécharger la configuration
            </button>

            {result?.exportError && (
              <div style={{ fontSize: 13, color: 'var(--error)' }}>✗ {result.exportError}</div>
            )}
          </div>

          {/* ── IMPORT ────────────────────────────────────────── */}
          <div className="card" style={{ gap: 12 }}>
            <div className="card-header" style={{ marginBottom: 4 }}>
              <h4 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
                <Upload size={15} /> Importer
              </h4>
            </div>
            <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0 }}>
              Restaure une configuration depuis un fichier JSON exporté précédemment.
            </p>

            {/* Mode */}
            <div className="form-group" style={{ margin: 0 }}>
              <label className="form-label" style={{ marginBottom: 6 }}>Mode d'import</label>
              <div className="flex gap-2">
                {[
                  { value: 'merge', label: 'Fusionner', desc: 'Conserve l\'existant, ajoute le nouveau' },
                  { value: 'replace', label: 'Remplacer', desc: 'Réinitialise avant d\'importer' },
                ].map((m) => (
                  <div
                    key={m.value}
                    onClick={() => setImportMode(m.value)}
                    style={{
                      flex: 1, padding: '8px 12px', borderRadius: 8, cursor: 'pointer',
                      border: `1px solid ${importMode === m.value ? 'var(--accent)' : 'var(--border)'}`,
                      background: importMode === m.value ? 'rgba(99,102,241,0.1)' : 'var(--bg-primary)',
                    }}
                  >
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{m.label}</div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{m.desc}</div>
                  </div>
                ))}
              </div>
            </div>

            {importMode === 'replace' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--warning)', padding: '6px 10px', background: 'rgba(245,158,11,0.08)', borderRadius: 6 }}>
                <AlertTriangle size={13} />
                Le mode Remplacer supprime les configs existantes avant l'import.
              </div>
            )}

            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => fileRef.current?.click()}
              disabled={importing}
              style={{ alignSelf: 'flex-start' }}
            >
              {importing ? <div className="spinner" /> : <Upload size={15} />}
              Choisir un fichier JSON…
            </button>
            <input ref={fileRef} type="file" accept=".json,application/json" style={{ display: 'none' }} onChange={handleFileChange} />

            {/* Résultat import */}
            {result && !result.exportError && (
              <div style={{
                padding: '10px 14px', borderRadius: 8, fontSize: 13,
                background: result.success ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)',
                border: `1px solid ${result.success ? 'var(--success)' : 'var(--error)'}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600, marginBottom: 6,
                  color: result.success ? 'var(--success)' : 'var(--error)' }}>
                  {result.success ? <CheckCircle size={14} /> : <XCircle size={14} />}
                  {result.success ? 'Import réussi' : 'Import partiel ou échoué'}
                </div>
                <div style={{ display: 'flex', gap: 16, color: 'var(--text-secondary)', fontSize: 12 }}>
                  <span>✓ LLM : {result.imported?.llm_config ?? 0}</span>
                  <span>✓ Connexions : {result.imported?.connections ?? 0}</span>
                  <span>✓ Agents : {result.imported?.agents ?? 0}</span>
                </div>
                {result.errors?.length > 0 && (
                  <ul style={{ margin: '6px 0 0', paddingLeft: 16, color: 'var(--error)', fontSize: 11 }}>
                    {result.errors.map((err, i) => <li key={i}>{err}</li>)}
                  </ul>
                )}
              </div>
            )}
          </div>

        </div>

        <div className="modal-footer">
          <button type="button" className="btn btn-secondary" onClick={onClose}>Fermer</button>
        </div>
      </div>
    </div>
  )
}
