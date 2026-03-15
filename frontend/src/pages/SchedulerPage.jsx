import React, { useEffect, useRef, useState, useCallback } from 'react'
import {
  Plus, Play, Pause, Trash2, RefreshCw, Clock, FileSearch,
  Database, CalendarClock, ChevronDown, ChevronUp, CheckCircle,
  XCircle, Loader2, Activity, Timer, SkipForward
} from 'lucide-react'
import { schedulerApi, agentsApi, connectionsApi } from '../services/api'

// ── Helpers ───────────────────────────────────────────────────────────────────
const STATUS_COLOR = {
  active:  '#10b981',
  paused:  '#f59e0b',
}
const RUN_STATUS_COLOR = {
  running: '#6366f1',
  success: '#10b981',
  error:   '#ef4444',
  skipped: '#94a3b8',
}
const RUN_STATUS_ICON = {
  running: <Loader2 size={12} className="spin" />,
  success: <CheckCircle size={12} />,
  error:   <XCircle size={12} />,
  skipped: <SkipForward size={12} />,
}
const TRIGGER_ICON = {
  cron:          <Clock size={14} />,
  interval:      <Timer size={14} />,
  file_watch:    <FileSearch size={14} />,
  sql_condition: <Database size={14} />,
}
const TRIGGER_LABEL = {
  cron:          'Cron (heure fixe)',
  interval:      'Intervalle',
  file_watch:    'Surveiller fichier',
  sql_condition: 'Condition SQL',
}

