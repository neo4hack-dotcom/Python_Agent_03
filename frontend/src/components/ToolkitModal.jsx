import React, { useEffect, useState } from 'react'
import { X, Save, Wrench, ToggleLeft, ToggleRight } from 'lucide-react'
import { toolkitsApi } from '../services/api'

const DB_TYPES = [
  { value: 'clickhouse', label: '📊 ClickHouse' },
  { value: 'oracle', label: '🔮 Oracle' },
]

export default function ToolkitModal({ toolkit, onClose, onSaved }) {
  const isEdit = !!toolkit
  const [form, setForm] = useState({
    name: toolkit?.name || '',
    description: toolkit?.description || '',
    db_type: toolkit?.db_type || 'clickhouse',
    tools: toolkit?.tools || [],
  })
  const [templates, setTemplates] = useState({ clickhouse: [], oracle: [] })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [loadingTemplates, setLoadingTemplates] = useState(true)

  // Load tool templates on mount
  useEffect(() => {
    toolkitsApi.getTemplates()
      .then(r => {
        setTemplates(r.data)
        // If creating new toolkit and no tools yet, seed from template
        if (!isEdit && form.tools.length === 0) {
          const tpls = r.data[form.db_type] || []
          setForm(f => ({
            ...f,
            tools: tpls.map(t => ({ name: t.name, description: null, enabled: t.enabled ?? true })),
          }))
        }
      })
      .catch(() => {})
      .finally(() => setLoadingTemplates(false))
  }, [])

  // When db_type changes, reset tools to the template for that type
  const handleDbTypeChange = (db_type) => {
    const tpls = templates[db_type] || []
    setForm(f => ({
      ...f,
      db_type,
      tools: tpls.map(t => ({ name: t.name, description: null, enabled: t.enabled ?? true })),
    }))
  }

  const handleToolToggle = (name) => {
    setForm(f => ({
      ...f,
      tools: f.tools.map(t =>
        t.name === name ? { ...t, enabled: !t.enabled } : t
      ),
    }))
  }

  const handleToolDescription = (name, description) => {
    setForm(f => ({
      ...f,
      tools: f.tools.map(t =>
        t.name === name ? { ...t, description: description || null } : t
      ),
    }))
  }

  const handleSave = async () => {
    if (!form.name.trim()) { setError('Le nom est obligatoire'); return }
    if (form.tools.length === 0) { setError('Au moins un outil est requis'); return }
    setSaving(true)
    setError('')
    try {
      if (isEdit) {
        await toolkitsApi.update(toolkit.id, form)
      } else {
        await toolkitsApi.create(form)
      }
      onSaved()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setSaving(false)
    }
  }

  const getDefaultDescription = (toolName) => {
    const tpls = templates[form.db_type] || []
    const tpl = tpls.find(t => t.name === toolName)
    return tpl?.description || ''
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 620 }}>
        <div className="modal-header">
          <div className="flex items-center gap-2">
            <Wrench size={18} />
            <h3>{isEdit ? `Modifier : ${toolkit.name}` : 'Créer un toolkit'}</h3>
          </div>
          <button className="btn btn-icon btn-secondary" onClick={onClose}>
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
            <label className="form-label">Nom du toolkit *</label>
            <input
              className="input"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              placeholder="ex: ClickHouse Production"
            />
          </div>

          <div className="form-group">
            <label className="form-label">Description</label>
            <input
              className="input"
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              placeholder="Description optionnelle..."
            />
          </div>

          <div className="form-group">
            <label className="form-label">Type de base de données</label>
            <div style={{ display: 'flex', gap: 8 }}>
              {DB_TYPES.map(dt => (
                <div
                  key={dt.value}
                  onClick={() => !isEdit && handleDbTypeChange(dt.value)}
                  style={{
                    padding: '8px 16px',
                    borderRadius: 8,
                    border: `1px solid ${form.db_type === dt.value ? 'var(--accent)' : 'var(--border)'}`,
                    background: form.db_type === dt.value ? 'rgba(99,102,241,0.1)' : 'var(--bg-primary)',
                    cursor: isEdit ? 'default' : 'pointer',
                    fontWeight: 600,
                    fontSize: 13,
                    opacity: isEdit ? 0.7 : 1,
                  }}
                >
                  {dt.label}
                </div>
              ))}
            </div>
            {isEdit && (
              <p className="text-sm text-muted" style={{ marginTop: 4 }}>
                Le type de DB ne peut pas être modifié après création.
              </p>
            )}
          </div>

          <div className="form-group">
            <label className="form-label">Outils disponibles</label>
            <p className="text-sm text-muted" style={{ marginBottom: 10 }}>
              Activez / désactivez les outils exposés au LLM. Vous pouvez aussi personnaliser
              leur description pour guider le comportement de l'agent.
            </p>

            {loadingTemplates ? (
              <div style={{ textAlign: 'center', padding: 20 }}><div className="spinner" /></div>
            ) : form.tools.length === 0 ? (
              <p className="text-sm text-muted">Aucun outil configuré.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {form.tools.map(tool => (
                  <ToolRow
                    key={tool.name}
                    tool={tool}
                    defaultDescription={getDefaultDescription(tool.name)}
                    onToggle={() => handleToolToggle(tool.name)}
                    onDescriptionChange={desc => handleToolDescription(tool.name, desc)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose}>Annuler</button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? <div className="spinner" /> : <Save size={15} />}
            {isEdit ? 'Sauvegarder' : 'Créer'}
          </button>
        </div>
      </div>
    </div>
  )
}

function ToolRow({ tool, defaultDescription, onToggle, onDescriptionChange }) {
  const [showDesc, setShowDesc] = useState(false)

  const toolLabels = {
    list_tables: { icon: '📋', label: 'list_tables', hint: 'Lister les tables disponibles' },
    get_schema: { icon: '🔍', label: 'get_schema', hint: 'Obtenir le schéma des colonnes' },
    execute_query: { icon: '⚡', label: 'execute_query', hint: 'Exécuter une requête SELECT' },
    check_query: { icon: '✅', label: 'check_query', hint: 'Valider la syntaxe avec EXPLAIN' },
  }
  const info = toolLabels[tool.name] || { icon: '🔧', label: tool.name, hint: '' }

  return (
    <div
      style={{
        border: `1px solid ${tool.enabled ? 'var(--accent)' : 'var(--border)'}`,
        borderRadius: 8,
        padding: '10px 12px',
        background: tool.enabled ? 'rgba(99,102,241,0.05)' : 'var(--bg-primary)',
        opacity: tool.enabled ? 1 : 0.6,
        transition: 'all 0.15s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 18 }}>{info.icon}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 13, fontFamily: 'var(--font-mono)' }}>
            {info.label}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{info.hint}</div>
        </div>
        <button
          className="btn btn-icon btn-secondary"
          style={{ padding: '2px 6px', fontSize: 11, color: 'var(--text-muted)' }}
          onClick={() => setShowDesc(!showDesc)}
          title="Personnaliser la description"
        >
          {showDesc ? '▲' : '▼'} desc
        </button>
        <button
          onClick={onToggle}
          style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
          title={tool.enabled ? 'Désactiver' : 'Activer'}
        >
          {tool.enabled
            ? <ToggleRight size={24} color="var(--accent)" />
            : <ToggleLeft size={24} color="var(--text-muted)" />
          }
        </button>
      </div>

      {showDesc && (
        <div style={{ marginTop: 8 }}>
          <textarea
            className="textarea"
            value={tool.description || ''}
            onChange={e => onDescriptionChange(e.target.value)}
            placeholder={defaultDescription || 'Description personnalisée (vide = description par défaut)'}
            style={{ minHeight: 60, fontSize: 12, fontFamily: 'var(--font-mono)' }}
          />
          <p className="text-sm text-muted" style={{ marginTop: 4 }}>
            Laissez vide pour utiliser la description par défaut.
          </p>
        </div>
      )}
    </div>
  )
}
