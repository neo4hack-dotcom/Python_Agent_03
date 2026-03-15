import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, Plus, Pencil, Trash2, MessageSquare, Eye } from 'lucide-react'
import { agentsApi, connectionsApi } from '../services/api'
import { useToast } from '../components/Toast'
import AgentModal from '../components/AgentModal'

const TYPE_LABELS = {
  orchestrator:       { label: 'Orchestrateur',       icon: '🌐', css: 'agent-icon-orchestrator', color: '#6366f1' },
  data_analyst:       { label: 'Analyste de Données', icon: '🧠', css: 'agent-icon-data-analyst',  color: '#10b981' },
  clickhouse_analyst: { label: 'Analyste ClickHouse', icon: '📊', css: 'agent-icon-clickhouse',    color: '#f59e0b' },
  oracle_analyst:     { label: 'Analyste Oracle',     icon: '🔮', css: 'agent-icon-oracle',         color: '#8b5cf6' },
  report_writer:      { label: 'Rédacteur PDF',       icon: '📄', css: 'agent-icon-report',         color: '#2563eb' },
  file_manager:       { label: 'Gest. Fichiers',      icon: '🗂️', css: 'agent-icon-file',           color: '#0891b2' },
  powerbi_analyst:    { label: 'Analyste Power BI',   icon: '📈', css: 'agent-icon-powerbi',        color: '#f97316' },
  custom:             { label: 'Personnalisé',         icon: '🤖', css: 'agent-icon-custom',         color: '#64748b' },
}

export default function AgentsPage() {
  const { show, ToastContainer } = useToast()
  const navigate = useNavigate()
  const [agents, setAgents] = useState([])
  const [connections, setConnections] = useState([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [editingAgent, setEditingAgent] = useState(null)

  const load = () => {
    Promise.all([agentsApi.list(), connectionsApi.list()])
      .then(([ar, cr]) => {
        setAgents(ar.data)
        setConnections(cr.data)
      })
      .catch(() => show('Erreur de chargement', 'error'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const handleCreate = () => { setEditingAgent(null); setShowModal(true) }
  const handleEdit = (agent) => { setEditingAgent(agent); setShowModal(true) }

  const handleDelete = async (agent) => {
    if (!window.confirm(`Supprimer l'agent "${agent.name}" ?`)) return
    try {
      await agentsApi.delete(agent.id)
      show('Agent supprimé', 'success')
      load()
    } catch (e) {
      show('Erreur suppression: ' + e.message, 'error')
    }
  }

  const handleSaved = () => {
    setShowModal(false)
    load()
    show('Agent sauvegardé', 'success')
  }

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><div className="spinner" /></div>

  return (
    <div className="page">
      <ToastContainer />
      <div className="page-header">
        <div className="page-title">
          <Bot size={20} />
          <h2>Gestion des Agents</h2>
          <span className="badge badge-accent">{agents.length}</span>
        </div>
        <button className="btn btn-primary" onClick={handleCreate}>
          <Plus size={15} />
          Créer un agent
        </button>
      </div>

      <div className="page-body">
        {agents.length === 0 ? (
          <EmptyState onCreate={handleCreate} />
        ) : (
          <div className="agent-grid">
            {agents.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                connections={connections}
                onEdit={() => handleEdit(agent)}
                onDelete={() => handleDelete(agent)}
                onChat={() => navigate(`/chat/${agent.id}`)}
              />
            ))}
          </div>
        )}
      </div>

      {showModal && (
        <AgentModal
          agent={editingAgent}
          connections={connections}
          onClose={() => setShowModal(false)}
          onSaved={handleSaved}
        />
      )}
    </div>
  )
}

function AgentCard({ agent, connections, onEdit, onDelete, onChat }) {
  const typeInfo = TYPE_LABELS[agent.type] || TYPE_LABELS.custom
  const conn = connections.find((c) => c.id === agent.connection_id)

  return (
    <div className="agent-card" style={{ borderLeft: `3px solid ${typeInfo.color}` }}>
      <div className="agent-card-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className={`agent-icon ${typeInfo.css}`}>{typeInfo.icon}</div>
          <div>
            <div className="agent-card-name" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {agent.name}
              {agent.type === 'orchestrator' && (
                <span style={{
                  fontSize: 9, fontWeight: 800, letterSpacing: '0.1em',
                  background: typeInfo.color + '1a',
                  border: `1px solid ${typeInfo.color}55`,
                  borderRadius: 3, padding: '1px 5px', color: typeInfo.color,
                  textTransform: 'uppercase',
                }}>MANAGER</span>
              )}
            </div>
            <span className="badge badge-accent" style={{ marginTop: 4, color: typeInfo.color, background: typeInfo.color + '15', borderColor: typeInfo.color + '40' }}>{typeInfo.label}</span>
          </div>
        </div>
        <div className="agent-card-actions">
          <button className="btn btn-icon btn-secondary" onClick={onEdit} title="Modifier">
            <Pencil size={14} />
          </button>
          <button className="btn btn-icon btn-danger" onClick={onDelete} title="Supprimer">
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {agent.description && (
        <p className="agent-card-desc">{agent.description}</p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {conn && (
          <div className="flex items-center gap-2 text-sm text-secondary">
            <span>🗄️</span>
            <span>{conn.name} ({conn.type})</span>
          </div>
        )}
        <div className="flex gap-2 text-sm text-muted">
          <span>Retries: {agent.max_retries}</span>
          <span>·</span>
          <span>Limit: {agent.row_limit} lignes</span>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
        <button className="btn btn-primary btn-sm" style={{ flex: 1 }} onClick={onChat}>
          <MessageSquare size={13} />
          Discuter
        </button>
        <button className="btn btn-secondary btn-sm" onClick={onEdit}>
          <Eye size={13} />
          Config
        </button>
      </div>
    </div>
  )
}

function EmptyState({ onCreate }) {
  return (
    <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text-muted)' }}>
      <div style={{ fontSize: 48, marginBottom: 16 }}>🤖</div>
      <h3 style={{ color: 'var(--text-secondary)', marginBottom: 8 }}>Aucun agent configuré</h3>
      <p style={{ marginBottom: 24 }}>Créez votre premier agent pour commencer.</p>
      <button className="btn btn-primary" onClick={onCreate}>
        <Plus size={15} />
        Créer un agent
      </button>
    </div>
  )
}
