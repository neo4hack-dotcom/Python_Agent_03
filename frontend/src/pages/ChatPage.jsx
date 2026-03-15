import React, { useEffect, useRef, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Send, Plus, Trash2, Download, Database, ChevronDown,
  MessageSquare, CheckCircle, FileText, Terminal, Copy, Check,
  Bot, Sparkles, Clock
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { agentsApi, chatApi, exportApi, downloadBlob, streamChat } from '../services/api'
import { useToast } from '../components/Toast'
import DataTable from '../components/DataTable'
import AgentLogPanel from '../components/AgentLogPanel'

// ── Agent type metadata ────────────────────────────────────────────────────────
const AGENT_META = {
  orchestrator:      { icon: '🎯', color: '#6366f1', label: 'Orchestrateur' },
  clickhouse_analyst:{ icon: '📊', color: '#f59e0b', label: 'ClickHouse' },
  oracle_analyst:    { icon: '🔮', color: '#8b5cf6', label: 'Oracle' },
  data_analyst:      { icon: '🧠', color: '#10b981', label: 'Data Analyst' },
  report_writer:     { icon: '📄', color: '#2563eb', label: 'Rapport PDF' },
  custom:            { icon: '🤖', color: '#64748b', label: 'Custom' },
}

const WELCOME_HINTS = {
  orchestrator: ['Analyse les ventes du dernier trimestre', 'Crée un rapport consolidé multi-source', 'Compare les performances entre régions'],
  clickhouse_analyst: ['Top 10 produits par CA ce mois', 'Utilisateurs actifs par jour (30j)', 'Quelles tables sont disponibles ?'],
  oracle_analyst: ['Top 20 commandes de la semaine', 'Répartition clients par région', 'Tables Oracle disponibles'],
  data_analyst: ['Profiling statistique des données fournies', 'Analyse des tendances et saisonnalité', 'KPIs et métriques business'],
  report_writer: ['Génère un rapport PDF de notre analyse', 'Synthèse executive de la session', 'Rapport avec recommandations actionnables'],
}

// ── Copy button for code blocks ────────────────────────────────────────────────
function CopyButton({ text }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }
  return (
    <button onClick={copy} style={{
      position: 'absolute', top: 8, right: 8,
      background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.15)',
      borderRadius: 4, padding: '3px 8px', cursor: 'pointer',
      color: copied ? '#86efac' : '#94a3b8', fontSize: 11,
      display: 'flex', alignItems: 'center', gap: 4,
    }}>
      {copied ? <Check size={11} /> : <Copy size={11} />}
      {copied ? 'Copié' : 'Copier'}
    </button>
  )
}

