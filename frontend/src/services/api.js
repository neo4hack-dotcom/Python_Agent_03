import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

// ── LLM Config ───────────────────────────────────────────────────────────────
export const llmApi = {
  get: () => api.get('/llm-config'),
  update: (config) => api.put('/llm-config', config),
  test: (config) => api.post('/llm-config/test', config),
  listModels: (baseUrl, apiKey) =>
    api.get('/llm-config/models', { params: { base_url: baseUrl, api_key: apiKey } }),
}

// ── Agents ───────────────────────────────────────────────────────────────────
export const agentsApi = {
  list: () => api.get('/agents'),
  create: (payload) => api.post('/agents', payload),
  get: (id) => api.get(`/agents/${id}`),
  update: (id, payload) => api.put(`/agents/${id}`, payload),
  delete: (id) => api.delete(`/agents/${id}`),
  getTemplate: (id) => api.get(`/agents/${id}/template`),
}

// ── Connections ───────────────────────────────────────────────────────────────
export const connectionsApi = {
  list: () => api.get('/connections'),
  create: (payload) => api.post('/connections', payload),
  get: (id) => api.get(`/connections/${id}`),
  update: (id, payload) => api.put(`/connections/${id}`, payload),
  delete: (id) => api.delete(`/connections/${id}`),
  test: (id) => api.post(`/connections/${id}/test`),
  listTables: (id, database) =>
    api.get(`/connections/${id}/tables`, { params: database ? { database } : {} }),
  getSchema: (id, table, database) =>
    api.get(`/connections/${id}/schema/${table}`, {
      params: database ? { database } : {},
    }),
}

// ── Chat ──────────────────────────────────────────────────────────────────────
export const chatApi = {
  sendMessage: (payload) => api.post('/chat/message', payload),
  getSessions: (agentId) => api.get(`/chat/sessions/${agentId}`),
  getMessages: (agentId, sessionId) =>
    api.get(`/chat/sessions/${agentId}/${sessionId}/messages`),
  deleteSession: (sessionId) => api.delete(`/chat/sessions/${sessionId}`),
}

// ── Export ────────────────────────────────────────────────────────────────────
export const exportApi = {
  exportQuery: async (payload) => {
    const resp = await api.post('/export/excel/query', payload, {
      responseType: 'blob',
    })
    return resp
  },
  exportSession: async (sessionId) => {
    const resp = await api.post(`/export/excel/session/${sessionId}`, {}, {
      responseType: 'blob',
    })
    return resp
  },
}

// ── Streaming chat helper ─────────────────────────────────────────────────────
export async function* streamChat(agentId, sessionId, message) {
  const resp = await fetch('/api/chat/message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      agent_id: agentId,
      session_id: sessionId,
      message,
      stream: true,
    }),
  })

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    const text = decoder.decode(value)
    const lines = text.split('\n').filter((l) => l.trim())
    for (const line of lines) {
      try {
        yield JSON.parse(line)
      } catch {
        // ignore malformed chunks
      }
    }
  }
}

// ── Config Export / Import ────────────────────────────────────────────────────
export const configApi = {
  export: async (includePasswords = true) => {
    const resp = await api.get('/config/export', {
      params: { include_passwords: includePasswords },
      responseType: 'blob',
    })
    return resp.data
  },
  import: (bundle, mode = 'merge') =>
    api.post('/config/import', bundle, { params: { mode } }),
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export default api
