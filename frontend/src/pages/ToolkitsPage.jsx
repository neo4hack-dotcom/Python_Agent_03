import React, { useEffect, useState } from 'react'
import { Wrench, Plus, Pencil, Trash2, Shield } from 'lucide-react'
import { toolkitsApi } from '../services/api'
import { useToast } from '../components/Toast'
import ToolkitModal from '../components/ToolkitModal'

const DB_TYPE_LABELS = {
  clickhouse: { label: 'ClickHouse', icon: '📊', color: 'var(--warning)' },
  oracle: { label: 'Oracle', icon: '🔮', color: 'var(--accent)' },
  any: { label: 'Universel', icon: '🌐', color: 'var(--text-secondary)' },
}

export default function ToolkitsPage() {
  const { show, ToastContainer } = useToast()
  const [toolkits, setToolkits] = useState([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [editingToolkit, setEditingToolkit] = useState(null)

  const load = () => {
    toolkitsApi.list()
      .then(r => setToolkits(r.data))
      .catch(() => show('Erreur de chargement', 'error'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const handleCreate = () => { setEditingToolkit(null); setShowModal(true) }
  const handleEdit = (tk) => { setEditingToolkit(tk); setShowModal(true) }

  const handleDelete = async (tk) => {
    if (tk.is_default) {
      show('Les toolkits système ne peuvent pas être supprimés', 'error')
      return
    }
    if (!window.confirm(`Supprimer le toolkit "${tk.name}" ?`)) return
    try {
      await toolkitsApi.delete(tk.id)
      show('Toolkit supprimé', 'success')
      load()
    } catch (e) {
      show(e.response?.data?.detail || 'Erreur suppression', 'error')
    }
  }

  const handleSaved = () => {
    setShowModal(false)
    load()
    show('Toolkit sauvegardé', 'success')
  }

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><div className="spinner" /></div>

  return (
    <div className="page">
      <ToastContainer />
      <div className="page-header">
        <div className="page-title">
          <Wrench size={20} />
          <h2>Gestion des Toolkits</h2>
          <span className="badge badge-accent">{toolkits.length}</span>
        </div>
        <button className="btn btn-primary" onClick={handleCreate}>
          <Plus size={15} />
          Créer un toolkit
        </button>
      </div>

      <div className="page-body">
        <div className="card" style={{ marginBottom: 16, background: 'rgba(99,102,241,0.05)', borderColor: 'rgba(99,102,241,0.3)' }}>
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            <strong>💡 Toolkits :</strong> Un toolkit définit quels outils SQL sont disponibles pour vos agents
            (lister les tables, voir les schémas, exécuter des requêtes, valider la syntaxe).
            Assignez un toolkit à un agent dans ses paramètres avancés pour contrôler
            ses capacités et personnaliser les descriptions des outils.
          </p>
        </div>

        {toolkits.length === 0 ? (
          <EmptyState onCreate={handleCreate} />
        ) : (
          <div className="agent-grid">
            {toolkits.map(tk => (
              <ToolkitCard
                key={tk.id}
                toolkit={tk}
                onEdit={() => handleEdit(tk)}
                onDelete={() => handleDelete(tk)}
              />
            ))}
          </div>
        )}
      </div>

      {showModal && (
        <ToolkitModal
          toolkit={editingToolkit}
          onClose={() => setShowModal(false)}
          onSaved={handleSaved}
        />
      )}
    </div>
  )
}

function ToolkitCard({ toolkit, onEdit, onDelete }) {
  const dbInfo = DB_TYPE_LABELS[toolkit.db_type] || DB_TYPE_LABELS.any
  const enabledTools = (toolkit.tools || []).filter(t => t.enabled)
  const disabledTools = (toolkit.tools || []).filter(t => !t.enabled)

  const toolIcons = {
    list_tables: '📋',
    get_schema: '🔍',
    execute_query: '⚡',
    check_query: '✅',
  }

  return (
    <div className="agent-card">
      <div className="agent-card-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className="agent-icon agent-icon-analyst" style={{ fontSize: 20 }}>
            {dbInfo.icon}
          </div>
          <div>
            <div className="agent-card-name" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {toolkit.name}
              {toolkit.is_default && (
                <Shield size={12} title="Toolkit système" style={{ color: 'var(--text-muted)' }} />
              )}
            </div>
            <span
              className="badge badge-accent"
              style={{ marginTop: 4, background: `${dbInfo.color}22`, color: dbInfo.color, borderColor: dbInfo.color }}
            >
              {dbInfo.label}
            </span>
          </div>
        </div>
        <div className="agent-card-actions">
          <button className="btn btn-icon btn-secondary" onClick={onEdit} title="Modifier">
            <Pencil size={14} />
          </button>
          <button
            className="btn btn-icon btn-danger"
            onClick={onDelete}
            title={toolkit.is_default ? 'Toolkit système (non supprimable)' : 'Supprimer'}
            disabled={toolkit.is_default}
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {toolkit.description && (
        <p className="agent-card-desc">{toolkit.description}</p>
      )}

      <div style={{ marginTop: 8 }}>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Outils actifs ({enabledTools.length}/{toolkit.tools?.length || 0})
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {(toolkit.tools || []).map(t => (
            <span
              key={t.name}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                padding: '2px 8px',
                borderRadius: 4,
                fontSize: 11,
                fontFamily: 'var(--font-mono)',
                background: t.enabled ? 'rgba(99,102,241,0.15)' : 'var(--bg-secondary)',
                color: t.enabled ? 'var(--accent)' : 'var(--text-muted)',
                border: `1px solid ${t.enabled ? 'rgba(99,102,241,0.3)' : 'var(--border)'}`,
                textDecoration: t.enabled ? 'none' : 'line-through',
              }}
            >
              {toolIcons[t.name] || '🔧'} {t.name}
              {t.description && <span title="Description personnalisée">✏️</span>}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

function EmptyState({ onCreate }) {
  return (
    <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text-muted)' }}>
      <div style={{ fontSize: 48, marginBottom: 16 }}>🔧</div>
      <h3 style={{ color: 'var(--text-secondary)', marginBottom: 8 }}>Aucun toolkit configuré</h3>
      <p style={{ marginBottom: 24 }}>Créez un toolkit pour personnaliser les outils de vos agents.</p>
      <button className="btn btn-primary" onClick={onCreate}>
        <Plus size={15} />
        Créer un toolkit
      </button>
    </div>
  )
}
