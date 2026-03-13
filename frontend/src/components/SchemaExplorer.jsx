import React, { useEffect, useState } from 'react'
import { X, Database, ChevronRight, Table } from 'lucide-react'
import { connectionsApi } from '../services/api'

export default function SchemaExplorer({ connection, onClose }) {
  const [tables, setTables] = useState([])
  const [selectedTable, setSelectedTable] = useState(null)
  const [schema, setSchema] = useState(null)
  const [loading, setLoading] = useState(true)
  const [schemaLoading, setSchemaLoading] = useState(false)
  const [search, setSearch] = useState('')

  useEffect(() => {
    connectionsApi.listTables(connection.id)
      .then((r) => setTables(r.data.tables || []))
      .catch(() => setTables(['Erreur de chargement']))
      .finally(() => setLoading(false))
  }, [connection.id])

  const handleSelectTable = async (table) => {
    setSelectedTable(table)
    setSchemaLoading(true)
    try {
      const r = await connectionsApi.getSchema(connection.id, table)
      setSchema(r.data)
    } catch (e) {
      setSchema({ error: e.message })
    } finally {
      setSchemaLoading(false)
    }
  }

  const filtered = tables.filter((t) =>
    typeof t === 'string' && t.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 900, height: '80vh', display: 'flex', flexDirection: 'column' }}>
        <div className="modal-header">
          <div className="flex items-center gap-2">
            <Database size={18} />
            <h3>Explorateur de schéma — {connection.name}</h3>
          </div>
          <button className="btn btn-icon btn-secondary" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
          {/* Table list */}
          <div style={{ width: 220, borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
            <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)' }}>
              <input
                className="input"
                style={{ fontSize: 12, padding: '6px 10px' }}
                placeholder="Rechercher une table..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
              {loading ? (
                <div style={{ padding: 16, textAlign: 'center' }}><div className="spinner" /></div>
              ) : filtered.map((table) => (
                <div
                  key={table}
                  onClick={() => handleSelectTable(table)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '7px 10px', borderRadius: 6, cursor: 'pointer',
                    background: selectedTable === table ? 'rgba(99,102,241,0.15)' : 'transparent',
                    color: selectedTable === table ? 'var(--accent-light)' : 'var(--text-secondary)',
                    fontSize: 12, transition: 'all 0.1s',
                  }}
                >
                  <Table size={13} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{table}</span>
                </div>
              ))}
            </div>
            <div style={{ padding: '8px 12px', borderTop: '1px solid var(--border)', fontSize: 11, color: 'var(--text-muted)' }}>
              {filtered.length} table(s)
            </div>
          </div>

          {/* Schema detail */}
          <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
            {!selectedTable ? (
              <div style={{ textAlign: 'center', paddingTop: 60, color: 'var(--text-muted)' }}>
                <Table size={32} style={{ margin: '0 auto 12px' }} />
                <p>Sélectionnez une table pour voir son schéma</p>
              </div>
            ) : schemaLoading ? (
              <div style={{ textAlign: 'center', paddingTop: 60 }}><div className="spinner" /></div>
            ) : schema?.error ? (
              <div className="text-error">{schema.error}</div>
            ) : schema ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div>
                  <h3 style={{ marginBottom: 8 }}>{schema.table}</h3>
                  {schema.metadata && (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      {schema.metadata.engine && (
                        <span className="badge badge-accent">{schema.metadata.engine}</span>
                      )}
                      {schema.metadata.partition_key && (
                        <span className="badge badge-warning">Partition: {schema.metadata.partition_key}</span>
                      )}
                      {schema.metadata.primary_key && (
                        <span className="badge badge-success">PK: {schema.metadata.primary_key}</span>
                      )}
                    </div>
                  )}
                </div>

                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Colonne</th>
                      <th>Type</th>
                      <th>Commentaire</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(schema.columns || []).map((col) => (
                      <tr key={col.name}>
                        <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-light)' }}>{col.name}</td>
                        <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--warning)' }}>{col.type}</td>
                        <td style={{ color: 'var(--text-muted)' }}>{col.comment || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
