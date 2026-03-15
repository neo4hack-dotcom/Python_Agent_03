import React, { useEffect, useState } from 'react'
import { X, Save, Wrench, ToggleLeft, ToggleRight, Sparkles, Loader } from 'lucide-react'
import { toolkitsApi, connectionsApi } from '../services/api'

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

  // AI generation state
  const [showAI, setShowAI] = useState(!isEdit)
  const [aiDescription, setAIDescription] = useState('')
  const [aiConnectionId, setAIConnectionId] = useState('')
  const [generating, setGenerating] = useState(false)
  const [aiError, setAIError] = useState('')
  const [connections, setConnections] = useState([])

  useEffect(() => {
    // Load templates and connections in parallel
    Promise.all([
      toolkitsApi.getTemplates(),
      connectionsApi.list(),
    ]).then(([tplRes, connRes]) => {
      setTemplates(tplRes.data)
      setConnections(connRes.data || [])
      if (!isEdit && form.tools.length === 0) {
        const tpls = tplRes.data[form.db_type] || []
        setForm(f => ({
          ...f,
          tools: tpls.map(t => ({ name: t.name, description: null, enabled: t.enabled ?? true })),
        }))
      }
    }).catch(() => {}).finally(() => setLoadingTemplates(false))
  }, [])

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
      tools: f.tools.map(t => t.name === name ? { ...t, enabled: !t.enabled } : t),
    }))
  }

  const handleToolDescription = (name, description) => {
    setForm(f => ({
      ...f,
      tools: f.tools.map(t => t.name === name ? { ...t, description: description || null } : t),
    }))
  }

  const handleGenerate = async () => {
    if (!aiDescription.trim()) { setAIError('Décrivez votre cas d\'usage'); return }
    setGenerating(true)
    setAIError('')
    try {
      const res = await toolkitsApi.generate({
        description: aiDescription,
        db_type: form.db_type,
        connection_id: aiConnectionId || undefined,
      })
      // Apply generated values to form
      setForm(f => ({
        ...f,
        name: res.data.name || f.name,
        description: res.data.description || f.description,
        db_type: res.data.db_type || f.db_type,
        tools: res.data.tools || f.tools,
      }))
      setShowAI(false) // switch to manual view to review
    } catch (e) {
      setAIError(e.response?.data?.detail || 'Erreur lors de la génération')
    } finally {
      setGenerating(false)
    }
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
    return (tpls.find(t => t.name === toolName) || {}).description || ''
  }

  // Filter connections by db_type
  const filteredConnections = connections.filter(c => {
    const ct = c.db_type || c.type || ''
    return form.db_type === 'oracle' ? ct === 'oracle' : ct === 'clickhouse'
  })

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 640 }}>
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
          {/* ── AI Generation Panel ─────────────────────────────────────── */}
          {!isEdit && (
            <div style={{
              marginBottom: 20,
              border: `1px solid ${showAI ? 'rgba(139,92,246,0.4)' : 'var(--border)'}`,
              borderRadius: 10,
              overflow: 'hidden',
            }}>
              <button
                onClick={() => setShowAI(!showAI)}
                style={{
                  width: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '10px 14px',
                  background: showAI ? 'rgba(139,92,246,0.08)' : 'var(--bg-secondary)',
                  border: 'none',
                  cursor: 'pointer',
                  color: showAI ? 'rgba(139,92,246,1)' : 'var(--text-secondary)',
                  fontWeight: 600,
                  fontSize: 13,
                  textAlign: 'left',
                }}
              >
                <Sparkles size={15} />
                ✨ Générer avec l'IA
                <span style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 400 }}>
                  {showAI ? '▲ replier' : '▼ décrire mon cas d\'usage'}
                </span>
              </button>

              {showAI && (
                <div style={{ padding: '14px 16px', background: 'rgba(139,92,246,0.04)', borderTop: '1px solid rgba(139,92,246,0.2)' }}>
                  <p className="text-sm text-muted" style={{ marginBottom: 12 }}>
                    Décrivez votre cas d'usage en langage naturel. Le LLM va générer des descriptions
                    d'outils personnalisées et adaptées à votre contexte métier.
                  </p>

                  {/* db_type for generation */}
                  <div className="form-group" style={{ marginBottom: 10 }}>
                    <label className="form-label">Type de base</label>
                    <div style={{ display: 'flex', gap: 8 }}>
                      {DB_TYPES.map(dt => (
                        <div
                          key={dt.value}
                          onClick={() => handleDbTypeChange(dt.value)}
                          style={{
                            padding: '6px 14px',
                            borderRadius: 6,
                            border: `1px solid ${form.db_type === dt.value ? 'rgba(139,92,246,0.6)' : 'var(--border)'}`,
                            background: form.db_type === dt.value ? 'rgba(139,92,246,0.12)' : 'var(--bg-primary)',
                            cursor: 'pointer',
                            fontWeight: 600,
                            fontSize: 12,
                          }}
                        >
                          {dt.label}
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Optional connection for real schema */}
                  {filteredConnections.length > 0 && (
                    <div className="form-group" style={{ marginBottom: 10 }}>
                      <label className="form-label">
                        Connexion <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(optionnel — pour injecter le schéma réel)</span>
                      </label>
                      <select
                        className="select"
                        value={aiConnectionId}
                        onChange={e => setAIConnectionId(e.target.value)}
                      >
                        <option value="">-- Sans connexion --</option>
                        {filteredConnections.map(c => (
                          <option key={c.id} value={c.id}>{c.name}</option>
                        ))}
                      </select>
                    </div>
                  )}

                  {/* Description textarea */}
                  <div className="form-group" style={{ marginBottom: 10 }}>
                    <label className="form-label">Décrivez votre cas d'usage *</label>
                    <textarea
                      className="textarea"
                      value={aiDescription}
                      onChange={e => setAIDescription(e.target.value)}
                      placeholder="Ex: Je veux analyser les ventes e-commerce par région et par produit. Les tables principales sont orders, order_items et products. Je veux des KPIs comme le CA mensuel, le panier moyen et le taux de conversion."
                      style={{ minHeight: 90, fontSize: 13 }}
                    />
                  </div>

                  {aiError && (
                    <div style={{ padding: '6px 10px', background: 'rgba(239,68,68,0.1)', borderRadius: 6, fontSize: 12, color: 'var(--error)', marginBottom: 10 }}>
                      {aiError}
                    </div>
                  )}

                  <button
                    className="btn btn-primary"
                    onClick={handleGenerate}
                    disabled={generating}
                    style={{ background: 'linear-gradient(135deg, rgba(139,92,246,0.9), rgba(99,102,241,0.9))' }}
                  >
                    {generating ? <Loader size={14} className="spin" /> : <Sparkles size={14} />}
                    {generating ? 'Génération en cours…' : 'Générer le toolkit'}
                  </button>
                </div>
              )}
            </div>
          )}

          {/* ── Manual Form ─────────────────────────────────────────────── */}
          {error && (
            <div style={{ padding: '8px 12px', background: 'rgba(239,68,68,0.1)', borderRadius: 6, marginBottom: 12, fontSize: 13 }}>
              {error}
            </div>
          )}

          <div className="form-group">
            <label className="form-label">Nom du toolkit *</label>
            <input
              className="input"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              placeholder="ex: ClickHouse Production Ventes"
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

          {!isEdit && (
            <div className="form-group">
              <label className="form-label">Type de base de données</label>
              <div style={{ display: 'flex', gap: 8 }}>
                {DB_TYPES.map(dt => (
                  <div
                    key={dt.value}
                    onClick={() => handleDbTypeChange(dt.value)}
                    style={{
                      padding: '8px 16px',
                      borderRadius: 8,
                      border: `1px solid ${form.db_type === dt.value ? 'var(--accent)' : 'var(--border)'}`,
                      background: form.db_type === dt.value ? 'rgba(99,102,241,0.1)' : 'var(--bg-primary)',
                      cursor: 'pointer',
                      fontWeight: 600,
                      fontSize: 13,
                    }}
                  >
                    {dt.label}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="form-group">
            <label className="form-label">Outils disponibles</label>
            <p className="text-sm text-muted" style={{ marginBottom: 10 }}>
              Activez / désactivez les outils exposés au LLM. Personnalisez leur description
              pour guider le comportement de l'agent.
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
  const [showDesc, setShowDesc] = useState(!!(tool.description))

  const toolLabels = {
    list_tables: { icon: '📋', label: 'list_tables', hint: 'Lister les tables disponibles' },
    get_schema: { icon: '🔍', label: 'get_schema', hint: 'Obtenir le schéma des colonnes' },
    execute_query: { icon: '⚡', label: 'execute_query', hint: 'Exécuter une requête SELECT' },
    check_query: { icon: '✅', label: 'check_query', hint: 'Valider la syntaxe avec EXPLAIN' },
  }
  const info = toolLabels[tool.name] || { icon: '🔧', label: tool.name, hint: '' }

  return (
    <div style={{
      border: `1px solid ${tool.enabled ? 'var(--accent)' : 'var(--border)'}`,
      borderRadius: 8,
      padding: '10px 12px',
      background: tool.enabled ? 'rgba(99,102,241,0.05)' : 'var(--bg-primary)',
      opacity: tool.enabled ? 1 : 0.6,
      transition: 'all 0.15s',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 18 }}>{info.icon}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 13, fontFamily: 'var(--font-mono)' }}>
            {info.label}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{info.hint}</div>
        </div>
        {tool.description && (
          <span style={{ fontSize: 10, padding: '1px 5px', borderRadius: 3, background: 'rgba(139,92,246,0.15)', color: 'rgba(139,92,246,1)', fontWeight: 600 }}>
            ✨ IA
          </span>
        )}
        <button
          className="btn btn-icon btn-secondary"
          style={{ padding: '2px 8px', fontSize: 11 }}
          onClick={() => setShowDesc(!showDesc)}
          title="Personnaliser la description"
        >
          {showDesc ? '▲' : '▼'}
        </button>
        <button onClick={onToggle} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
          {tool.enabled
            ? <ToggleRight size={24} color="var(--accent)" />
            : <ToggleLeft size={24} color="var(--text-muted)" />}
        </button>
      </div>

      {showDesc && (
        <div style={{ marginTop: 8 }}>
          <textarea
            className="textarea"
            value={tool.description || ''}
            onChange={e => onDescriptionChange(e.target.value)}
            placeholder={defaultDescription || 'Description personnalisée (vide = par défaut)'}
            style={{ minHeight: 72, fontSize: 12, fontFamily: 'var(--font-mono)' }}
          />
          <p className="text-sm text-muted" style={{ marginTop: 4 }}>
            Laissez vide pour la description par défaut. Le badge <strong>✨ IA</strong> indique une description générée.
          </p>
        </div>
      )}
    </div>
  )
}
