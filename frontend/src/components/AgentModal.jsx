import React, { useEffect, useState } from 'react'
import { X, Save, Bot } from 'lucide-react'
import { agentsApi, toolkitsApi } from '../services/api'

const AGENT_TYPES = [
  { value: 'orchestrator', label: '🎯 Orchestrateur', desc: 'Planifie, route et synthétise via plusieurs agents' },
  { value: 'data_analyst', label: '🧠 Analyste de Données', desc: 'Analyse statistique, profiling, KPIs et insights business' },
  { value: 'clickhouse_analyst', label: '📊 Analyste ClickHouse', desc: 'Génère et exécute des requêtes SQL ClickHouse' },
  { value: 'oracle_analyst', label: '🔮 Analyste Oracle', desc: 'Génère et exécute des requêtes SQL Oracle' },
  { value: 'custom', label: '🤖 Personnalisé', desc: 'Agent générique configurable' },
]

const DEFAULT_PROMPTS = {
  orchestrator: `Tu es un orchestrateur expert. Tu décomposes les tâches complexes en étapes, délègues aux spécialistes appropriés, et synthétises les résultats en réponses claires et actionnables.`,
  data_analyst: `Tu es un analyste de données senior et expert en business intelligence.

Tes capacités :
- **Analyse statistique** : distributions, moyenne/médiane, écart-type, percentiles (P10→P99), outliers, corrélations
- **Data profiling** : qualité des données, taux de NULL, cardinalité, valeurs aberrantes, couverture temporelle
- **Analyse de tendances** : évolution temporelle, taux de croissance MoM/YoY, saisonnalité, anomalies
- **KPIs métier** : calcul, interprétation et benchmark de métriques business
- **Recommandations** : insights actionnables, comparaisons, diagnostic de performance

Tu produis des rapports structurés avec : résumé exécutif, insights clés chiffrés, analyse détaillée, recommandations business et points de vigilance.
Tu peux travailler avec ou sans base de données connectée.`,
  clickhouse_analyst: `Tu es un expert ClickHouse. Tu génères des requêtes SQL optimisées :
- Jamais de SELECT * — sélectionne explicitement les colonnes
- Filtre toujours sur les clés de partition (ORDER BY / PARTITION BY)
- Utilise les fonctions natives : uniq(), any(), argMax(), topK()
- Gestion du temps : toStartOfDay(), toStartOfHour(), toYYYYMM()
- Évite les JOINs, utilise IN (SELECT ...) ou Dictionaries
- Format SQL : mots-clés en MAJUSCULES, indentation propre`,
  oracle_analyst: `Tu es un expert Oracle. Tu génères des requêtes SQL optimisées :
- Jamais de SELECT * — sélectionne explicitement les colonnes
- Utilise les fonctions Oracle : TRUNC(), TO_DATE(), analytic functions OVER PARTITION BY
- Optimise les colonnes indexées dans les WHERE
- Format SQL : mots-clés en MAJUSCULES, indentation propre`,
  custom: `Tu es un assistant IA expert. Réponds de façon précise et structurée.`,
}