function fmtDatetime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
}
function fmtDuration(start, end) {
  if (!start || !end) return ''
  const ms = new Date(end) - new Date(start)
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`
}
function triggerSummary(s) {
  const tc = s.trigger_config || {}
  if (s.trigger_type === 'cron') {
    const dow = tc.day_of_week && tc.day_of_week !== '*' ? ` (${tc.day_of_week})` : ''
    return `${String(tc.hour || 0).padStart(2,'0')}:${String(tc.minute || 0).padStart(2,'0')}${dow}`
  }
  if (s.trigger_type === 'interval') {
    const parts = []
    if (tc.days) parts.push(`${tc.days}j`)
    if (tc.hours) parts.push(`${tc.hours}h`)
    if (tc.minutes) parts.push(`${tc.minutes}min`)
    if (tc.seconds) parts.push(`${tc.seconds}s`)
    return `tous les ${parts.join(' ') || '5 min'}`
  }
  if (s.trigger_type === 'file_watch') return tc.directory || '—'
  if (s.trigger_type === 'sql_condition') return `résultat ${tc.operator || '>'} ${tc.threshold ?? 0}`
  return ''
}

// ── Schedule Modal ─────────────────────────────────────────────────────────────
function ScheduleModal({ schedule, agents, connections, onSave, onClose }) {
  const editing = !!schedule?.id
  const [name, setName] = useState(schedule?.name || '')
  const [description, setDescription] = useState(schedule?.description || '')
  const [agentId, setAgentId] = useState(schedule?.agent_id || '')
  const [message, setMessage] = useState(schedule?.message || '')
  const [triggerType, setTriggerType] = useState(schedule?.trigger_type || 'cron')
  const [tc, setTc] = useState(schedule?.trigger_config || {
    hour: '9', minute: '0', day_of_week: '*',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const setTcField = (k, v) => setTc(prev => ({ ...prev, [k]: v }))

  const handleTriggerTypeChange = (t) => {
    setTriggerType(t)
    const defaults = {
      cron:          { hour: '9', minute: '0', day: '*', month: '*', day_of_week: '*' },
      interval:      { days: 0, hours: 0, minutes: 30, seconds: 0 },
      file_watch:    { directory: '', pattern: '*.csv', check_interval_minutes: 5, consume: true },
      sql_condition: { connection_id: '', query: '', operator: '>', threshold: 0, check_interval_minutes: 15 },
    }
    setTc(defaults[t] || {})
  }

  const handleSubmit = async () => {
    if (!name.trim()) return setError('Le nom est requis.')
    if (!agentId) return setError('Choisissez un agent.')
    if (!message.trim()) return setError('Le message est requis.')
    setSaving(true)
    setError('')
    try {
      const payload = { name: name.trim(), description, agent_id: agentId, message, trigger_type: triggerType, trigger_config: tc }
      if (editing) {
        await schedulerApi.updateSchedule(schedule.id, payload)
      } else {
        await schedulerApi.createSchedule(payload)
      }
      onSave()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setSaving(false)
    }
  }

  const inputStyle = {
    width: '100%', padding: '7px 10px', borderRadius: 6, fontSize: 13,
    background: 'var(--bg-primary)', border: '1px solid var(--border)',
    color: 'var(--text-primary)', boxSizing: 'border-box',
  }
  const labelStyle = {
    fontSize: 11, fontWeight: 700, color: 'var(--text-muted)',
    textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 4, display: 'block',
  }
  const sectionStyle = { display: 'flex', flexDirection: 'column', gap: 4 }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={{
        background: 'var(--bg-card)', borderRadius: 12, width: '100%', maxWidth: 600,
        maxHeight: '90vh', overflowY: 'auto',
        border: '1px solid var(--border)', boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
      }}>
        {/* Header */}
        <div style={{
          padding: '14px 18px', borderBottom: '1px solid var(--border)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <CalendarClock size={18} color="#6366f1" />
            <span style={{ fontWeight: 700, fontSize: 14 }}>
              {editing ? 'Modifier la planification' : 'Nouvelle planification'}
            </span>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: 18 }}>✕</button>
        </div>

        <div style={{ padding: '18px', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {error && (
            <div style={{ padding: '8px 12px', borderRadius: 6, background: 'rgba(239,68,68,0.1)', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)', fontSize: 12 }}>
              {error}
            </div>
          )}

          {/* Name + description */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div style={sectionStyle}>
              <label style={labelStyle}>Nom *</label>
              <input style={inputStyle} value={name} onChange={e => setName(e.target.value)} placeholder="Ex: Rapport quotidien" />
            </div>
            <div style={sectionStyle}>
              <label style={labelStyle}>Description</label>
              <input style={inputStyle} value={description} onChange={e => setDescription(e.target.value)} placeholder="Optionnel" />
            </div>
          </div>

          {/* Agent */}
          <div style={sectionStyle}>
            <label style={labelStyle}>Agent *</label>
            <select style={inputStyle} value={agentId} onChange={e => setAgentId(e.target.value)}>
              <option value="">— Choisir un agent —</option>
              {agents.map(a => (
                <option key={a.id} value={a.id}>{a.name} ({a.type})</option>
              ))}
            </select>
          </div>

          {/* Message */}
          <div style={sectionStyle}>
            <label style={labelStyle}>Message / instruction *</label>
            <textarea
              style={{ ...inputStyle, minHeight: 80, resize: 'vertical', fontFamily: 'inherit' }}
              value={message} onChange={e => setMessage(e.target.value)}
              placeholder="Ex: Génère un rapport des ventes de la journée et envoie-le en PDF."
            />
          </div>

          {/* Trigger type selector */}
          <div style={sectionStyle}>
            <label style={labelStyle}>Type de déclencheur</label>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {Object.entries(TRIGGER_LABEL).map(([k, v]) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => handleTriggerTypeChange(k)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px',
                    borderRadius: 6, border: `1px solid ${triggerType === k ? '#6366f1' : 'var(--border)'}`,
                    background: triggerType === k ? 'rgba(99,102,241,0.12)' : 'var(--bg-primary)',
                    color: triggerType === k ? '#6366f1' : 'var(--text-secondary)',
                    fontWeight: triggerType === k ? 700 : 400, fontSize: 12, cursor: 'pointer',
                  }}
                >
                  {TRIGGER_ICON[k]} {v}
                </button>
              ))}
            </div>
          </div>

          {/* Trigger config */}
          <div style={{ background: 'rgba(99,102,241,0.04)', borderRadius: 8, padding: 14, border: '1px solid rgba(99,102,241,0.15)' }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#6366f1', marginBottom: 12, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Configuration du déclencheur
            </div>

            {triggerType === 'cron' && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
                <div style={sectionStyle}>
                  <label style={labelStyle}>Heure (0-23)</label>
                  <input style={inputStyle} value={tc.hour ?? '9'} onChange={e => setTcField('hour', e.target.value)} placeholder="9" />
                </div>
                <div style={sectionStyle}>
                  <label style={labelStyle}>Minute (0-59)</label>
                  <input style={inputStyle} value={tc.minute ?? '0'} onChange={e => setTcField('minute', e.target.value)} placeholder="0" />
                </div>
                <div style={sectionStyle}>
                  <label style={labelStyle}>Jour semaine (mon-sun / *)</label>
                  <input style={inputStyle} value={tc.day_of_week ?? '*'} onChange={e => setTcField('day_of_week', e.target.value)} placeholder="mon-fri" />
                </div>
                <div style={sectionStyle}>
                  <label style={labelStyle}>Jour du mois (1-31 / *)</label>
                  <input style={inputStyle} value={tc.day ?? '*'} onChange={e => setTcField('day', e.target.value)} placeholder="*" />
                </div>
                <div style={sectionStyle}>
                  <label style={labelStyle}>Mois (1-12 / *)</label>
                  <input style={inputStyle} value={tc.month ?? '*'} onChange={e => setTcField('month', e.target.value)} placeholder="*" />
                </div>
              </div>
            )}

            {triggerType === 'interval' && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
                {[['Jours', 'days'], ['Heures', 'hours'], ['Minutes', 'minutes'], ['Secondes', 'seconds']].map(([l, k]) => (
                  <div key={k} style={sectionStyle}>
                    <label style={labelStyle}>{l}</label>
                    <input
                      type="number" min="0" style={inputStyle}
                      value={tc[k] ?? 0} onChange={e => setTcField(k, parseInt(e.target.value) || 0)}
                    />
                  </div>
                ))}
              </div>
            )}

            {triggerType === 'file_watch' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10 }}>
                  <div style={sectionStyle}>
                    <label style={labelStyle}>Répertoire à surveiller</label>
                    <input style={inputStyle} value={tc.directory || ''} onChange={e => setTcField('directory', e.target.value)} placeholder="/data/inbox" />
                  </div>
                  <div style={sectionStyle}>
                    <label style={labelStyle}>Motif fichier</label>
                    <input style={inputStyle} value={tc.pattern || '*'} onChange={e => setTcField('pattern', e.target.value)} placeholder="*.csv" />
                  </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <div style={sectionStyle}>
                    <label style={labelStyle}>Intervalle de vérif. (min)</label>
                    <input type="number" min="1" style={inputStyle} value={tc.check_interval_minutes || 5} onChange={e => setTcField('check_interval_minutes', parseInt(e.target.value) || 5)} />
                  </div>
                  <div style={{ ...sectionStyle, justifyContent: 'flex-end' }}>
                    <label style={{ ...labelStyle, marginBottom: 8 }}>Consommer le fichier</label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 13 }}>
                      <input type="checkbox" checked={tc.consume !== false} onChange={e => setTcField('consume', e.target.checked)} style={{ accentColor: '#6366f1' }} />
                      Déplacer dans ./processed/
                    </label>
                  </div>
                </div>
              </div>
            )}

            {triggerType === 'sql_condition' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={sectionStyle}>
                  <label style={labelStyle}>Connexion DB</label>
                  <select style={inputStyle} value={tc.connection_id || ''} onChange={e => setTcField('connection_id', e.target.value)}>
                    <option value="">— Choisir —</option>
                    {connections.map(c => (
                      <option key={c.id} value={c.id}>{c.name} ({c.type})</option>
                    ))}
                  </select>
                </div>
                <div style={sectionStyle}>
                  <label style={labelStyle}>Requête SQL (retourne une valeur numérique)</label>
                  <textarea
                    style={{ ...inputStyle, minHeight: 70, resize: 'vertical', fontFamily: 'var(--font-mono)', fontSize: 12 }}
                    value={tc.query || ''} onChange={e => setTcField('query', e.target.value)}
                    placeholder="SELECT COUNT(*) FROM errors WHERE date >= today()"
                  />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
                  <div style={sectionStyle}>
                    <label style={labelStyle}>Opérateur</label>
                    <select style={inputStyle} value={tc.operator || '>'} onChange={e => setTcField('operator', e.target.value)}>
                      {['==', '!=', '>', '>=', '<', '<='].map(op => (
                        <option key={op} value={op}>{op}</option>
                      ))}
                    </select>
                  </div>
                  <div style={sectionStyle}>
                    <label style={labelStyle}>Seuil</label>
                    <input type="number" style={inputStyle} value={tc.threshold ?? 0} onChange={e => setTcField('threshold', parseFloat(e.target.value) || 0)} />
                  </div>
                  <div style={sectionStyle}>
                    <label style={labelStyle}>Vérif. toutes les (min)</label>
                    <input type="number" min="1" style={inputStyle} value={tc.check_interval_minutes || 15} onChange={e => setTcField('check_interval_minutes', parseInt(e.target.value) || 15)} />
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Actions */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
            <button onClick={onClose} className="btn btn-secondary" style={{ fontSize: 13 }}>Annuler</button>
            <button onClick={handleSubmit} className="btn btn-primary" disabled={saving} style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              {saving ? <><Loader2 size={13} className="spin" /> Enregistrement…</> : <>{editing ? 'Enregistrer' : 'Créer'}</>}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── RunRow ────────────────────────────────────────────────────────────────────
function RunRow({ run }) {
  const [expanded, setExpanded] = useState(false)
  const color = RUN_STATUS_COLOR[run.status] || '#94a3b8'
  return (
    <div style={{ borderBottom: '1px solid var(--border-subtle)' }}>
      <div
        onClick={() => run.output && setExpanded(v => !v)}
        style={{
          display: 'grid', gridTemplateColumns: '120px 1fr 120px 80px 80px 24px',
          gap: 8, padding: '7px 12px', alignItems: 'center',
          fontSize: 12, cursor: run.output ? 'pointer' : 'default',
          background: expanded ? 'rgba(99,102,241,0.04)' : 'transparent',
        }}
      >
        <span style={{ color, display: 'flex', alignItems: 'center', gap: 4, fontWeight: 600 }}>
          {RUN_STATUS_ICON[run.status]} {run.status}
        </span>
        <span style={{ color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {run.schedule_name}
        </span>
        <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>{fmtDatetime(run.started_at)}</span>
        <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>{fmtDuration(run.started_at, run.completed_at)}</span>
        <span style={{ color: 'var(--text-muted)', fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {run.trigger_reason}
        </span>
        <span style={{ color: 'var(--text-muted)' }}>
          {run.output && (expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />)}
        </span>
      </div>
      {expanded && run.output && (
        <div style={{
          padding: '8px 12px 12px', background: 'rgba(0,0,0,0.15)',
          fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--text-secondary)',
          whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 300, overflowY: 'auto',
          borderTop: '1px solid var(--border-subtle)',
        }}>
          {run.error ? <span style={{ color: '#ef4444' }}>{run.error}</span> : run.output}
        </div>
      )}
    </div>
  )
}

// ── ScheduleCard ──────────────────────────────────────────────────────────────
function ScheduleCard({ schedule, agents, onEdit, onToggle, onDelete, onRunNow, running }) {
  const agent = agents.find(a => a.id === schedule.agent_id)
  const statusColor = STATUS_COLOR[schedule.status] || '#94a3b8'

  return (
    <div style={{
      background: 'var(--bg-card)', borderRadius: 10,
      border: `1px solid ${schedule.status === 'active' ? 'rgba(99,102,241,0.25)' : 'var(--border)'}`,
      overflow: 'hidden',
    }}>
      {/* Top accent line */}
      <div style={{ height: 3, background: schedule.status === 'active' ? 'linear-gradient(90deg, #6366f1, #3b82f6)' : 'var(--border)' }} />

      <div style={{ padding: '12px 14px' }}>
        {/* Header row */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 10,
                padding: '2px 7px', borderRadius: 10, background: `${statusColor}20`,
                color: statusColor, fontWeight: 700,
              }}>
                <span style={{ width: 5, height: 5, borderRadius: '50%', background: statusColor, display: 'inline-block' }} />
                {schedule.status}
              </span>
            </div>
            <div style={{ fontWeight: 700, fontSize: 14, color: 'var(--text-primary)', marginBottom: 2 }}>
              {schedule.name}
            </div>
            {schedule.description && (
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>{schedule.description}</div>
            )}
          </div>

          {/* Actions */}
          <div style={{ display: 'flex', gap: 4, flexShrink: 0, marginLeft: 8 }}>
            <button
              title="Exécuter maintenant"
              onClick={() => onRunNow(schedule.id)}
              disabled={running === schedule.id}
              style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer', padding: '4px 7px', color: '#10b981' }}
            >
              {running === schedule.id ? <Loader2 size={13} className="spin" /> : <Play size={13} />}
            </button>
            <button
              title={schedule.status === 'active' ? 'Mettre en pause' : 'Activer'}
              onClick={() => onToggle(schedule)}
              style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer', padding: '4px 7px', color: schedule.status === 'active' ? '#f59e0b' : '#10b981' }}
            >
              {schedule.status === 'active' ? <Pause size={13} /> : <Play size={13} />}
            </button>
            <button title="Modifier" onClick={() => onEdit(schedule)}
              style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer', padding: '4px 7px', color: 'var(--text-muted)' }}>
              <RefreshCw size={13} />
            </button>
            <button title="Supprimer" onClick={() => onDelete(schedule.id)}
              style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer', padding: '4px 7px', color: '#ef4444' }}>
              <Trash2 size={13} />
            </button>
          </div>
        </div>

        {/* Trigger + agent info */}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 5, fontSize: 12,
            color: 'var(--text-secondary)',
          }}>
            <span style={{ color: '#6366f1' }}>{TRIGGER_ICON[schedule.trigger_type]}</span>
            <span style={{ fontWeight: 600 }}>{TRIGGER_LABEL[schedule.trigger_type]}</span>
            <span style={{ color: 'var(--text-muted)' }}>·</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>{triggerSummary(schedule)}</span>
          </div>
          {agent && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
              <span>→</span>
              <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>{agent.name}</span>
            </div>
          )}
        </div>

        {/* Stats row */}
        <div style={{ display: 'flex', gap: 16, marginTop: 10, fontSize: 11, color: 'var(--text-muted)' }}>
          <span>Exécutions : <strong style={{ color: 'var(--text-secondary)' }}>{schedule.run_count || 0}</strong></span>
          <span>Dernier : <strong style={{ color: 'var(--text-secondary)' }}>{fmtDatetime(schedule.last_run)}</strong></span>
          <span>Prochain : <strong style={{ color: schedule.status === 'active' ? '#10b981' : 'var(--text-muted)' }}>{fmtDatetime(schedule.next_run)}</strong></span>
        </div>
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function SchedulerPage() {
  const [schedules, setSchedules] = useState([])
  const [agents, setAgents] = useState([])
  const [connections, setConnections] = useState([])
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [editSchedule, setEditSchedule] = useState(null)
  const [running, setRunning] = useState(null)  // schedule id being run
  const [logTab, setLogTab] = useState('live')  // 'live' | 'history'
  const [liveRuns, setLiveRuns] = useState([])
  const liveEndRef = useRef(null)
  const sseRef = useRef(null)

  const load = useCallback(async () => {
    try {
      const [sa, aa, ca, ra] = await Promise.all([
        schedulerApi.listSchedules(),
        agentsApi.list(),
        connectionsApi.list(),
        schedulerApi.listRuns(200),
      ])
      setSchedules(sa.data || [])
      setAgents(aa.data || [])
      setConnections(ca.data || [])
      setRuns(ra.data || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [])

  // SSE live log stream
  useEffect(() => {
    load()

    const es = new EventSource('/api/scheduler/logs/stream')
    sseRef.current = es

    es.onmessage = (event) => {
      const data = JSON.parse(event.data)
      if (data.type === 'run_update') {
        const run = data.run
        setLiveRuns(prev => {
          const idx = prev.findIndex(r => r.id === run.id)
          if (idx >= 0) {
            const next = [...prev]
            next[idx] = run
            return next
          }
          return [run, ...prev].slice(0, 200)
        })
        // Refresh schedule stats when run completes
        if (run.status !== 'running') {
          load()
        }
      }
    }

    es.onerror = () => {
      // SSE disconnected — silently ignore (will reconnect on next render)
    }

    return () => es.close()
  }, [load])

  // Auto-scroll live log
  useEffect(() => {
    if (logTab === 'live' && liveEndRef.current) {
      liveEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [liveRuns, logTab])

  const handleToggle = async (s) => {
    const newStatus = s.status === 'active' ? 'paused' : 'active'
    await schedulerApi.updateSchedule(s.id, { status: newStatus })
    load()
  }

  const handleDelete = async (id) => {
    if (!confirm('Supprimer cette planification ?')) return
    await schedulerApi.deleteSchedule(id)
    load()
  }

  const handleRunNow = async (id) => {
    setRunning(id)
    try {
      const res = await schedulerApi.runNow(id)
      setLiveRuns(prev => {
        const idx = prev.findIndex(r => r.id === res.data?.id)
        if (idx >= 0) {
          const next = [...prev]; next[idx] = res.data; return next
        }
        return [res.data, ...prev].slice(0, 200)
      })
    } catch (e) {
      console.error(e)
    } finally {
      setRunning(null)
      load()
    }
  }

  const handleSave = () => {
    setShowModal(false)
    setEditSchedule(null)
    load()
  }

  const displayRuns = logTab === 'live' ? liveRuns : runs

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '60vh', color: 'var(--text-muted)', gap: 10 }}>
        <Loader2 size={20} className="spin" /> Chargement…
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Page header */}
      <div style={{
        padding: '16px 20px', borderBottom: '1px solid var(--border)',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        background: 'var(--bg-card)', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <CalendarClock size={22} color="#6366f1" />
          <div>
            <div style={{ fontWeight: 700, fontSize: 16 }}>Scheduler</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              Planification automatique d'exécutions d'agents
            </div>
          </div>
        </div>
        <button
          className="btn btn-primary"
          style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}
          onClick={() => { setEditSchedule(null); setShowModal(true) }}
        >
          <Plus size={14} /> Nouvelle planification
        </button>
      </div>

      {/* Body: schedules list (left) + log panel (right) */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 420px', flex: 1, overflow: 'hidden', minHeight: 0 }}>

        {/* Schedules column */}
        <div style={{ padding: 16, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {/* Stats bar */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 4 }}>
            {[
              { label: 'Total', value: schedules.length, color: '#6366f1' },
              { label: 'Actifs', value: schedules.filter(s => s.status === 'active').length, color: '#10b981' },
              { label: 'En pause', value: schedules.filter(s => s.status === 'paused').length, color: '#f59e0b' },
            ].map(stat => (
              <div key={stat.label} style={{
                padding: '8px 14px', borderRadius: 8, background: 'var(--bg-card)',
                border: '1px solid var(--border)', display: 'flex', gap: 8, alignItems: 'center',
              }}>
                <span style={{ fontSize: 18, fontWeight: 700, color: stat.color }}>{stat.value}</span>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{stat.label}</span>
              </div>
            ))}
          </div>

          {schedules.length === 0 ? (
            <div style={{
              textAlign: 'center', padding: '60px 20px', color: 'var(--text-muted)',
              border: '2px dashed var(--border)', borderRadius: 12,
            }}>
              <CalendarClock size={40} style={{ opacity: 0.3, marginBottom: 12 }} />
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>Aucune planification</div>
              <div style={{ fontSize: 12, marginBottom: 16 }}>Créez votre première planification pour automatiser l'exécution des agents.</div>
              <button className="btn btn-primary" onClick={() => { setEditSchedule(null); setShowModal(true) }}>
                <Plus size={13} /> Créer une planification
              </button>
            </div>
          ) : (
            schedules.map(s => (
              <ScheduleCard
                key={s.id}
                schedule={s}
                agents={agents}
                onEdit={(sch) => { setEditSchedule(sch); setShowModal(true) }}
                onToggle={handleToggle}
                onDelete={handleDelete}
                onRunNow={handleRunNow}
                running={running}
              />
            ))
          )}
        </div>

        {/* Log panel */}
        <div style={{
          borderLeft: '1px solid var(--border)', display: 'flex', flexDirection: 'column', overflow: 'hidden',
          background: 'var(--bg-primary)',
        }}>
          {/* Panel header */}
          <div style={{
            padding: '10px 14px', borderBottom: '1px solid var(--border)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            background: 'var(--bg-card)', flexShrink: 0,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 700 }}>
              <Activity size={14} color="#6366f1" /> Logs d'exécution
            </div>
            <div style={{ display: 'flex', gap: 4 }}>
              {[['live', 'Temps réel'], ['history', 'Historique']].map(([k, l]) => (
                <button key={k} onClick={() => setLogTab(k)} style={{
                  padding: '3px 10px', borderRadius: 5, fontSize: 11, cursor: 'pointer',
                  background: logTab === k ? 'rgba(99,102,241,0.2)' : 'transparent',
                  border: `1px solid ${logTab === k ? '#6366f1' : 'var(--border)'}`,
                  color: logTab === k ? '#6366f1' : 'var(--text-muted)',
                  fontWeight: logTab === k ? 700 : 400,
                }}>{l}</button>
              ))}
            </div>
          </div>

          {/* Column headers */}
          <div style={{
            display: 'grid', gridTemplateColumns: '120px 1fr 120px 80px 80px 24px',
            gap: 8, padding: '5px 12px',
            fontSize: 10, fontWeight: 700, color: 'var(--text-muted)',
            textTransform: 'uppercase', letterSpacing: '0.05em',
            borderBottom: '1px solid var(--border)', flexShrink: 0,
          }}>
            <span>Statut</span><span>Planification</span><span>Démarré</span>
            <span>Durée</span><span>Raison</span><span></span>
          </div>

          {/* Run rows */}
          <div style={{ flex: 1, overflowY: 'auto' }}>
            {displayRuns.length === 0 ? (
              <div style={{ padding: '30px 14px', textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
                {logTab === 'live'
                  ? 'En attente d\'événements…'
                  : 'Aucun historique disponible.'}
              </div>
            ) : (
              displayRuns.map(run => <RunRow key={run.id} run={run} />)
            )}
            <div ref={liveEndRef} />
          </div>
        </div>
      </div>

      {/* Modal */}
      {showModal && (
        <ScheduleModal
          schedule={editSchedule}
          agents={agents}
          connections={connections}
          onSave={handleSave}
          onClose={() => { setShowModal(false); setEditSchedule(null) }}
        />
      )}
    </div>
  )
}
