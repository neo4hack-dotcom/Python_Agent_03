import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, Plus, Pencil, Trash2, MessageSquare, Eye } from 'lucide-react'
import { agentsApi, connectionsApi } from '../services/api'
import { useToast } from '../components/Toast'
import AgentModal from '../components/AgentModal'

const TYPE_LABELS = {
  orchestrator: { label: 'Orchestrateur', icon: '🎯', css: 'agent-icon-orchestrator' },
  data_analyst: { label: 'Analyste de Données', icon: '🧠', css: 'agent-icon-data-analyst' },
  clickhouse_analyst: { label: 'Analyste ClickHouse', icon: '📊', css: 'agent-icon-analyst' },
  oracle_analyst: { label: 'Analyste Oracle', icon: '🔮', css: 'agent-icon-analyst' },
  report_writer: { label: 'Rédacteur PDF', icon: '📄', css: 'agent-icon-report' },
  custom: { label: 'Personnalisé', icon: '🤖', css: 'agent-icon-custom' },
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
    <div className="agent-card">
      <div className="agent-card-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className={`agent-icon ${typeInfo.css}`}>{typeInfo.icon}</div>
          <div>
            <div className="agent-card-name">{agent.name}</div>
            <span className="badge badge-accent" style={{ marginTop: 4 }}>{typeInfo.label}</span>
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
