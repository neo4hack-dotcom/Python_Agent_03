import React, { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Send, Bot, Plus, Trash2, Download, Database,
  ChevronDown, MessageSquare, AlertCircle, CheckCircle, FileText, Terminal
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { agentsApi, chatApi, exportApi, downloadBlob, streamChat } from '../services/api'
import { useToast } from '../components/Toast'
import DataTable from '../components/DataTable'
import AgentLogPanel from '../components/AgentLogPanel'

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
    agentsApi.list().then((r) => {
      const active = r.data.filter((a) => a.is_active)
      setAgents(active)
      if (agentId) {
        const found = active.find((a) => a.id === agentId)
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
      .then((r) => setSessions(r.data || []))
      .catch(() => {})
    setCurrentSession(null)
    setMessages([])
    sessionIdRef.current = null
  }, [selectedAgent?.id])

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSelectSession = async (session) => {
    setCurrentSession(session)
    sessionIdRef.current = session.id
    try {
      const r = await chatApi.getMessages(selectedAgent.id, session.id)
      setMessages(r.data || [])
    } catch (e) {
      show('Erreur chargement messages', 'error')
    }
  }

  const handleNewSession = () => {
    setCurrentSession(null)
    sessionIdRef.current = null
    setMessages([])
  }

  const handleDeleteSession = async (session) => {
    if (!window.confirm('Supprimer cette session ?')) return
    try {
      await chatApi.deleteSession(session.id)
      if (currentSession?.id === session.id) handleNewSession()
      setSessions((s) => s.filter((x) => x.id !== session.id))
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

    // Optimistic user message
    const userMsg = {
      id: Date.now().toString(),
      role: 'user',
      content: userMessage,
      timestamp: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, userMsg])

    // Placeholder assistant message
    const assistantPlaceholder = {
      id: 'streaming',
      role: 'assistant',
      content: '',
      streaming: true,
      metadata: {},
    }
    setMessages((prev) => [...prev, assistantPlaceholder])

    let fullContent = ''
    let queryResult = null
    let newSessionId = sessionIdRef.current
    // Reset logs for new request
    setAgentLogs([])

    try {
      for await (const event of streamChat(selectedAgent.id, sessionIdRef.current, userMessage)) {
        // Feed ALL events to log panel
        setAgentLogs(prev => [...prev, event])

        if (event.type === 'token' || event.type === 'final') {
          fullContent = event.content
          setMessages((prev) =>
            prev.map((m) =>
              m.id === 'streaming' ? { ...m, content: event.content, streaming: event.type !== 'final' } : m
            )
          )
        } else if (event.type === 'sql') {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === 'streaming' ? { ...m, metadata: { ...m.metadata, sql: event.content } } : m
            )
          )
        } else if (event.type === 'query_result') {
          queryResult = event
          setMessages((prev) =>
            prev.map((m) =>
              m.id === 'streaming' ? { ...m, metadata: { ...m.metadata, query_result: event } } : m
            )
          )
        } else if (event.type === 'pdf_ready') {
          setLastPdfReady(event)
        } else if (event.type === 'error') {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === 'streaming'
                ? { ...m, content: `❌ Erreur: ${event.content}`, streaming: false, error: true }
                : m
            )
          )
          show('Erreur agent: ' + event.content, 'error')
          break
        }
      }

      // Finalize streaming message
      setMessages((prev) =>
        prev.map((m) =>
          m.id === 'streaming'
            ? { ...m, id: Date.now().toString(), streaming: false }
            : m
        )
      )

      // Reload sessions to pick up new session
      const sr = await chatApi.getSessions(selectedAgent.id)
      const updatedSessions = sr.data || []
      setSessions(updatedSessions)

      // Try to find the new/current session
      if (!sessionIdRef.current && updatedSessions.length > 0) {
        const latest = updatedSessions.sort((a, b) =>
          new Date(b.updated_at) - new Date(a.updated_at)
        )[0]
        sessionIdRef.current = latest.id
        setCurrentSession(latest)
      }
    } catch (e) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === 'streaming'
            ? { ...m, content: `❌ Erreur réseau: ${e.message}`, streaming: false, error: true }
            : m
        )
      )
      show('Erreur réseau: ' + e.message, 'error')
    } finally {
      setSending(false)
      inputRef.current?.focus()
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
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

  const agentIcon = (type) => {
    if (type === 'orchestrator') return '🎯'
    if (type === 'clickhouse_analyst') return '📊'
    if (type === 'oracle_analyst') return '🔮'
    if (type === 'data_analyst') return '🧠'
    if (type === 'report_writer') return '📄'
    return '🤖'
  }

  return (
    <div className="page" style={{ flexDirection: 'row', height: '100vh', overflow: 'hidden' }}>
      <ToastContainer />

      {/* Log Panel */}
      {showLogs && (
        <AgentLogPanel
          logs={agentLogs}
          onClose={() => setShowLogs(false)}
          agentName={selectedAgent?.name}
        />
      )}

      {/* Sessions Sidebar */}
      <div style={{
        width: 240, flexShrink: 0,
        background: 'var(--bg-secondary)',
        borderRight: '1px solid var(--border)',
        display: 'flex', flexDirection: 'column',
      }}>
        <div style={{ padding: '12px 10px', borderBottom: '1px solid var(--border)' }}>
          {/* Agent selector */}
          <div
            style={{
              padding: '8px 12px', borderRadius: 8, cursor: 'pointer',
              background: 'var(--bg-card)', border: '1px solid var(--border)',
              display: 'flex', alignItems: 'center', gap: 8,
            }}
            onClick={() => setShowAgentPicker(!showAgentPicker)}
          >
            <span style={{ fontSize: 18 }}>{selectedAgent ? agentIcon(selectedAgent.type) : '🤖'}</span>
            <span style={{ flex: 1, fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {selectedAgent?.name || 'Sélectionner un agent'}
            </span>
            <ChevronDown size={14} />
          </div>

          {showAgentPicker && (
            <div style={{
              marginTop: 4, background: 'var(--bg-card)', border: '1px solid var(--border)',
              borderRadius: 8, overflow: 'hidden', boxShadow: 'var(--shadow)',
            }}>
              {agents.map((a) => (
                <div
                  key={a.id}
                  onClick={() => {
                    setSelectedAgent(a)
                    navigate(`/chat/${a.id}`)
                    setShowAgentPicker(false)
                  }}
                  style={{
                    padding: '8px 12px', cursor: 'pointer', fontSize: 13,
                    display: 'flex', alignItems: 'center', gap: 8,
                    background: a.id === selectedAgent?.id ? 'rgba(99,102,241,0.15)' : 'transparent',
                    color: a.id === selectedAgent?.id ? 'var(--accent-light)' : 'var(--text-secondary)',
                  }}
                >
                  <span>{agentIcon(a.type)}</span>
                  <span>{a.name}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* New session button */}
        <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
          <button
            className="btn btn-secondary full-width"
            style={{ justifyContent: 'flex-start', gap: 8 }}
            onClick={handleNewSession}
          >
            <Plus size={15} />
            Nouvelle session
          </button>
        </div>

        {/* Session list */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 6px' }}>
          {sessions.length === 0 ? (
            <p style={{ padding: '16px 10px', fontSize: 12, color: 'var(--text-muted)', textAlign: 'center' }}>
              Aucune session
            </p>
          ) : (
            sessions
              .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))
              .map((s) => (
                <div
                  key={s.id}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 6, padding: '6px 8px',
                    borderRadius: 6, cursor: 'pointer', marginBottom: 2,
                    background: currentSession?.id === s.id ? 'rgba(99,102,241,0.15)' : 'transparent',
                    color: currentSession?.id === s.id ? 'var(--accent-light)' : 'var(--text-secondary)',
                  }}
                  onClick={() => handleSelectSession(s)}
                >
                  <MessageSquare size={13} style={{ flexShrink: 0 }} />
                  <span style={{ flex: 1, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {s.title || new Date(s.created_at).toLocaleDateString('fr-FR')}
                  </span>
                  <button
                    className="btn btn-icon"
                    style={{ padding: 2, opacity: 0.5, color: 'var(--error)' }}
                    onClick={(e) => { e.stopPropagation(); handleDeleteSession(s) }}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))
          )}
        </div>
      </div>

      {/* Chat area */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Chat header */}
        <div className="page-header">
          <div className="page-title">
            {selectedAgent ? (
              <>
                <span style={{ fontSize: 22 }}>{agentIcon(selectedAgent.type)}</span>
                <div>
                  <div style={{ fontWeight: 600 }}>{selectedAgent.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{selectedAgent.type}</div>
                </div>
              </>
            ) : (
              <span style={{ color: 'var(--text-muted)' }}>Sélectionnez un agent</span>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              className={`btn btn-secondary btn-sm ${sending ? 'active' : ''}`}
              onClick={() => setShowLogs(!showLogs)}
              title="Afficher les logs de l'agent"
              style={{
                position: 'relative',
                borderColor: agentLogs.length > 0 ? 'var(--accent)' : undefined,
              }}
            >
              <Terminal size={13} />
              Logs
              {agentLogs.length > 0 && (
                <span style={{
                  position: 'absolute', top: -5, right: -5,
                  background: sending ? 'var(--accent)' : 'var(--text-muted)',
                  color: 'white', borderRadius: 8, fontSize: 9, padding: '0 4px',
                  minWidth: 14, textAlign: 'center',
                  animation: sending ? 'pulse 1.5s infinite' : 'none',
                }}>
                  {agentLogs.length}
                </span>
              )}
            </button>
            {lastPdfReady && (
              <a
                href={lastPdfReady.download_url}
                download={lastPdfReady.filename}
                className="btn btn-primary btn-sm"
                style={{ textDecoration: 'none', background: 'linear-gradient(135deg,#1a3a6c,#2563eb)' }}
              >
                <FileText size={13} />
                Télécharger PDF
              </a>
            )}
            {sessionIdRef.current && (
              <button className="btn btn-secondary btn-sm" onClick={handleExportSession}>
                <Download size={13} />
                Exporter Excel
              </button>
            )}
          </div>
        </div>

        {/* Messages */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {messages.length === 0 ? (
            <WelcomeScreen agent={selectedAgent} />
          ) : (
            messages.map((msg, idx) => (
              <MessageBubble key={msg.id || idx} message={msg} onExport={show} />
            ))
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div style={{
          padding: '12px 24px 20px',
          borderTop: '1px solid var(--border)',
          background: 'var(--bg-secondary)',
        }}>
          {!selectedAgent ? (
            <div style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 12 }}>
              Sélectionnez un agent pour commencer
            </div>
          ) : (
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
              <textarea
                ref={inputRef}
                className="textarea"
                style={{
                  flex: 1, minHeight: 48, maxHeight: 160,
                  resize: 'none', fontFamily: 'inherit', fontSize: 14,
                }}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={
                  selectedAgent.type === 'clickhouse_analyst' || selectedAgent.type === 'oracle_analyst'
                    ? 'Posez votre question analytique... (Entrée pour envoyer)'
                    : 'Décrivez votre tâche à l\'orchestrateur... (Entrée pour envoyer)'
                }
                disabled={sending}
              />
              <button
                className="btn btn-primary"
                style={{ padding: '12px 16px', flexShrink: 0 }}
                onClick={handleSend}
                disabled={!input.trim() || sending}
              >
                {sending ? <div className="spinner" /> : <Send size={16} />}
              </button>
            </div>
          )}
          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
            Shift+Entrée pour un saut de ligne
          </p>
        </div>
      </div>
    </div>
  )
}

function MessageBubble({ message, onExport }) {
  const isUser = message.role === 'user'
  const hasQueryResult = message.metadata?.query_result
  const hasSql = message.metadata?.sql
  const [showTable, setShowTable] = useState(false)

  return (
    <div style={{
      display: 'flex',
      flexDirection: isUser ? 'row-reverse' : 'row',
      gap: 10,
      alignItems: 'flex-start',
    }}>
      {/* Avatar */}
      <div style={{
        width: 32, height: 32, borderRadius: 8, flexShrink: 0,
        background: isUser ? 'var(--accent)' : 'var(--bg-hover)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 16,
      }}>
        {isUser ? '👤' : '🤖'}
      </div>

      {/* Content */}
      <div style={{ maxWidth: '75%', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{
          background: isUser ? 'var(--accent)' : 'var(--bg-card)',
          border: `1px solid ${isUser ? 'transparent' : 'var(--border)'}`,
          borderRadius: isUser ? '12px 2px 12px 12px' : '2px 12px 12px 12px',
          padding: '10px 14px',
          color: isUser ? 'white' : 'var(--text-primary)',
        }}>
          {message.streaming ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {message.content ? (
                <MarkdownContent content={message.content} />
              ) : (
                <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>Réflexion en cours...</span>
              )}
              <span style={{ display: 'inline-block', width: 6, height: 16, background: 'var(--accent-light)', borderRadius: 2, animation: 'blink 1s infinite' }} />
            </div>
          ) : (
            <MarkdownContent content={message.content} isUser={isUser} />
          )}
        </div>

        {/* SQL block */}
        {hasSql && !isUser && (
          <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <div style={{ padding: '6px 12px', background: 'var(--bg-hover)', fontSize: 11, color: 'var(--text-muted)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span><Database size={12} style={{ display: 'inline', marginRight: 6 }} />SQL Généré</span>
            </div>
            <pre style={{ padding: 12, fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--accent-light)', overflowX: 'auto', margin: 0 }}>
              {message.metadata.sql}
            </pre>
          </div>
        )}

        {/* Query result */}
        {hasQueryResult && (
          <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <div
              style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', background: 'var(--bg-hover)' }}
              onClick={() => setShowTable(!showTable)}
            >
              <span style={{ fontSize: 12, fontWeight: 600 }}>
                <CheckCircle size={13} style={{ display: 'inline', marginRight: 6, color: 'var(--success)' }} />
                {message.metadata.query_result.row_count} lignes
                {message.metadata.query_result.warning && ' ⚠️'}
              </span>
              <span style={{ fontSize: 11, color: 'var(--accent-light)' }}>{showTable ? 'Masquer' : 'Afficher'}</span>
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

        <div style={{ fontSize: 11, color: 'var(--text-muted)', textAlign: isUser ? 'right' : 'left' }}>
          {message.timestamp ? new Date(message.timestamp).toLocaleTimeString('fr-FR') : ''}
        </div>
      </div>
    </div>
  )
}

function MarkdownContent({ content, isUser }) {
  return (
    <div style={{ fontSize: 14, lineHeight: 1.7 }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ inline, children, ...props }) {
            if (inline) {
              return (
                <code style={{
                  background: isUser ? 'rgba(255,255,255,0.2)' : 'var(--bg-hover)',
                  padding: '1px 6px', borderRadius: 4,
                  fontFamily: 'var(--font-mono)', fontSize: '0.9em',
                }}>
                  {children}
                </code>
              )
            }
            return (
              <pre style={{
                background: 'var(--bg-primary)', border: '1px solid var(--border)',
                padding: 12, borderRadius: 8, overflowX: 'auto',
                fontFamily: 'var(--font-mono)', fontSize: 12, margin: '8px 0',
              }}>
                <code {...props}>{children}</code>
              </pre>
            )
          },
          table({ children }) {
            return (
              <div style={{ overflowX: 'auto', margin: '8px 0' }}>
                <table className="data-table">{children}</table>
              </div>
            )
          },
          p({ children }) {
            return <p style={{ margin: '4px 0' }}>{children}</p>
          },
          ul({ children }) {
            return <ul style={{ paddingLeft: 20, margin: '4px 0' }}>{children}</ul>
          },
          ol({ children }) {
            return <ol style={{ paddingLeft: 20, margin: '4px 0' }}>{children}</ol>
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

function WelcomeScreen({ agent }) {
  if (!agent) return null
  const hints = {
    orchestrator: [
      'Analyse les ventes du dernier trimestre et compare avec N-1',
      'Crée un rapport consolidé sur la performance des équipes',
      'Décompose et exécute une analyse multi-source',
    ],
    clickhouse_analyst: [
      'Montre-moi le top 10 des produits par chiffre d\'affaires ce mois-ci',
      'Combien d\'utilisateurs actifs par jour sur les 30 derniers jours ?',
      'Quelles tables ClickHouse sont disponibles ?',
    ],
    oracle_analyst: [
      'Liste les 20 premières commandes de la semaine',
      'Quelle est la répartition des clients par région ?',
      'Montre-moi les tables Oracle disponibles',
    ],
  }
  const examples = hints[agent.type] || hints.orchestrator

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 40 }}>
      <div style={{ fontSize: 56, marginBottom: 16 }}>
        {agent.type === 'orchestrator' ? '🎯' : agent.type === 'clickhouse_analyst' ? '📊' : '🔮'}
      </div>
      <h2 style={{ marginBottom: 8 }}>{agent.name}</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: 32, textAlign: 'center', maxWidth: 500 }}>
        {agent.description || 'Posez vos questions ou décrivez une tâche.'}
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, width: '100%', maxWidth: 500 }}>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', textAlign: 'center' }}>Exemples de requêtes</p>
        {examples.map((ex, i) => (
          <div key={i} style={{
            padding: '10px 16px', borderRadius: 8,
            border: '1px solid var(--border)', cursor: 'pointer',
            fontSize: 13, color: 'var(--text-secondary)',
            transition: 'all 0.15s',
            background: 'var(--bg-card)',
          }}
            onClick={() => document.querySelector('textarea')?.value !== undefined &&
              (document.querySelector('textarea').value = ex)
            }
          >
            {ex}
          </div>
        ))}
      </div>
    </div>
  )
}
