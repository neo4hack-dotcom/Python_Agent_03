/**
 * AgentLogPanel — Fenêtre de logs en temps réel
 *
 * Affiche toutes les étapes de traitement de l'agent en direct :
 *   - Tokens LLM streamés
 *   - SQL généré (bloc coloré)
 *   - Résultats de requêtes (ligne avec # lignes)
 *   - Appels d'outils (tool_calls dans le cycle ReAct)
 *   - PDF prêt
 *   - Erreurs
 *
 * Usage :
 *   <AgentLogPanel logs={logs} onClose={() => setShowLogs(false)} />
 *
 * Props :
 *   logs     : Array<LogEntry>  — log entries accumulés depuis le dernier envoi
 *   onClose  : () => void       — ferme le panneau
 *   agentName: string           — nom de l'agent (affiché dans le titre)
 */
import React, { useEffect, useRef, useState } from 'react'
import { X, Terminal, ChevronDown, ChevronUp, Download } from 'lucide-react'

const LOG_COLORS = {
  token:        { bg: 'rgba(99,102,241,0.08)',  border: 'rgba(99,102,241,0.25)', label: '💬 LLM',       labelColor: '#818cf8' },
  sql:          { bg: 'rgba(245,158,11,0.08)',  border: 'rgba(245,158,11,0.3)',  label: '🔷 SQL',        labelColor: '#f59e0b' },
  query_result: { bg: 'rgba(16,185,129,0.08)',  border: 'rgba(16,185,129,0.3)',  label: '✅ Résultat',   labelColor: '#10b981' },
  tool_call:    { bg: 'rgba(139,92,246,0.08)',  border: 'rgba(139,92,246,0.25)', label: '🔧 Outil',      labelColor: '#a78bfa' },
  tool_result:  { bg: 'rgba(59,130,246,0.08)',  border: 'rgba(59,130,246,0.2)',  label: '📥 Retour',    labelColor: '#60a5fa' },
  final:        { bg: 'rgba(16,185,129,0.1)',   border: 'rgba(16,185,129,0.4)',  label: '🎯 Réponse',   labelColor: '#34d399' },
  pdf_ready:    { bg: 'rgba(26,58,108,0.1)',    border: 'rgba(37,99,235,0.4)',   label: '📄 PDF prêt',  labelColor: '#2563eb' },
  error:        { bg: 'rgba(239,68,68,0.08)',   border: 'rgba(239,68,68,0.3)',   label: '❌ Erreur',     labelColor: '#f87171' },
  info:         { bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.2)', label: 'ℹ️ Info',       labelColor: '#94a3b8' },
}

function formatContent(type, entry) {
  if (type === 'token' || type === 'final') {
    const text = entry.content || ''
    return text.length > 280 ? text.slice(0, 280) + '…' : text
  }
  if (type === 'sql') {
    return entry.content || ''
  }
  if (type === 'query_result') {
    const cols = (entry.columns || []).join(', ')
    return `${entry.row_count ?? '?'} ligne(s) — colonnes : ${cols || '—'}`
  }
  if (type === 'tool_call') {
    return `${entry.tool_name}(${JSON.stringify(entry.args || {}).slice(0, 120)})`
  }
  if (type === 'tool_result') {
    const s = JSON.stringify(entry.result || entry.content || '')
    return s.length > 200 ? s.slice(0, 200) + '…' : s
  }
  if (type === 'pdf_ready') {
    return `Rapport disponible : ${entry.filename || entry.download_url || ''}`
  }
  if (type === 'error') {
    return entry.content || ''
  }
  return JSON.stringify(entry).slice(0, 200)
}