export default function AgentModal({ agent, connections, onClose, onSaved }) {
  const isEdit = !!agent
  const [form, setForm] = useState({
    name: agent?.name || '',
    type: agent?.type || 'orchestrator',
    description: agent?.description || '',
    connection_id: agent?.connection_id || '',
    toolkit_id: agent?.toolkit_id || '',
    system_prompt: agent?.system_prompt || DEFAULT_PROMPTS.orchestrator,
    max_retries: agent?.max_retries || 3,
    row_limit: agent?.row_limit || 1000,
    extra_config: agent?.extra_config || {},
  })
  const [toolkits, setToolkits] = useState([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('basic')

  useEffect(() => {
    toolkitsApi.list().then(r => setToolkits(r.data)).catch(() => {})
  }, [])

  const handleTypeChange = (type) => {
    setForm((f) => ({
      ...f,
      type,
      system_prompt: DEFAULT_PROMPTS[type] || DEFAULT_PROMPTS.custom,
    }))
  }

  const handleSave = async () => {
    if (!form.name.trim()) { setError('Le nom est obligatoire'); return }
    setSaving(true)
    setError('')
    try {
      if (isEdit) {
        await agentsApi.update(agent.id, form)
      } else {
        await agentsApi.create(form)
      }
      onSaved()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setSaving(false)
    }
  }

  const needsConnection = ['clickhouse_analyst', 'oracle_analyst', 'data_analyst'].includes(form.type)
  const connectionOptional = form.type === 'data_analyst'
  const filteredConnections = connections.filter((c) => {
    if (form.type === 'clickhouse_analyst') return c.type === 'clickhouse'
    if (form.type === 'oracle_analyst') return c.type === 'oracle'
    return true // data_analyst peut se connecter à n'importe quel type de DB
  })

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 680 }}>
        <div className="modal-header">
          <div className="flex items-center gap-2">
            <Bot size={18} />
            <h3>{isEdit ? `Modifier : ${agent.name}` : 'Créer un agent'}</h3>
          </div>
          <button className="btn btn-icon btn-secondary" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        <div className="tabs" style={{ padding: '0 20px' }}>
          {['basic', 'prompt', 'advanced'].map((tab) => (
            <div
              key={tab}
              className={`tab ${activeTab === tab ? 'active' : ''}`}
              onClick={() => setActiveTab(tab)}
            >
              {tab === 'basic' ? 'Général' : tab === 'prompt' ? 'Prompt Système' : 'Avancé'}
            </div>
          ))}
        </div>

        <div className="modal-body">
          {error && <div className="form-error" style={{ padding: '8px 12px', background: 'rgba(239,68,68,0.1)', borderRadius: 6 }}>{error}</div>}

          {activeTab === 'basic' && (
            <>
              <div className="form-group">
                <label className="form-label">Nom de l'agent *</label>
                <input
                  className="input"
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="ex: Analyste Ventes ClickHouse"
                />
              </div>

              <div className="form-group">
                <label className="form-label">Type d'agent</label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  {AGENT_TYPES.map((t) => (
                    <div
                      key={t.value}
                      onClick={() => handleTypeChange(t.value)}
                      style={{
                        padding: '10px 12px',
                        borderRadius: 8,
                        border: `1px solid ${form.type === t.value ? 'var(--accent)' : 'var(--border)'}`,
                        background: form.type === t.value ? 'rgba(99,102,241,0.1)' : 'var(--bg-primary)',
                        cursor: 'pointer',
                        transition: 'all 0.15s',
                      }}
                    >
                      <div style={{ fontWeight: 600, fontSize: 13 }}>{t.label}</div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{t.desc}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Description</label>
                <textarea
                  className="textarea"
                  value={form.description}
                  onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                  placeholder="Description de l'agent et de son rôle..."
                  style={{ minHeight: 60 }}
                />
              </div>

              {needsConnection && (
                <div className="form-group">
                  <label className="form-label">
                    Connexion Base de Données {connectionOptional ? <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(optionnelle)</span> : '*'}
                  </label>
                  {connectionOptional && (
                    <p className="text-sm text-muted" style={{ marginBottom: 6 }}>
                      💡 Sans connexion, l'agent analyse les données fournies dans la conversation. Avec une connexion, il peut aussi interroger directement la base.
                    </p>
                  )}
                  {filteredConnections.length === 0 ? (
                    <p className="text-sm text-warning">
                      Aucune connexion disponible.
                      <a href="/connections" style={{ marginLeft: 6 }}>Créer une connexion →</a>
                    </p>
                  ) : (
                    <select
                      className="select"
                      value={form.connection_id}
                      onChange={(e) => setForm((f) => ({ ...f, connection_id: e.target.value }))}
                    >
                      <option value="">{connectionOptional ? '-- Aucune (analyse sans DB) --' : '-- Sélectionner --'}</option>
                      {filteredConnections.map((c) => (
                        <option key={c.id} value={c.id}>{c.name} ({c.host}:{c.port})</option>
                      ))}
                    </select>
                  )}
                </div>
              )}
            </>
          )}

          {activeTab === 'prompt' && (
            <div className="form-group" style={{ height: '100%' }}>
              <label className="form-label">Prompt système</label>
              <textarea
                className="textarea"
                value={form.system_prompt}
                onChange={(e) => setForm((f) => ({ ...f, system_prompt: e.target.value }))}
                style={{ minHeight: 300, fontFamily: 'var(--font-mono)', fontSize: 12 }}
              />
              <p className="text-sm text-muted">
                Ce prompt définit le comportement, les contraintes et les capacités de l'agent.
              </p>
            </div>
          )}

          {activeTab === 'advanced' && (
            <>
              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">Tentatives max (retry)</label>
                  <input
                    className="input"
                    type="number" min="1" max="10"
                    value={form.max_retries}
                    onChange={(e) => setForm((f) => ({ ...f, max_retries: parseInt(e.target.value) }))}
                  />
                  <span className="text-sm text-muted">En cas d'erreur SQL, nombre de ré-essais</span>
                </div>

                <div className="form-group">
                  <label className="form-label">Limite de lignes (hard cap)</label>
                  <input
                    className="input"
                    type="number" min="1" max="50000"
                    value={form.row_limit}
                    onChange={(e) => setForm((f) => ({ ...f, row_limit: parseInt(e.target.value) }))}
                  />
                  <span className="text-sm text-muted">Nombre maximum de lignes retournées par requête</span>
                </div>
              </div>

              {['clickhouse_analyst', 'oracle_analyst'].includes(form.type) && (
                <div className="form-group">
                  <label className="form-label">
                    Toolkit <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(optionnel)</span>
                  </label>
                  <p className="text-sm text-muted" style={{ marginBottom: 6 }}>
                    🔧 Sélectionnez un toolkit pour contrôler quels outils SQL sont disponibles
                    pour cet agent (activer/désactiver list_tables, get_schema, execute_query, check_query).
                  </p>
                  <select
                    className="select"
                    value={form.toolkit_id}
                    onChange={(e) => setForm((f) => ({ ...f, toolkit_id: e.target.value }))}
                  >
                    <option value="">-- Toolkit par défaut (tous les outils) --</option>
                    {toolkits
                      .filter(tk => tk.db_type === (form.type === 'oracle_analyst' ? 'oracle' : 'clickhouse') || tk.db_type === 'any')
                      .map(tk => (
                        <option key={tk.id} value={tk.id}>
                          {tk.is_default ? '⚙️ ' : '🔧 '}{tk.name}
                          {' '}({(tk.tools || []).filter(t => t.enabled).length} outils actifs)
                        </option>
                      ))
                    }
                  </select>
                  <a href="/toolkits" style={{ fontSize: 12, marginTop: 4, display: 'inline-block' }}>
                    Gérer les toolkits →
                  </a>
                </div>
              )}

              <div className="card" style={{ background: 'rgba(245,158,11,0.05)', borderColor: 'rgba(245,158,11,0.3)' }}>
                <p className="text-sm" style={{ color: 'var(--warning)' }}>
                  <strong>Guardrails actifs :</strong> Seules les commandes SELECT/WITH/EXPLAIN sont autorisées.
                  DROP, TRUNCATE, DELETE, INSERT, UPDATE sont bloquées au niveau du code.
                </p>
              </div>
            </>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose}>Annuler</button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? <div className="spinner" /> : <Save size={15} />}
            {isEdit ? 'Sauvegarder' : 'Créer'}
          </button>
        </div>
      </div>
    </div>
  )
}
