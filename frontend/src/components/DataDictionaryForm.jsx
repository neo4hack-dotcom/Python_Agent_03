import React, { useEffect, useState, useMemo } from 'react'
import { Search, Play, AlertCircle, X } from 'lucide-react'
import { connectionsApi } from '../services/api'

export default function DataDictionaryForm({ agent, onSubmit, disabled }) {
  const [tables, setTables] = useState([])
  const [tableSearch, setTableSearch] = useState('')
  const [selectedTables, setSelectedTables] = useState([])
  const [allTables, setAllTables] = useState(true)  // true = all tables
  const [sampleRows, setSampleRows] = useState(5)
  const [language, setLanguage] = useState('fr')
  const [loadingTables, setLoadingTables] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!agent?.connection_id) return
    setLoadingTables(true)
    connectionsApi.listTables(agent.connection_id)
      .then(r => setTables(r.data?.tables || r.data || []))
      .catch(e => setError('Impossible de charger les tables : ' + (e.response?.data?.detail || e.message)))
      .finally(() => setLoadingTables(false))
  }, [agent?.connection_id])

  const filteredTables = useMemo(
    () => tables.filter(t => t.toLowerCase().includes(tableSearch.toLowerCase())),
    [tables, tableSearch]
  )

  const toggleTable = (t) => {
    setSelectedTables(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t])
  }
  const selectAll = () => setSelectedTables(filteredTables)
  const clearAll = () => setSelectedTables([])

  const handleSubmit = () => {
    setError('')
    const params = {
      __dd__: true,
      tables: allTables ? [] : selectedTables,
      sample_rows: sampleRows,
      language,
    }
    if (!allTables && selectedTables.length === 0) {
      setError('Sélectionnez au moins une table ou activez "Toutes les tables".')
      return
    }
    onSubmit(JSON.stringify(params))
  }

  if (!agent?.connection_id) {
    return (
      <div style={{
        padding: 14, borderRadius: 10,
        background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.3)',
        display: 'flex', gap: 10, alignItems: 'center', fontSize: 13, color: 'var(--warning)',
      }}>
        <AlertCircle size={16} />
        Cet agent Data Dictionary n'a pas de connexion configurée.
        <a href="/agents" style={{ marginLeft: 4 }}>Configurer →</a>
      </div>
    )
  }

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 12, background: 'var(--bg-card)', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{
        padding: '10px 14px', background: 'rgba(99,102,241,0.07)',
        borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: 8,
        fontSize: 13, fontWeight: 700, color: '#6366f1',
      }}>
        📖 Génération du Dictionnaire de Données
      </div>

      <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {error && (
          <div style={{
            padding: '8px 12px', borderRadius: 6, fontSize: 12,
            background: 'rgba(239,68,68,0.1)', color: '#ef4444',
            border: '1px solid rgba(239,68,68,0.3)',
            display: 'flex', gap: 6, alignItems: 'center',
          }}>
            <AlertCircle size={12} /> {error}
          </div>
        )}

        {/* All tables toggle */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
            <input
              type="checkbox"
              checked={allTables}
              onChange={e => { setAllTables(e.target.checked); if (e.target.checked) setSelectedTables([]) }}
              style={{ width: 14, height: 14, accentColor: 'var(--accent)' }}
            />
            <span style={{ fontWeight: 600 }}>Toutes les tables</span>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>({tables.length} disponibles)</span>
          </label>
          {!allTables && selectedTables.length > 0 && (
            <span style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 700 }}>
              {selectedTables.length} sélectionnée(s)
            </span>
          )}
        </div>

        {/* Table selector (shown when not "all tables") */}
        {!allTables && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Tables *
              </label>
              <div style={{ display: 'flex', gap: 6 }}>
                <button type="button" className="btn btn-secondary btn-sm" style={{ fontSize: 11 }} onClick={selectAll}>Toutes</button>
                <button type="button" className="btn btn-secondary btn-sm" style={{ fontSize: 11 }} onClick={clearAll}>Aucune</button>
              </div>
            </div>
            <div style={{ position: 'relative', marginBottom: 4 }}>
              <Search size={12} style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
              <input
                className="input"
                style={{ paddingLeft: 28, fontSize: 12 }}
                placeholder={loadingTables ? 'Chargement…' : 'Rechercher une table…'}
                value={tableSearch}
                onChange={e => setTableSearch(e.target.value)}
                disabled={loadingTables}
              />
            </div>
            <div style={{ maxHeight: 180, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 6, background: 'var(--bg-primary)' }}>
              {filteredTables.length === 0
                ? <div style={{ padding: '10px 12px', fontSize: 12, color: 'var(--text-muted)' }}>{loadingTables ? 'Chargement…' : 'Aucune table trouvée'}</div>
                : filteredTables.map(t => {
                  const checked = selectedTables.includes(t)
                  return (
                    <div
                      key={t}
                      onClick={() => toggleTable(t)}
                      style={{
                        padding: '6px 12px', fontSize: 12, cursor: 'pointer',
                        display: 'flex', alignItems: 'center', gap: 8,
                        background: checked ? 'rgba(99,102,241,0.1)' : 'transparent',
                        borderBottom: '1px solid var(--border-subtle)',
                        transition: 'background 0.1s',
                      }}
                      onMouseEnter={e => { if (!checked) e.currentTarget.style.background = 'rgba(255,255,255,0.04)' }}
                      onMouseLeave={e => { if (!checked) e.currentTarget.style.background = 'transparent' }}
                    >
                      <input type="checkbox" checked={checked} onChange={() => {}} style={{ width: 13, height: 13, accentColor: 'var(--accent)' }} />
                      <span style={{ fontFamily: 'var(--font-mono)', color: checked ? 'var(--accent)' : 'var(--text-primary)', fontWeight: checked ? 600 : 400 }}>{t}</span>
                    </div>
                  )
                })}
            </div>
          </div>
        )}

        {/* Options row */}
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          {/* Sample rows */}
          <div>
            <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: 6 }}>
              Lignes d'exemple
            </label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="range" min="1" max="20" value={sampleRows}
                onChange={e => setSampleRows(parseInt(e.target.value))}
                style={{ width: 100, accentColor: 'var(--accent)' }}
              />
              <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--accent)', minWidth: 20, textAlign: 'center' }}>{sampleRows}</span>
            </div>
            <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>lignes par table (1-20)</span>
          </div>

          {/* Language */}
          <div>
            <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: 6 }}>
              Langue
            </label>
            <div style={{ display: 'flex', gap: 6 }}>
              {['fr', 'en'].map(lang => (
                <button
                  key={lang}
                  type="button"
                  className="btn btn-sm"
                  style={{
                    fontSize: 12, padding: '4px 14px',
                    background: language === lang ? 'rgba(99,102,241,0.2)' : 'var(--bg-primary)',
                    border: `1px solid ${language === lang ? 'var(--accent)' : 'var(--border)'}`,
                    color: language === lang ? 'var(--accent)' : 'var(--text-secondary)',
                    fontWeight: language === lang ? 700 : 400,
                  }}
                  onClick={() => setLanguage(lang)}
                >
                  {lang === 'fr' ? '🇫🇷 Français' : '🇬🇧 English'}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Submit */}
        <button
          type="button"
          className="btn btn-primary"
          style={{ alignSelf: 'flex-start', display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}
          onClick={handleSubmit}
          disabled={disabled || loadingTables}
        >
          {disabled
            ? <><div className="spinner" /> Génération en cours…</>
            : <><Play size={14} /> Générer le dictionnaire</>
          }
        </button>
      </div>
    </div>
  )
}
