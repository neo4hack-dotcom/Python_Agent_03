import React, { useEffect, useState, useMemo } from 'react'
import { Search, Play, AlertCircle, ChevronDown, ChevronUp, X } from 'lucide-react'
import { connectionsApi, agentsApi } from '../services/api'

const SAMPLE_OPTIONS = [
  { value: 10000,   label: '10 000 lignes' },
  { value: 50000,   label: '50 000 lignes' },
  { value: 100000,  label: '100 000 lignes' },
  { value: 500000,  label: '500 000 lignes' },
  { value: 0,       label: 'Full scan' },
]

const FILTER_OPERATORS = ['=', '!=', '<', '>', '<=', '>=', 'LIKE', 'BETWEEN']

// ── FilterBuilder ─────────────────────────────────────────────────────────────
function FilterBuilder({ value, onChange }) {
  const [raw, setRaw] = useState(value || '')
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {FILTER_OPERATORS.map(op => (
          <button
            key={op}
            type="button"
            className="btn btn-secondary btn-sm"
            style={{ fontSize: 11, padding: '2px 8px', fontFamily: 'var(--font-mono)' }}
            onClick={() => {
              const next = raw ? `${raw} ${op} ` : `${op} `
              setRaw(next)
              onChange(next)
            }}
          >
            {op}
          </button>
        ))}
        {raw && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            style={{ fontSize: 11, padding: '2px 8px', color: 'var(--danger)' }}
            onClick={() => { setRaw(''); onChange('') }}
          >
            <X size={10} /> effacer
          </button>
        )}
      </div>
      <input
        className="input"
        style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}
        placeholder="ex: region = 'FR'  ou  montant > 0  ou  statut LIKE 'OPEN%'"
        value={raw}
        onChange={e => { setRaw(e.target.value); onChange(e.target.value) }}
      />
    </div>
  )
}