// ── Markdown renderer ──────────────────────────────────────────────────────────
function MarkdownContent({ content, isUser }) {
  return (
    <div className="markdown-body" style={{ fontSize: 14, lineHeight: 1.75, color: isUser ? 'white' : 'var(--text-primary)' }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ inline, className, children, ...props }) {
            const match = /language-(\w+)/.exec(className || '')
            const lang = match ? match[1] : ''
            const codeString = String(children).replace(/\n$/, '')
            if (inline) {
              return (
                <code style={{
                  background: isUser ? 'rgba(255,255,255,0.2)' : 'var(--bg-hover)',
                  padding: '1px 6px', borderRadius: 4,
                  fontFamily: 'var(--font-mono)', fontSize: '0.88em',
                  color: isUser ? '#fef3c7' : 'var(--accent-light)',
                }}>
                  {children}
                </code>
              )
            }
            return (
              <div style={{ position: 'relative', margin: '10px 0' }}>
                {lang && (
                  <div style={{
                    position: 'absolute', top: 0, left: 0, right: 0,
                    background: '#1e293b', padding: '4px 12px',
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    borderRadius: '6px 6px 0 0', borderBottom: '1px solid rgba(255,255,255,0.08)',
                    zIndex: 1,
                  }}>
                    <span style={{ fontSize: 10, color: '#64748b', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{lang}</span>
                    <CopyButton text={codeString} />
                  </div>
                )}
                <SyntaxHighlighter
                  style={oneDark}
                  language={lang || 'text'}
                  PreTag="div"
                  customStyle={{
                    margin: 0, borderRadius: 6,
                    paddingTop: lang ? 36 : 12,
                    fontSize: 12.5,
                    border: '1px solid rgba(255,255,255,0.06)',
                  }}
                  {...props}
                >
                  {codeString}
                </SyntaxHighlighter>
              </div>
            )
          },
          table({ children }) {
            return (
              <div style={{ overflowX: 'auto', margin: '10px 0', borderRadius: 6, border: '1px solid var(--border)' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>{children}</table>
              </div>
            )
          },
          thead({ children }) {
            return <thead style={{ background: 'var(--bg-hover)' }}>{children}</thead>
          },
          th({ children }) {
            return <th style={{ padding: '7px 12px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid var(--border)', fontSize: 12 }}>{children}</th>
          },
          td({ children }) {
            return <td style={{ padding: '6px 12px', borderBottom: '1px solid var(--border)', fontSize: 13 }}>{children}</td>
          },
          p({ children }) { return <p style={{ margin: '0 0 8px' }}>{children}</p> },
          h1({ children }) { return <h1 style={{ fontSize: '1.4em', fontWeight: 700, color: 'var(--text-primary)', margin: '16px 0 8px', paddingBottom: 4, borderBottom: '2px solid var(--accent)' }}>{children}</h1> },
          h2({ children }) { return <h2 style={{ fontSize: '1.15em', fontWeight: 700, color: 'var(--text-primary)', margin: '14px 0 6px', paddingLeft: 8, borderLeft: '3px solid var(--accent)' }}>{children}</h2> },
          h3({ children }) { return <h3 style={{ fontSize: '1.05em', fontWeight: 600, color: 'var(--text-secondary)', margin: '10px 0 4px' }}>{children}</h3> },
          ul({ children }) { return <ul style={{ paddingLeft: 20, margin: '4px 0 8px' }}>{children}</ul> },
          ol({ children }) { return <ol style={{ paddingLeft: 20, margin: '4px 0 8px' }}>{children}</ol> },
          li({ children }) { return <li style={{ margin: '3px 0' }}>{children}</li> },
          blockquote({ children }) {
            return (
              <blockquote style={{
                borderLeft: '3px solid var(--accent)', background: 'rgba(99,102,241,0.07)',
                margin: '8px 0', padding: '6px 14px', borderRadius: '0 4px 4px 0',
                color: 'var(--text-secondary)',
              }}>{children}</blockquote>
            )
          },
          strong({ children }) { return <strong style={{ color: isUser ? '#fef3c7' : 'var(--text-primary)', fontWeight: 700 }}>{children}</strong> },
          hr() { return <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '12px 0' }} /> },
          a({ href, children }) {
            return (
              <a href={href} target="_blank" rel="noopener noreferrer"
                style={{ color: isUser ? '#93c5fd' : 'var(--accent-light)', textDecoration: 'underline' }}>
                {children}
              </a>
            )
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

// ── Message bubble ─────────────────────────────────────────────────────────────
function MessageBubble({ message, onExport, agentMeta }) {
  const isUser = message.role === 'user'
  const hasQueryResult = message.metadata?.query_result
  const hasSql = message.metadata?.sql
  const hasPdf = message.metadata?.pdf_ready
  const [showTable, setShowTable] = useState(false)
  const [hover, setHover] = useState(false)

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: isUser ? 'row-reverse' : 'row',
        gap: 10,
        alignItems: 'flex-start',
        marginBottom: 4,
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {/* Avatar */}
      <div style={{
        width: 34, height: 34, borderRadius: 10, flexShrink: 0,
        background: isUser ? 'var(--accent)' : (agentMeta?.color + '22' || 'var(--bg-hover)'),
        border: `1px solid ${isUser ? 'transparent' : (agentMeta?.color + '44' || 'var(--border)')}`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 17,
      }}>
        {isUser ? '👤' : (agentMeta?.icon || '🤖')}
      </div>

      {/* Content */}
      <div style={{ maxWidth: '76%', display: 'flex', flexDirection: 'column', gap: 6 }}>
        {/* Main bubble */}
        <div style={{
          background: isUser
            ? 'linear-gradient(135deg, var(--accent), #4f46e5)'
            : 'var(--bg-card)',
          border: `1px solid ${isUser ? 'transparent' : 'var(--border)'}`,
          borderRadius: isUser ? '16px 4px 16px 16px' : '4px 16px 16px 16px',
          padding: '10px 16px',
          color: isUser ? 'white' : 'var(--text-primary)',
          boxShadow: isUser ? '0 2px 12px rgba(99,102,241,0.25)' : '0 1px 4px rgba(0,0,0,0.12)',
        }}>
          {message.streaming ? (
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
              {message.content
                ? <MarkdownContent content={message.content} />
                : <span style={{ color: 'var(--text-muted)', fontSize: 13, fontStyle: 'italic' }}>Réflexion en cours…</span>
              }
              <span style={{
                display: 'inline-block', width: 7, height: 16, marginLeft: 2, marginTop: 2,
                background: 'var(--accent-light)', borderRadius: 2,
                animation: 'blink 0.9s step-start infinite', flexShrink: 0,
              }} />
            </div>
          ) : (
            <MarkdownContent content={message.content} isUser={isUser} />
          )}
        </div>

        {/* SQL block */}
        {hasSql && !isUser && (
          <div style={{
            borderRadius: 8, overflow: 'hidden',
            border: '1px solid rgba(245,158,11,0.3)',
            background: 'rgba(245,158,11,0.05)',
          }}>
            <div style={{
              padding: '5px 12px', fontSize: 11, fontWeight: 600,
              display: 'flex', alignItems: 'center', gap: 6,
              color: '#f59e0b', borderBottom: '1px solid rgba(245,158,11,0.2)',
            }}>
              <Database size={12} />SQL Généré
            </div>
            <div style={{ position: 'relative' }}>
              <SyntaxHighlighter style={oneDark} language="sql" PreTag="div"
                customStyle={{ margin: 0, borderRadius: 0, fontSize: 12, padding: '10px 12px' }}>
                {message.metadata.sql}
              </SyntaxHighlighter>
              <CopyButton text={message.metadata.sql} />
            </div>
          </div>
        )}

        {/* Query result */}
        {hasQueryResult && (
          <div style={{
            borderRadius: 8, overflow: 'hidden',
            border: '1px solid rgba(16,185,129,0.3)',
            background: 'rgba(16,185,129,0.05)',
          }}>
            <div
              style={{
                padding: '6px 12px', cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              }}
              onClick={() => setShowTable(!showTable)}
            >
              <span style={{ fontSize: 12, fontWeight: 600, color: '#10b981', display: 'flex', alignItems: 'center', gap: 6 }}>
                <CheckCircle size={13} />
                {message.metadata.query_result.row_count} ligne(s)
                {message.metadata.query_result.warning && <span style={{ color: '#f59e0b' }}>⚠️</span>}
              </span>
              <span style={{ fontSize: 11, color: 'var(--accent-light)' }}>
                {showTable ? '▲ masquer' : '▼ afficher'}
              </span>
            </div>
            {showTable && (
              <DataTable
                columns={message.metadata.query_result.columns}
                rows={message.metadata.query_result.rows}
                sql={message.metadata.query_result.sql}
                onExport={onExport}
              />
            )}
          </div>
        )}

        {/* PDF download */}
        {hasPdf && (
          <a
            href={message.metadata.pdf_ready.download_url}
            download={message.metadata.pdf_ready.filename}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 8,
              padding: '8px 16px', borderRadius: 8,
              background: 'linear-gradient(135deg,#1a3a6c,#2563eb)',
              color: 'white', textDecoration: 'none',
              fontWeight: 600, fontSize: 13, alignSelf: 'flex-start',
              boxShadow: '0 2px 10px rgba(37,99,235,0.35)',
            }}
          >
            <FileText size={14} />
            Télécharger le rapport PDF
          </a>
        )}

        {/* Timestamp */}
        {hover && message.timestamp && (
          <div style={{
            fontSize: 10, color: 'var(--text-muted)',
            textAlign: isUser ? 'right' : 'left',
            display: 'flex', alignItems: 'center', gap: 3,
            justifyContent: isUser ? 'flex-end' : 'flex-start',
          }}>
            <Clock size={9} />
            {new Date(message.timestamp).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Welcome screen ─────────────────────────────────────────────────────────────
function WelcomeScreen({ agent, onHintClick }) {
  if (!agent) return null
  const meta = AGENT_META[agent.type] || AGENT_META.custom
  const hints = WELCOME_HINTS[agent.type] || WELCOME_HINTS.orchestrator

  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      padding: '40px 24px', maxWidth: 560, margin: '0 auto', width: '100%',
    }}>
      <div style={{
        width: 72, height: 72, borderRadius: 20,
        background: `${meta.color}22`,
        border: `2px solid ${meta.color}44`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 36, marginBottom: 20,
        boxShadow: `0 8px 32px ${meta.color}22`,
      }}>
        {meta.icon}
      </div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 8 }}>{agent.name}</h2>
      <p style={{
        color: 'var(--text-muted)', marginBottom: 32,
        textAlign: 'center', lineHeight: 1.6, fontSize: 14,
        maxWidth: 420,
      }}>
        {agent.description || `Agent ${meta.label} — posez vos questions ou décrivez une tâche.`}
      </p>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
        Exemples de requêtes
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: '100%' }}>
        {hints.map((hint, i) => (
          <button
            key={i}
            onClick={() => onHintClick(hint)}
            style={{
              width: '100%', textAlign: 'left',
              padding: '10px 16px', borderRadius: 10,
              border: '1px solid var(--border)',
              background: 'var(--bg-card)', cursor: 'pointer',
              fontSize: 13, color: 'var(--text-secondary)',
              transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: 10,
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = meta.color; e.currentTarget.style.background = `${meta.color}11` }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.background = 'var(--bg-card)' }}
          >
            <Sparkles size={13} style={{ color: meta.color, flexShrink: 0 }} />
            {hint}
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Main ChatPage ──────────────────────────────────────────────────────────────
export default function ChatPage() {
  const { agentId } = useParams()
  const navigate = useNavigate()
  const { show, ToastContainer } = useToast()

  const [agents, setAgents] = useState([])
  const [selectedAgent, setSelectedAgent] = useState(null)
  const [sessions, setSessions] = useState([])
  const [currentSession, setCurrentSession] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [showAgentPicker, setShowAgentPicker] = useState(false)
  const [lastPdfReady, setLastPdfReady] = useState(null)
  const [showLogs, setShowLogs] = useState(false)
  const [agentLogs, setAgentLogs] = useState([])

  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)
  const sessionIdRef = useRef(null)

  // Load agents
  useEffect(() => {
    agentsApi.list().then(r => {
      const active = r.data.filter(a => a.is_active)
      setAgents(active)
      if (agentId) {
        const found = active.find(a => a.id === agentId)
        if (found) setSelectedAgent(found)
      } else if (active.length > 0) {
        setSelectedAgent(active[0])
        navigate(`/chat/${active[0].id}`, { replace: true })
      }
    }).catch(() => {})
  }, [agentId])

  // Load sessions when agent changes
  useEffect(() => {
    if (!selectedAgent) return
    chatApi.getSessions(selectedAgent.id)
      .then(r => setSessions(r.data || []))
      .catch(() => {})
    setCurrentSession(null)
    setMessages([])
    sessionIdRef.current = null
    setLastPdfReady(null)
  }, [selectedAgent?.id])

  // Scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSelectSession = async (session) => {
    setCurrentSession(session)
    sessionIdRef.current = session.id
    setLastPdfReady(null)
    try {
      const r = await chatApi.getMessages(selectedAgent.id, session.id)
      // Re-hydrate metadata from stored messages
      setMessages(r.data || [])
    } catch {
      show('Erreur chargement messages', 'error')
    }
  }

  const handleNewSession = () => {
    setCurrentSession(null)
    sessionIdRef.current = null
    setMessages([])
    setLastPdfReady(null)
    setAgentLogs([])
  }

  const handleDeleteSession = async (session) => {
    if (!window.confirm('Supprimer cette session ?')) return
    try {
      await chatApi.deleteSession(session.id)
      if (currentSession?.id === session.id) handleNewSession()
      setSessions(s => s.filter(x => x.id !== session.id))
      show('Session supprimée', 'success')
    } catch (e) {
      show('Erreur: ' + e.message, 'error')
    }
  }

  const handleSend = async () => {
    if (!input.trim() || !selectedAgent || sending) return
    const userMessage = input.trim()
    setInput('')
    setSending(true)
    setAgentLogs([])

    const userMsg = {
      id: Date.now().toString(),
      role: 'user',
      content: userMessage,
      timestamp: new Date().toISOString(),
      metadata: {},
    }
    setMessages(prev => [...prev, userMsg])

    const placeholder = { id: 'streaming', role: 'assistant', content: '', streaming: true, metadata: {} }
    setMessages(prev => [...prev, placeholder])

    let sqlForMsg = null
    let queryResultForMsg = null
    let pdfEvent = null

    try {
      for await (const event of streamChat(selectedAgent.id, sessionIdRef.current, userMessage)) {
        setAgentLogs(prev => [...prev, event])

        if (event.type === 'token' || event.type === 'final') {
          setMessages(prev => prev.map(m =>
            m.id === 'streaming'
              ? { ...m, content: event.content, streaming: event.type !== 'final' }
              : m
          ))
        } else if (event.type === 'sql') {
          sqlForMsg = event.content
          setMessages(prev => prev.map(m =>
            m.id === 'streaming' ? { ...m, metadata: { ...m.metadata, sql: event.content } } : m
          ))
        } else if (event.type === 'query_result') {
          queryResultForMsg = event
          setMessages(prev => prev.map(m =>
            m.id === 'streaming' ? { ...m, metadata: { ...m.metadata, query_result: event } } : m
          ))
        } else if (event.type === 'pdf_ready') {
          pdfEvent = event
          setLastPdfReady(event)
          setMessages(prev => prev.map(m =>
            m.id === 'streaming' ? { ...m, metadata: { ...m.metadata, pdf_ready: event } } : m
          ))
        } else if (event.type === 'error') {
          setMessages(prev => prev.map(m =>
            m.id === 'streaming'
              ? { ...m, content: `❌ Erreur: ${event.content}`, streaming: false, error: true }
              : m
          ))
          show('Erreur agent: ' + event.content, 'error')
          break
        }
      }

      // Finalize
      setMessages(prev => prev.map(m =>
        m.id === 'streaming' ? { ...m, id: Date.now().toString(), streaming: false } : m
      ))

      // Reload sessions
      const sr = await chatApi.getSessions(selectedAgent.id)
      const updatedSessions = sr.data || []
      setSessions(updatedSessions)

      if (!sessionIdRef.current && updatedSessions.length > 0) {
        const latest = updatedSessions.sort((a, b) =>
          new Date(b.updated_at) - new Date(a.updated_at)
        )[0]
        sessionIdRef.current = latest.id
        setCurrentSession(latest)
      }
    } catch (e) {
      setMessages(prev => prev.map(m =>
        m.id === 'streaming'
          ? { ...m, content: `❌ Erreur réseau: ${e.message}`, streaming: false, error: true }
          : m
      ))
      show('Erreur réseau: ' + e.message, 'error')
    } finally {
      setSending(false)
      inputRef.current?.focus()
    }
  }

  const handleKeyDown = e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const handleExportSession = async () => {
    if (!sessionIdRef.current) return
    try {
      const r = await exportApi.exportSession(sessionIdRef.current)
      downloadBlob(r.data, `session_${sessionIdRef.current.slice(0, 8)}.xlsx`)
      show('Export téléchargé', 'success')
    } catch (e) {
      show('Erreur export: ' + e.message, 'error')
    }
  }

  const agentMeta = selectedAgent ? (AGENT_META[selectedAgent.type] || AGENT_META.custom) : null

  return (
    <div className="page" style={{ flexDirection: 'row', height: '100vh', overflow: 'hidden' }}>
      <ToastContainer />

      {/* Log Panel */}
      {showLogs && (
        <AgentLogPanel logs={agentLogs} onClose={() => setShowLogs(false)} agentName={selectedAgent?.name} />
      )}

      {/* ── Sessions sidebar ──────────────────────────────────────────── */}
      <div style={{
        width: 250, flexShrink: 0,
        background: 'var(--bg-secondary)',
        borderRight: '1px solid var(--border)',
        display: 'flex', flexDirection: 'column',
      }}>
        {/* Agent picker */}
        <div style={{ padding: '10px 10px 0' }}>
          <div
            onClick={() => setShowAgentPicker(!showAgentPicker)}
            style={{
              padding: '9px 12px', borderRadius: 10, cursor: 'pointer',
              background: agentMeta ? `${agentMeta.color}11` : 'var(--bg-card)',
              border: `1px solid ${agentMeta ? agentMeta.color + '33' : 'var(--border)'}`,
              display: 'flex', alignItems: 'center', gap: 10,
              transition: 'all 0.15s',
            }}
          >
            <span style={{ fontSize: 20 }}>{agentMeta?.icon || '🤖'}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {selectedAgent?.name || 'Choisir un agent'}
              </div>
              {agentMeta && (
                <div style={{ fontSize: 10, color: agentMeta.color, fontWeight: 600, letterSpacing: '0.05em' }}>
                  {agentMeta.label.toUpperCase()}
                </div>
              )}
            </div>
            <ChevronDown size={14} style={{ color: 'var(--text-muted)', flexShrink: 0, transform: showAgentPicker ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} />
          </div>

          {showAgentPicker && (
            <div style={{
              marginTop: 4,
              background: 'var(--bg-card)',
              border: '1px solid var(--border)',
              borderRadius: 10, overflow: 'hidden',
              boxShadow: 'var(--shadow)',
            }}>
              {agents.map(a => {
                const m = AGENT_META[a.type] || AGENT_META.custom
                return (
                  <div
                    key={a.id}
                    onClick={() => { setSelectedAgent(a); navigate(`/chat/${a.id}`); setShowAgentPicker(false) }}
                    style={{
                      padding: '8px 12px', cursor: 'pointer', fontSize: 13,
                      display: 'flex', alignItems: 'center', gap: 10,
                      background: a.id === selectedAgent?.id ? `${m.color}15` : 'transparent',
                      borderLeft: a.id === selectedAgent?.id ? `2px solid ${m.color}` : '2px solid transparent',
                      transition: 'all 0.1s',
                    }}
                  >
                    <span style={{ fontSize: 16 }}>{m.icon}</span>
                    <div>
                      <div style={{ fontWeight: a.id === selectedAgent?.id ? 600 : 400 }}>{a.name}</div>
                      <div style={{ fontSize: 10, color: m.color }}>{m.label}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* New session */}
        <div style={{ padding: '8px 10px' }}>
          <button className="btn btn-secondary full-width" style={{ justifyContent: 'flex-start', gap: 8, fontSize: 12 }} onClick={handleNewSession}>
            <Plus size={14} />
            Nouvelle conversation
          </button>
        </div>

        {/* Session list */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 6px 8px' }}>
          {sessions.length === 0 ? (
            <p style={{ padding: '16px 10px', fontSize: 12, color: 'var(--text-muted)', textAlign: 'center' }}>
              Aucune session
            </p>
          ) : (
            sessions
              .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))
              .map(s => (
                <div
                  key={s.id}
                  onClick={() => handleSelectSession(s)}
                  style={{
                    display: 'flex', alignItems: 'flex-start', gap: 8, padding: '8px 8px',
                    borderRadius: 8, cursor: 'pointer', marginBottom: 2,
                    background: currentSession?.id === s.id ? `${agentMeta?.color || 'var(--accent)'}15` : 'transparent',
                    borderLeft: currentSession?.id === s.id ? `2px solid ${agentMeta?.color || 'var(--accent)'}` : '2px solid transparent',
                    transition: 'all 0.1s',
                  }}
                >
                  <MessageSquare size={13} style={{ flexShrink: 0, marginTop: 2, color: currentSession?.id === s.id ? agentMeta?.color : 'var(--text-muted)' }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      fontWeight: currentSession?.id === s.id ? 600 : 400,
                      color: currentSession?.id === s.id ? 'var(--text-primary)' : 'var(--text-secondary)',
                    }}>
                      {s.title || 'Conversation'}
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                      {new Date(s.updated_at).toLocaleDateString('fr-FR')}
                    </div>
                  </div>
                  <button
                    className="btn btn-icon"
                    style={{ padding: 2, opacity: 0.4, color: 'var(--error)', flexShrink: 0 }}
                    onClick={e => { e.stopPropagation(); handleDeleteSession(s) }}
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              ))
          )}
        </div>
      </div>

      {/* ── Chat area ─────────────────────────────────────────────────── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Chat header */}
        <div className="page-header" style={{ padding: '10px 20px' }}>
          <div className="page-title" style={{ gap: 10 }}>
            {selectedAgent ? (
              <>
                <div style={{
                  width: 38, height: 38, borderRadius: 10,
                  background: `${agentMeta?.color}22`,
                  border: `1px solid ${agentMeta?.color}44`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 20,
                }}>
                  {agentMeta?.icon}
                </div>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 15 }}>{selectedAgent.name}</div>
                  <div style={{ fontSize: 11, color: agentMeta?.color }}>{agentMeta?.label}</div>
                </div>
                {currentSession && (
                  <div style={{
                    marginLeft: 6, padding: '2px 8px', borderRadius: 6,
                    background: 'var(--bg-hover)', fontSize: 11,
                    color: 'var(--text-muted)', maxWidth: 200,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {currentSession.title || 'Session'}
                  </div>
                )}
              </>
            ) : (
              <span style={{ color: 'var(--text-muted)' }}>Sélectionnez un agent</span>
            )}
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setShowLogs(!showLogs)}
              style={{
                position: 'relative',
                borderColor: sending ? 'var(--accent)' : (agentLogs.length > 0 ? 'rgba(99,102,241,0.4)' : undefined),
              }}
              title="Logs de l'agent en temps réel"
            >
              <Terminal size={13} />
              Logs
              {agentLogs.length > 0 && (
                <span style={{
                  position: 'absolute', top: -5, right: -5,
                  background: sending ? 'var(--accent)' : '#475569',
                  color: 'white', borderRadius: 8, fontSize: 9,
                  padding: '1px 5px', minWidth: 15, textAlign: 'center',
                }}>
                  {agentLogs.length}
                </span>
              )}
            </button>

            {lastPdfReady && (
              <a
                href={lastPdfReady.download_url}
                download={lastPdfReady.filename}
                className="btn btn-sm"
                style={{
                  textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 6,
                  background: 'linear-gradient(135deg,#1a3a6c,#2563eb)', color: 'white',
                  padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600,
                  border: 'none',
                }}
              >
                <FileText size={12} /> PDF
              </a>
            )}

            {sessionIdRef.current && (
              <button className="btn btn-secondary btn-sm" onClick={handleExportSession}>
                <Download size={12} />
                Excel
              </button>
            )}
          </div>
        </div>

        {/* Messages area */}
        <div style={{
          flex: 1, overflowY: 'auto', padding: '20px 24px',
          display: 'flex', flexDirection: 'column', gap: 12,
        }}>
          {messages.length === 0 ? (
            <WelcomeScreen agent={selectedAgent} onHintClick={hint => setInput(hint)} />
          ) : (
            messages.map((msg, idx) => (
              <MessageBubble
                key={msg.id || idx}
                message={msg}
                onExport={show}
                agentMeta={agentMeta}
              />
            ))
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div style={{
          padding: '12px 20px 18px',
          borderTop: '1px solid var(--border)',
          background: 'var(--bg-secondary)',
        }}>
          {!selectedAgent ? (
            <div style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 12, fontSize: 14 }}>
              Sélectionnez un agent dans la liste pour commencer
            </div>
          ) : (
            <>
              <div style={{
                display: 'flex', gap: 10, alignItems: 'flex-end',
                background: 'var(--bg-card)',
                border: `1px solid ${sending ? 'var(--accent)' : 'var(--border)'}`,
                borderRadius: 12, padding: '8px 8px 8px 14px',
                transition: 'border-color 0.2s',
                boxShadow: sending ? '0 0 0 2px rgba(99,102,241,0.15)' : 'none',
              }}>
                <textarea
                  ref={inputRef}
                  style={{
                    flex: 1, minHeight: 40, maxHeight: 160,
                    resize: 'none', fontFamily: 'inherit', fontSize: 14,
                    background: 'transparent', border: 'none', outline: 'none',
                    color: 'var(--text-primary)', lineHeight: 1.6,
                    paddingTop: 4,
                  }}
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={
                    selectedAgent.type === 'report_writer'
                      ? 'Demandez un rapport PDF de la session… (Entrée pour envoyer)'
                      : selectedAgent.type.includes('analyst')
                        ? 'Posez votre question analytique… (Entrée pour envoyer)'
                        : 'Décrivez votre tâche… (Entrée pour envoyer)'
                  }
                  disabled={sending}
                />
                <button
                  onClick={handleSend}
                  disabled={!input.trim() || sending}
                  style={{
                    width: 40, height: 40, borderRadius: 8, border: 'none',
                    background: input.trim() && !sending
                      ? `linear-gradient(135deg, ${agentMeta?.color || 'var(--accent)'}, var(--accent))`
                      : 'var(--bg-hover)',
                    cursor: input.trim() && !sending ? 'pointer' : 'not-allowed',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    flexShrink: 0, transition: 'all 0.2s',
                    color: input.trim() && !sending ? 'white' : 'var(--text-muted)',
                  }}
                >
                  {sending ? <div className="spinner" style={{ width: 16, height: 16 }} /> : <Send size={16} />}
                </button>
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 5, paddingLeft: 2 }}>
                Shift+Entrée pour un saut de ligne · Les agents ont accès à l'historique de cette session
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