function LogEntry({ entry, index }) {
  const style = LOG_COLORS[entry.type] || LOG_COLORS.info
  const [expanded, setExpanded] = useState(false)
  const isExpandable = entry.type === 'token' || entry.type === 'final' || entry.type === 'sql'
    || entry.type === 'tool_result'
  const preview = formatContent(entry.type, entry)
  const fullContent = entry.type === 'sql' ? entry.content
    : entry.type === 'token' || entry.type === 'final' ? entry.content
    : entry.type === 'tool_result' ? JSON.stringify(entry.result || entry.content, null, 2)
    : preview

  const canExpand = isExpandable && fullContent && fullContent.length > 280

  return (
    <div style={{
      border: `1px solid ${style.border}`,
      borderRadius: 6,
      background: style.bg,
      marginBottom: 6,
      overflow: 'hidden',
      fontSize: 11,
      fontFamily: 'var(--font-mono)',
    }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '5px 10px',
          cursor: canExpand ? 'pointer' : 'default',
        }}
        onClick={() => canExpand && setExpanded(!expanded)}
      >
        <span style={{
          fontWeight: 700,
          color: style.labelColor,
          minWidth: 80,
          fontSize: 10,
          letterSpacing: '0.04em',
        }}>
          {style.label}
        </span>
        <span style={{
          flex: 1,
          color: 'var(--text-secondary)',
          whiteSpace: entry.type === 'sql' ? 'pre' : 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}>
          {expanded ? null : preview}
        </span>
        <span style={{ color: 'var(--text-muted)', fontSize: 9, flexShrink: 0 }}>
          #{index + 1}
        </span>
        {canExpand && (
          <span style={{ color: style.labelColor, flexShrink: 0 }}>
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </span>
        )}
      </div>

      {expanded && (
        <div style={{
          borderTop: `1px solid ${style.border}`,
          padding: '8px 10px',
          background: 'rgba(0,0,0,0.15)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          color: entry.type === 'sql' ? '#93c5fd' : 'var(--text-secondary)',
          maxHeight: 300,
          overflowY: 'auto',
          fontSize: 11,
        }}>
          {fullContent}
        </div>
      )}
    </div>
  )
}

export default function AgentLogPanel({ logs, onClose, agentName }) {
  const bottomRef = useRef(null)
  const [autoScroll, setAutoScroll] = useState(true)

  useEffect(() => {
    if (autoScroll) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, autoScroll])

  const exportLogs = () => {
    const text = logs.map((e, i) =>
      `[${e.type}] #${i + 1} ${formatContent(e.type, e)}`
    ).join('\n')
    const blob = new Blob([text], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `agent_logs_${Date.now()}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  const counts = logs.reduce((acc, e) => {
    acc[e.type] = (acc[e.type] || 0) + 1
    return acc
  }, {})

  return (
    <div style={{
      position: 'fixed',
      right: 20,
      bottom: 20,
      width: 520,
      maxHeight: 480,
      background: 'var(--bg-primary)',
      border: '1px solid var(--border)',
      borderRadius: 12,
      boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
      display: 'flex',
      flexDirection: 'column',
      zIndex: 1000,
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        padding: '10px 14px',
        background: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        flexShrink: 0,
      }}>
        <Terminal size={14} style={{ color: 'var(--accent)' }} />
        <span style={{ fontWeight: 700, fontSize: 12, flex: 1 }}>
          Logs — {agentName || 'Agent'}
        </span>
        {/* Type summary badges */}
        {Object.entries(counts).slice(0, 4).map(([type, count]) => {
          const s = LOG_COLORS[type] || LOG_COLORS.info
          return (
            <span key={type} style={{
              fontSize: 9,
              padding: '1px 6px',
              borderRadius: 10,
              background: s.bg,
              color: s.labelColor,
              border: `1px solid ${s.border}`,
              fontFamily: 'var(--font-mono)',
            }}>
              {type} ×{count}
            </span>
          )
        })}
        <button
          className="btn btn-icon btn-secondary"
          style={{ padding: 4 }}
          onClick={exportLogs}
          title="Exporter les logs"
        >
          <Download size={12} />
        </button>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, color: 'var(--text-muted)', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={e => setAutoScroll(e.target.checked)}
            style={{ width: 12, height: 12 }}
          />
          Auto
        </label>
        <button className="btn btn-icon btn-secondary" style={{ padding: 4 }} onClick={onClose}>
          <X size={14} />
        </button>
      </div>

      {/* Log entries */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '10px 12px',
        minHeight: 0,
      }}>
        {logs.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '30px 0', color: 'var(--text-muted)', fontSize: 12 }}>
            <Terminal size={24} style={{ marginBottom: 8, opacity: 0.4 }} />
            <div>En attente des logs de l'agent…</div>
          </div>
        ) : (
          logs.map((entry, i) => <LogEntry key={i} entry={entry} index={i} />)
        )}
        <div ref={bottomRef} />
      </div>

      {/* Footer */}
      <div style={{
        padding: '6px 12px',
        borderTop: '1px solid var(--border)',
        background: 'var(--bg-secondary)',
        fontSize: 10,
        color: 'var(--text-muted)',
        display: 'flex',
        justifyContent: 'space-between',
        flexShrink: 0,
      }}>
        <span>{logs.length} événement(s) capturé(s)</span>
        <span>
          {counts.token || 0} tokens · {counts.sql || 0} SQL · {counts.query_result || 0} résultats
        </span>
      </div>
    </div>
  )
}