// ── Main DataQualityForm ──────────────────────────────────────────────────────
export default function DataQualityForm({ agent, onSubmit, disabled }) {
  const [tables, setTables] = useState([])
  const [tableSearch, setTableSearch] = useState('')
  const [selectedTable, setSelectedTable] = useState('')
  const [columns, setColumns] = useState([])      // [{name, type, comment}]
  const [colSearch, setColSearch] = useState('')
  const [selectedCols, setSelectedCols] = useState([])
  const [sampleSize, setSampleSize] = useState(50000)
  const [rowFilter, setRowFilter] = useState('')
  const [timeColumn, setTimeColumn] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [loadingTables, setLoadingTables] = useState(false)
  const [loadingCols, setLoadingCols] = useState(false)
  const [error, setError] = useState('')

  // Load tables on mount
  useEffect(() => {
    if (!agent?.connection_id) return
    setLoadingTables(true)
    connectionsApi.listTables(agent.connection_id)
      .then(r => setTables(r.data?.tables || r.data || []))
      .catch(e => setError('Impossible de charger les tables: ' + (e.response?.data?.detail || e.message)))
      .finally(() => setLoadingTables(false))
  }, [agent?.connection_id])

  // Load columns when table changes
  useEffect(() => {
    if (!selectedTable || !agent?.connection_id) { setColumns([]); return }
    setLoadingCols(true)
    setSelectedCols([])
    setTimeColumn('')
    connectionsApi.getSchema(agent.connection_id, selectedTable)
      .then(r => setColumns(r.data?.columns || []))
      .catch(e => setError('Impossible de charger le schéma: ' + (e.response?.data?.detail || e.message)))
      .finally(() => setLoadingCols(false))
  }, [selectedTable, agent?.connection_id])

  const filteredTables = useMemo(() =>
    tables.filter(t => t.toLowerCase().includes(tableSearch.toLowerCase())),
    [tables, tableSearch]
  )

  const filteredCols = useMemo(() =>
    columns.filter(c => c.name.toLowerCase().includes(colSearch.toLowerCase())),
    [columns, colSearch]
  )

  const toggleCol = (name) => {
    setSelectedCols(prev =>
      prev.includes(name) ? prev.filter(c => c !== name) : [...prev, name]
    )
  }

  const selectAll = () => setSelectedCols(filteredCols.map(c => c.name))
  const clearAll = () => setSelectedCols([])

  const handleSubmit = () => {
    if (!selectedTable) { setError('Sélectionnez une table'); return }
    if (selectedCols.length === 0) { setError('Sélectionnez au moins une colonne'); return }
    setError('')
    const params = {
      __dq__: true,
      table: selectedTable,
      columns: selectedCols,
      sample_size: sampleSize,
      row_filter: rowFilter.trim() || null,
      time_column: timeColumn || null,
    }
    onSubmit(JSON.stringify(params))
  }

  if (!agent?.connection_id) {
    return (
      <div style={{
        padding: 16, borderRadius: 10,
        background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.3)',
        display: 'flex', gap: 10, alignItems: 'center', fontSize: 13, color: 'var(--warning)',
      }}>
        <AlertCircle size={16} />
        Cet agent Data Quality n'a pas de connexion configurée.
        <a href="/agents" style={{ marginLeft: 4 }}>Configurer →</a>
      </div>
    )
  }

  return (
    <div style={{
      border: '1px solid var(--border)',
      borderRadius: 12,
      background: 'var(--bg-card)',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        padding: '10px 14px',
        background: 'rgba(16,185,129,0.07)',
        borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: 8,
        fontSize: 13, fontWeight: 700, color: '#10b981',
      }}>
        🔍 Analyse Data Quality
      </div>

      <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {error && (
          <div style={{
            padding: '8px 12px', borderRadius: 6, fontSize: 12,
            background: 'rgba(239,68,68,0.1)', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)',
            display: 'flex', gap: 6, alignItems: 'center',
          }}>
            <AlertCircle size={12} /> {error}
          </div>
        )}

        {/* Table selector */}
        <div>
          <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: 6 }}>
            Table *
          </label>
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
          <div style={{
            maxHeight: 140, overflowY: 'auto',
            border: '1px solid var(--border)', borderRadius: 6,
            background: 'var(--bg-primary)',
          }}>
            {filteredTables.length === 0
              ? <div style={{ padding: '10px 12px', fontSize: 12, color: 'var(--text-muted)' }}>{loadingTables ? 'Chargement…' : 'Aucune table trouvée'}</div>
              : filteredTables.map(t => (
                <div
                  key={t}
                  onClick={() => { setSelectedTable(t); setTableSearch('') }}
                  style={{
                    padding: '7px 12px', fontSize: 12, cursor: 'pointer',
                    fontFamily: 'var(--font-mono)',
                    background: selectedTable === t ? 'rgba(16,185,129,0.12)' : 'transparent',
                    color: selectedTable === t ? '#10b981' : 'var(--text-primary)',
                    borderBottom: '1px solid var(--border-subtle)',
                    transition: 'background 0.1s',
                  }}
                  onMouseEnter={e => { if (selectedTable !== t) e.currentTarget.style.background = 'rgba(255,255,255,0.04)' }}
                  onMouseLeave={e => { if (selectedTable !== t) e.currentTarget.style.background = 'transparent' }}
                >
                  {t}
                </div>
              ))}
          </div>
        </div>

        {/* Column selector */}
        {selectedTable && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Colonnes * <span style={{ fontWeight: 400, color: 'var(--accent)', marginLeft: 4 }}>{selectedCols.length} sélectionnée(s)</span>
              </label>
              <div style={{ display: 'flex', gap: 6 }}>
                <button type="button" className="btn btn-secondary btn-sm" style={{ fontSize: 11 }} onClick={selectAll}>Tout</button>
                <button type="button" className="btn btn-secondary btn-sm" style={{ fontSize: 11 }} onClick={clearAll}>Aucune</button>
              </div>
            </div>
            <div style={{ position: 'relative', marginBottom: 4 }}>
              <Search size={12} style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
              <input
                className="input"
                style={{ paddingLeft: 28, fontSize: 12 }}
                placeholder={loadingCols ? 'Chargement…' : 'Rechercher une colonne…'}
                value={colSearch}
                onChange={e => setColSearch(e.target.value)}
                disabled={loadingCols}
              />
            </div>
            <div style={{
              maxHeight: 180, overflowY: 'auto',
              border: '1px solid var(--border)', borderRadius: 6,
              background: 'var(--bg-primary)',
            }}>
              {filteredCols.length === 0
                ? <div style={{ padding: '10px 12px', fontSize: 12, color: 'var(--text-muted)' }}>{loadingCols ? 'Chargement…' : 'Aucune colonne'}</div>
                : filteredCols.map(c => {
                  const checked = selectedCols.includes(c.name)
                  return (
                    <div
                      key={c.name}
                      onClick={() => toggleCol(c.name)}
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
                      <span style={{ fontFamily: 'var(--font-mono)', color: checked ? 'var(--accent)' : 'var(--text-primary)', fontWeight: checked ? 600 : 400 }}>{c.name}</span>
                      <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 'auto', fontFamily: 'var(--font-mono)' }}>{c.type}</span>
                    </div>
                  )
                })}
            </div>
          </div>
        )}

        {/* Sample size */}
        <div>
          <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: 6 }}>
            Taille échantillon
          </label>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {SAMPLE_OPTIONS.map(o => (
              <button
                key={o.value}
                type="button"
                className="btn btn-sm"
                style={{
                  fontSize: 11,
                  background: sampleSize === o.value ? 'rgba(99,102,241,0.2)' : 'var(--bg-primary)',
                  border: `1px solid ${sampleSize === o.value ? 'var(--accent)' : 'var(--border)'}`,
                  color: sampleSize === o.value ? 'var(--accent)' : 'var(--text-secondary)',
                  fontWeight: sampleSize === o.value ? 700 : 400,
                }}
                onClick={() => setSampleSize(o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>

        {/* Advanced options (collapsible) */}
        <div>
          <button
            type="button"
            style={{ background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text-muted)', padding: 0 }}
            onClick={() => setShowAdvanced(v => !v)}
          >
            {showAdvanced ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            Options avancées (filtre, colonne temps)
          </button>
          {showAdvanced && (
            <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: 6 }}>
                  Filtre de lignes <span style={{ fontWeight: 400, textTransform: 'none' }}>(optionnel)</span>
                </label>
                <FilterBuilder value={rowFilter} onChange={setRowFilter} />
              </div>
              {columns.length > 0 && (
                <div>
                  <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: 6 }}>
                    Colonne temps <span style={{ fontWeight: 400, textTransform: 'none' }}>(analyse volumétrique)</span>
                  </label>
                  <select
                    className="select"
                    style={{ fontSize: 12 }}
                    value={timeColumn}
                    onChange={e => setTimeColumn(e.target.value)}
                  >
                    <option value="">-- Aucune --</option>
                    {columns
                      .filter(c => /date|time|ts|timestamp/i.test(c.type) || /date|time|ts/i.test(c.name))
                      .map(c => <option key={c.name} value={c.name}>{c.name} ({c.type})</option>)
                    }
                    <optgroup label="Toutes les colonnes">
                      {columns.map(c => <option key={`all_${c.name}`} value={c.name}>{c.name} ({c.type})</option>)}
                    </optgroup>
                  </select>
                  <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                    Active l'analyse de la distribution temporelle et détecte les anomalies de volume par période.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Submit */}
        <button
          type="button"
          className="btn btn-primary"
          style={{ alignSelf: 'flex-start', display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}
          onClick={handleSubmit}
          disabled={disabled || !selectedTable || selectedCols.length === 0}
        >
          {disabled
            ? <><div className="spinner" /> Analyse en cours…</>
            : <><Play size={14} /> Lancer l'analyse</>
          }
        </button>
      </div>
    </div>
  )
}
