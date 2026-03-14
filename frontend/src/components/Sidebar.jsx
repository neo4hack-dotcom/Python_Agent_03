import React, { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import {
  Bot, ChevronLeft, ChevronRight, Cpu, Plus, Plug, FolderDown
} from 'lucide-react'
import { agentsApi } from '../services/api'
import ConfigModal from './ConfigModal'

export default function Sidebar({ open, onToggle }) {
  const [agents, setAgents] = useState([])
  const [showConfig, setShowConfig] = useState(false)
  const navigate = useNavigate()

  useEffect(() => {
    agentsApi.list().then((r) => setAgents(r.data)).catch(() => {})
  }, [])

  const agentIcon = (type) => {
    if (type === 'orchestrator') return '🎯'
    if (type === 'clickhouse_analyst') return '📊'
    if (type === 'oracle_analyst') return '🔮'
    return '🤖'
  }

  return (
    <>
      <aside className={`sidebar ${open ? '' : 'collapsed'}`}>
        <div className="sidebar-header">
          <div className="sidebar-logo">
            <div className="sidebar-logo-icon">🤖</div>
            {open && <span>Agent Platform</span>}
          </div>
          <button className="btn btn-icon btn-secondary" onClick={onToggle} title="Toggle sidebar">
            {open ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
          </button>
        </div>

        <nav className="sidebar-nav">
          {open && <div className="nav-section-title">Navigation</div>}

          <NavLink to="/agents" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Bot size={18} className="nav-item-icon" />
            {open && <span className="nav-item-label">Agents</span>}
          </NavLink>

          <NavLink to="/connections" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Plug size={18} className="nav-item-icon" />
            {open && <span className="nav-item-label">Connexions DB</span>}
          </NavLink>

          <NavLink to="/llm-config" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Cpu size={18} className="nav-item-icon" />
            {open && <span className="nav-item-label">Config LLM</span>}
          </NavLink>

          {open && agents.length > 0 && (
            <>
              <div className="nav-section-title" style={{ marginTop: 12 }}>Chats Récents</div>
              {agents.filter(a => a.is_active).slice(0, 8).map((agent) => (
                <NavLink
                  key={agent.id}
                  to={`/chat/${agent.id}`}
                  className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                >
                  <span style={{ fontSize: 16 }}>{agentIcon(agent.type)}</span>
                  <span className="nav-item-label">{agent.name}</span>
                </NavLink>
              ))}
            </>
          )}

          {!open && agents.filter(a => a.is_active).slice(0, 6).map((agent) => (
            <NavLink
              key={agent.id}
              to={`/chat/${agent.id}`}
              className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
              title={agent.name}
            >
              <span style={{ fontSize: 16 }}>{agentIcon(agent.type)}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          {/* Export / Import configuration */}
          <button
            className="btn btn-secondary full-width"
            style={{ justifyContent: open ? 'flex-start' : 'center', marginBottom: 6 }}
            onClick={() => setShowConfig(true)}
            title="Exporter / Importer la configuration"
          >
            <FolderDown size={16} />
            {open && <span>Export / Import config</span>}
          </button>

          <button
            className="btn btn-secondary full-width"
            style={{ justifyContent: open ? 'flex-start' : 'center' }}
            onClick={() => navigate('/agents')}
          >
            <Plus size={16} />
            {open && <span>Nouvel agent</span>}
          </button>
        </div>
      </aside>

      {showConfig && <ConfigModal onClose={() => setShowConfig(false)} />}
    </>
  )
}
