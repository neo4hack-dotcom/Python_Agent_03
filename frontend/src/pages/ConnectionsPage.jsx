import React, { useEffect, useState } from 'react'
import { Plug, Plus, Pencil, Trash2, CheckCircle, XCircle, Database, Table } from 'lucide-react'
import { connectionsApi } from '../services/api'
import { useToast } from '../components/Toast'
import ConnectionModal from '../components/ConnectionModal'
import SchemaExplorer from '../components/SchemaExplorer'

export default function ConnectionsPage() {
  const { show, ToastContainer } = useToast()
  const [connections, setConnections] = useState([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [editingConn, setEditingConn] = useState(null)
  const [testingId, setTestingId] = useState(null)
  const [testResults, setTestResults] = useState({})
  const [schemaConn, setSchemaConn] = useState(null)

  const load = () => {
    connectionsApi.list()
      .then((r) => setConnections(r.data))
      .catch(() => show('Erreur de chargement', 'error'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const handleTest = async (conn) => {
    setTestingId(conn.id)
    try {
      const r = await connectionsApi.test(conn.id)
      setTestResults((prev) => ({ ...prev, [conn.id]: r.data }))
      show(r.data.success ? `Connexion OK: ${r.data.version || ''}` : `Échec: ${r.data.error}`,
        r.data.success ? 'success' : 'error')
    } catch (e) {
      show('Erreur test: ' + e.message, 'error')
    } finally {
      setTestingId(null)
    }
  }

  const handleDelete = async (conn) => {
    if (!window.confirm(`Supprimer la connexion "${conn.name}" ?`)) return
    try {
      await connectionsApi.delete(conn.id)
      show('Connexion supprimée', 'success')
      load()
    } catch (e) {
      show('Erreur: ' + e.message, 'error')
    }
  }

  const handleSaved = () => {
    setShowModal(false)
    load()
    show('Connexion sauvegardée', 'success')
  }

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><div className="spinner" /></div>

  return (
    <div className="page">
      <ToastContainer />
      <div className="page-header">
        <div className="page-title">
          <Plug size={20} />
          <h2>Connexions Base de Données</h2>
          <span className="badge badge-accent">{connections.length}</span>
        </div>
        <button className="btn btn-primary" onClick={() => { setEditingConn(null); setShowModal(true) }}>
          <Plus size={15} />
          Nouvelle connexion
        </button>
      </div>

      <div className="page-body">
        {connections.length === 0 ? (
          <EmptyState onCreate={() => { setEditingConn(null); setShowModal(true) }} />
        ) : (
          <div className="conn-grid">
            {connections.map((conn) => (
              <ConnCard
                key={conn.id}
                conn={conn}
                testResult={testResults[conn.id]}
                testing={testingId === conn.id}
                onTest={() => handleTest(conn)}
                onEdit={() => { setEditingConn(conn); setShowModal(true) }}
                onDelete={() => handleDelete(conn)}
                onExplore={() => setSchemaConn(conn)}
              />
            ))}
          </div>
        )}
      </div>

      {showModal && (
        <ConnectionModal
          connection={editingConn}
          onClose={() => setShowModal(false)}
          onSaved={handleSaved}
        />
      )}

      {schemaConn && (
        <SchemaExplorer
          connection={schemaConn}
          onClose={() => setSchemaConn(null)}
        />
      )}
    </div>
  )
}

function ConnCard({ conn, testResult, testing, onTest, onEdit, onDelete, onExplore }) {
  const isClickHouse = conn.type === 'clickhouse'
  const icon = isClickHouse ? '🟡' : '🔴'

  return (
    <div className="conn-card">
      <div className="conn-card-header">
        <div className="flex items-center gap-3">
          <div className={`conn-icon ${isClickHouse ? 'conn-icon-clickhouse' : 'conn-icon-oracle'}`}>
            {icon}
          </div>
          <div>
            <div style={{ fontWeight: 600 }}>{conn.name}</div>
            <span className={`badge ${isClickHouse ? 'badge-warning' : 'badge-error'}`}>
              {conn.type.toUpperCase()}
            </span>
          </div>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-icon btn-secondary" onClick={onEdit} title="Modifier">
            <Pencil size={14} />
          </button>
          <button className="btn btn-icon btn-danger" onClick={onDelete} title="Supprimer">
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      <div className="conn-info">
        <div className="conn-detail">
          <span className="conn-detail-key">Hôte:</span>
          <span>{conn.host}:{conn.port}</span>
        </div>
        <div className="conn-detail">
          <span className="conn-detail-key">Base:</span>
          <span>{conn.database}</span>
        </div>
        <div className="conn-detail">
          <span className="conn-detail-key">User:</span>
          <span>{conn.username}</span>
        </div>
        {conn.description && (
          <div className="conn-detail">
            <span className="conn-detail-key">Note:</span>
            <span>{conn.description}</span>
          </div>
        )}
      </div>

      {testResult && (
        <div className="flex items-center gap-2 text-sm" style={{
          padding: '6px 10px',
          borderRadius: 6,
          background: testResult.success ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
        }}>
          {testResult.success
            ? <CheckCircle size={14} color="var(--success)" />
            : <XCircle size={14} color="var(--error)" />}
          <span style={{ color: testResult.success ? 'var(--success)' : 'var(--error)' }}>
            {testResult.success ? testResult.version || 'Connecté' : testResult.error}
          </span>
        </div>
      )}

      <div className="flex gap-2">
        <button
          className={`btn btn-sm ${testResult?.success ? 'btn-success' : 'btn-secondary'}`}
          style={{ flex: 1 }}
          onClick={onTest}
          disabled={testing}
        >
          {testing ? <div className="spinner" style={{ width: 12, height: 12 }} /> : null}
          Tester
        </button>
        <button className="btn btn-secondary btn-sm" onClick={onExplore} title="Explorer le schéma">
          <Table size={13} />
          Schéma
        </button>
      </div>
    </div>
  )
}

function EmptyState({ onCreate }) {
  return (
    <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text-muted)' }}>
      <div style={{ fontSize: 48, marginBottom: 16 }}>🔌</div>
      <h3 style={{ color: 'var(--text-secondary)', marginBottom: 8 }}>Aucune connexion configurée</h3>
      <p style={{ marginBottom: 24 }}>Ajoutez une connexion ClickHouse ou Oracle.</p>
      <button className="btn btn-primary" onClick={onCreate}>
        <Plus size={15} />
        Nouvelle connexion
      </button>
    </div>
  )
}
