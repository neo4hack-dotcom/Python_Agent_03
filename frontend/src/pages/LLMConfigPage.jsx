import React, { useEffect, useState } from 'react'
import { Cpu, CheckCircle, XCircle, RefreshCw, Save, Wifi, Webhook } from 'lucide-react'
import { llmApi } from '../services/api'
import { useToast } from '../components/Toast'

const PROVIDERS = [
  { value: 'ollama', label: 'Ollama', defaultUrl: 'http://localhost:11434/v1' },
  { value: 'lmstudio', label: 'LM Studio', defaultUrl: 'http://localhost:1234/v1' },
  { value: 'openai_compatible', label: 'OpenAI Compatible', defaultUrl: 'https://api.openai.com/v1' },
  { value: 'custom', label: 'Custom', defaultUrl: '' },
  { value: 'n8n', label: 'n8n Webhook', defaultUrl: '' },
]

export default function LLMConfigPage() {
  const { show, ToastContainer } = useToast()
  const [config, setConfig] = useState({
    provider: 'ollama',
    base_url: 'http://localhost:11434/v1',
    model: 'llama3.2',
    api_key: 'ollama',
    temperature: 0.1,
    max_tokens: 4096,
    timeout: 120,
    streaming: true,
    verify_ssl: true,
    webhook_url: '',
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [models, setModels] = useState([])
  const [loadingModels, setLoadingModels] = useState(false)

  const isN8N = config.provider === 'n8n'

  useEffect(() => {
    llmApi.get().then((r) => {
      setConfig({ webhook_url: '', ...r.data })
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  const handleProviderChange = (provider) => {
    const p = PROVIDERS.find((x) => x.value === provider)
    setConfig((c) => ({ ...c, provider, base_url: p?.defaultUrl || c.base_url }))
    setModels([])
    setTestResult(null)
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await llmApi.update(config)
      show('Configuration sauvegardée', 'success')
    } catch (e) {
      show('Erreur lors de la sauvegarde: ' + e.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const r = await llmApi.test(config)
      setTestResult(r.data)
      show(r.data.success ? 'Connexion réussie!' : 'Connexion échouée', r.data.success ? 'success' : 'error')
    } catch (e) {
      setTestResult({ success: false, error: e.message })
      show('Test échoué: ' + e.message, 'error')
    } finally {
      setTesting(false)
    }
  }

  const handleLoadModels = async () => {
    setLoadingModels(true)
    try {
      const r = await llmApi.listModels(config.base_url, config.api_key, config.verify_ssl)
      if (r.data.success) {
        setModels(r.data.models)
        show(`${r.data.models.length} modèle(s) trouvé(s)`, 'success')
      } else {
        show('Impossible de charger les modèles', 'error')
      }
    } catch (e) {
      show('Erreur: ' + e.message, 'error')
    } finally {
      setLoadingModels(false)
    }
  }

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><div className="spinner" /></div>

  return (
    <div className="page">
      <ToastContainer />
      <div className="page-header">
        <div className="page-title">
          <Cpu size={20} />
          <h2>Configuration LLM</h2>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-secondary" onClick={handleTest} disabled={testing}>
            {testing ? <div className="spinner" /> : <Wifi size={15} />}
            Tester la connexion
          </button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? <div className="spinner" /> : <Save size={15} />}
            Sauvegarder
          </button>
        </div>
      </div>

      <div className="page-body">
        <div style={{ maxWidth: 720, display: 'flex', flexDirection: 'column', gap: 20 }}>

          {/* Test result banner */}
          {testResult && (
            <div className="card" style={{
              borderColor: testResult.success ? 'var(--success)' : 'var(--error)',
              background: testResult.success ? 'rgba(34,197,94,0.05)' : 'rgba(239,68,68,0.05)',
            }}>
              <div className="flex items-center gap-2">
                {testResult.success
                  ? <CheckCircle size={18} color="var(--success)" />
                  : <XCircle size={18} color="var(--error)" />}
                <span style={{ color: testResult.success ? 'var(--success)' : 'var(--error)', fontWeight: 600 }}>
                  {testResult.success ? 'Connexion établie avec succès' : `Connexion échouée: ${testResult.error || testResult.detail}`}
                </span>
              </div>
            </div>
          )}

          {/* Provider selection */}
          <div className="card">
            <div className="card-header">
              <h3>Fournisseur LLM</h3>
            </div>
            <div className="form-group">
              <label className="form-label">Type de fournisseur</label>
              <select
                className="select"
                value={config.provider}
                onChange={(e) => handleProviderChange(e.target.value)}
              >
                {PROVIDERS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
          </div>

          {/* n8n webhook config */}
          {isN8N && (
            <div className="card" style={{ borderColor: 'rgba(234,88,12,0.4)', background: 'rgba(234,88,12,0.03)' }}>
              <div className="card-header">
                <div className="flex items-center gap-2">
                  <Webhook size={16} color="#ea580c" />
                  <h3 style={{ color: '#ea580c' }}>n8n Webhook</h3>
                </div>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div className="form-group">
                  <label className="form-label">URL du webhook</label>
                  <input
                    className="input"
                    value={config.webhook_url || ''}
                    onChange={(e) => setConfig((c) => ({ ...c, webhook_url: e.target.value }))}
                    placeholder="https://mon-instance.n8n.cloud/webhook/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                  />
                  <span className="text-sm text-muted" style={{ marginTop: 4 }}>
                    URL POST du webhook n8n qui reçoit les messages et retourne la réponse du LLM.
                  </span>
                </div>

                {/* n8n format info */}
                <div style={{
                  padding: '12px 14px', borderRadius: 8,
                  background: 'rgba(234,88,12,0.08)', border: '1px solid rgba(234,88,12,0.2)',
                  fontSize: 13, lineHeight: 1.7,
                }}>
                  <div style={{ fontWeight: 600, marginBottom: 8, color: '#ea580c' }}>
                    Format attendu par le webhook
                  </div>
                  <div style={{ color: 'var(--text-secondary)' }}>
                    <strong>Corps de la requête (POST) :</strong>
                    <pre style={{
                      marginTop: 6, marginBottom: 10, padding: '8px 10px',
                      background: 'rgba(0,0,0,0.2)', borderRadius: 6, fontSize: 12,
                      overflowX: 'auto',
                    }}>{`{
  "messages": [
    { "role": "system",    "content": "..." },
    { "role": "user",      "content": "..." },
    { "role": "assistant", "content": "..." }
  ]
}`}</pre>
                    <strong>Réponse acceptée (l'une ou l'autre) :</strong>
                    <pre style={{
                      marginTop: 6, padding: '8px 10px',
                      background: 'rgba(0,0,0,0.2)', borderRadius: 6, fontSize: 12,
                      overflowX: 'auto',
                    }}>{`{ "content": "Réponse du LLM..." }
{ "message": "Réponse du LLM..." }
{ "output":  "Réponse du LLM..." }
[{ "content": "..." }]  ← tableau n8n natif
{ "choices": [{ "message": { "content": "..." } }] }`}</pre>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Standard endpoint config (hidden for n8n) */}
          {!isN8N && (
            <div className="card">
              <div className="card-header">
                <h3>Point d'accès HTTP</h3>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div className="form-group">
                  <label className="form-label">URL de base (OpenAI-compatible)</label>
                  <input
                    className="input"
                    value={config.base_url}
                    onChange={(e) => setConfig((c) => ({ ...c, base_url: e.target.value }))}
                    placeholder="http://localhost:11434/v1"
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Clé API (optionnel pour usage local)</label>
                  <input
                    className="input"
                    type="password"
                    value={config.api_key}
                    onChange={(e) => setConfig((c) => ({ ...c, api_key: e.target.value }))}
                    placeholder="ollama / sk-xxx / ..."
                  />
                </div>
              </div>
            </div>
          )}

          {/* Model selection (hidden for n8n) */}
          {!isN8N && (
            <div className="card">
              <div className="card-header">
                <h3>Modèle</h3>
                <button className="btn btn-secondary btn-sm" onClick={handleLoadModels} disabled={loadingModels}>
                  {loadingModels ? <div className="spinner" style={{ width: 12, height: 12 }} /> : <RefreshCw size={13} />}
                  Charger les modèles
                </button>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div className="form-group">
                  <label className="form-label">Nom du modèle</label>
                  {models.length > 0 ? (
                    <select
                      className="select"
                      value={config.model}
                      onChange={(e) => setConfig((c) => ({ ...c, model: e.target.value }))}
                    >
                      {models.map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                  ) : (
                    <input
                      className="input"
                      value={config.model}
                      onChange={(e) => setConfig((c) => ({ ...c, model: e.target.value }))}
                      placeholder="llama3.2 / mistral / qwen2.5 / ..."
                    />
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Parameters */}
          <div className="card">
            <div className="card-header">
              <h3>Paramètres d'inférence</h3>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              {!isN8N && (
                <div className="form-group">
                  <label className="form-label">Température ({config.temperature})</label>
                  <input
                    type="range" min="0" max="2" step="0.05"
                    value={config.temperature}
                    onChange={(e) => setConfig((c) => ({ ...c, temperature: parseFloat(e.target.value) }))}
                    style={{ width: '100%', accentColor: 'var(--accent)' }}
                  />
                  <div className="flex justify-between text-sm text-muted">
                    <span>0 (précis)</span><span>2 (créatif)</span>
                  </div>
                </div>
              )}

              {!isN8N && (
                <div className="form-group">
                  <label className="form-label">Tokens max</label>
                  <input
                    className="input"
                    type="number" min="64" max="32768" step="256"
                    value={config.max_tokens}
                    onChange={(e) => setConfig((c) => ({ ...c, max_tokens: parseInt(e.target.value) }))}
                  />
                </div>
              )}

              <div className="form-group">
                <label className="form-label">Timeout (secondes)</label>
                <input
                  className="input"
                  type="number" min="10" max="600"
                  value={config.timeout}
                  onChange={(e) => setConfig((c) => ({ ...c, timeout: parseInt(e.target.value) }))}
                />
              </div>

              {!isN8N && (
                <div className="form-group">
                  <label className="form-label">Streaming</label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingTop: 8 }}>
                    <input
                      type="checkbox"
                      id="streaming"
                      checked={config.streaming}
                      onChange={(e) => setConfig((c) => ({ ...c, streaming: e.target.checked }))}
                      style={{ width: 16, height: 16, accentColor: 'var(--accent)' }}
                    />
                    <label htmlFor="streaming" style={{ cursor: 'pointer', color: 'var(--text-secondary)' }}>
                      Activer le streaming des tokens
                    </label>
                  </div>
                </div>
              )}

              <div className="form-group">
                <label className="form-label">Vérification SSL</label>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingTop: 8 }}>
                  <input
                    type="checkbox"
                    id="verify_ssl"
                    checked={config.verify_ssl !== false}
                    onChange={(e) => setConfig((c) => ({ ...c, verify_ssl: e.target.checked }))}
                    style={{ width: 16, height: 16, accentColor: 'var(--accent)' }}
                  />
                  <label htmlFor="verify_ssl" style={{ cursor: 'pointer', color: 'var(--text-secondary)' }}>
                    Vérifier le certificat SSL
                  </label>
                </div>
                {config.verify_ssl === false && (
                  <div style={{
                    marginTop: 6, padding: '6px 10px', borderRadius: 6,
                    background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.3)',
                    fontSize: 12, color: '#f59e0b',
                  }}>
                    ⚠️ Vérification SSL désactivée — à utiliser uniquement avec des serveurs locaux de confiance.
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Info */}
          {!isN8N && (
            <div className="card" style={{ background: 'rgba(99,102,241,0.05)', borderColor: 'rgba(99,102,241,0.3)' }}>
              <div className="flex items-center gap-2" style={{ marginBottom: 8 }}>
                <Cpu size={16} color="var(--accent-light)" />
                <span style={{ fontWeight: 600, color: 'var(--accent-light)' }}>Compatibilité</span>
              </div>
              <p className="text-sm text-secondary" style={{ lineHeight: 1.7 }}>
                Cette plateforme supporte tout endpoint compatible avec l'API OpenAI.<br />
                <strong>Ollama</strong>: <code>http://localhost:11434/v1</code> — clé: <code>ollama</code><br />
                <strong>LM Studio</strong>: <code>http://localhost:1234/v1</code> — clé: <code>lm-studio</code><br />
                <strong>vLLM</strong>: <code>http://localhost:8000/v1</code> — clé: quelconque
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
