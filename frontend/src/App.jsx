import React, { useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import ChatPage from './pages/ChatPage'
import AgentsPage from './pages/AgentsPage'
import ConnectionsPage from './pages/ConnectionsPage'
import LLMConfigPage from './pages/LLMConfigPage'
import ToolkitsPage from './pages/ToolkitsPage'
import './styles/layout.css'

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true)

  return (
    <BrowserRouter>
      <div className="app-layout">
        <Sidebar open={sidebarOpen} onToggle={() => setSidebarOpen(!sidebarOpen)} />
        <div className={`app-content ${sidebarOpen ? '' : 'sidebar-collapsed'}`}>
          <Routes>
            <Route path="/" element={<Navigate to="/chat" replace />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:agentId" element={<ChatPage />} />
            <Route path="/agents" element={<AgentsPage />} />
            <Route path="/connections" element={<ConnectionsPage />} />
            <Route path="/llm-config" element={<LLMConfigPage />} />
            <Route path="/toolkits" element={<ToolkitsPage />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  )
}
