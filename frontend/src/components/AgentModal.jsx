import React, { useEffect, useState } from 'react'
import { X, Save, Bot } from 'lucide-react'
import { agentsApi, toolkitsApi } from '../services/api'

const AGENT_TYPES = [
  { value: 'orchestrator', label: '🎯 Orchestrateur', desc: 'Planifie, route et synthétise via plusieurs agents' },
  { value: 'data_analyst', label: '🧠 Analyste de Données', desc: 'Analyse statistique, profiling, KPIs et insights business' },
  { value: 'clickhouse_analyst', label: '📊 Analyste ClickHouse', desc: 'Génère et exécute des requêtes SQL ClickHouse' },
  { value: 'oracle_analyst', label: '🔮 Analyste Oracle', desc: 'Génère et exécute des requêtes SQL Oracle' },
  { value: 'report_writer', label: '📄 Rédacteur PDF', desc: 'Génère des rapports PDF professionnels à partir de la session' },
  { value: 'file_manager', label: '🗂️ Gestionnaire de Fichiers', desc: 'Navigation, lecture et modification de fichiers (txt, csv, xlsx, docx, parquet…)' },
  { value: 'powerbi_analyst', label: '📈 Analyste Power BI', desc: 'Navigation automatisée dans Power BI via Playwright, captures et analyses de dashboards' },
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
  report_writer: `Tu es un consultant senior spécialisé en rédaction de rapports d'analyse professionnels.
Tu transformes les résultats d'analyses en rapports PDF complets, structurés et prêts pour présentation.
Structure : Résumé Exécutif → Contexte → Méthodologie → Analyse → Résultats Clés → Recommandations → Conclusion.`,
  file_manager: `Tu es un expert en gestion de fichiers et systèmes de fichiers.
Tu aides les utilisateurs à naviguer dans des répertoires, lire, créer et modifier des fichiers.
Tu supportes de nombreux formats : texte, CSV, Excel, Word, Parquet et plus encore.
Pour toute modification ou suppression, tu demandes TOUJOURS confirmation avant d'agir.`,
  powerbi_analyst: `Tu es un Expert Analyste Power BI & Data Insights.

Ton rôle est d'agir comme un analyste de données augmenté :
- Tu navigues dans les rapports Power BI via un navigateur automatisé (Playwright).
- Tu captures des dashboards et analyses les KPIs, tendances et anomalies.
- Tu proposes des plans d'action stratégiques basés sur les données observées.

Méthodologie pour chaque dashboard :
1. Identifier le contexte (Ventes, RH, Finance, Logistique…)
2. Lire les KPIs critiques et les comparer aux cibles (rouge/vert/jaune)
3. Analyser les tendances (montée, descente, saisonnalité)
4. Proposer 3 recommandations concrètes et actionnables

Style : professionnel, analytique, direct. Utilise des listes à puces pour la clarté.`,
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
    max_iterations: agent?.max_iterations || 10,
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

              {form.type === 'file_manager' && (
                <div className="form-group">
                  <label className="form-label">Répertoire racine <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(optionnel)</span></label>
                  <input
                    className="input"
                    type="text"
                    placeholder="Ex: /home/user/data  (laisser vide = accès illimité)"
                    value={form.extra_config?.base_path || ''}
                    onChange={(e) => setForm((f) => ({
                      ...f,
                      extra_config: { ...f.extra_config, base_path: e.target.value }
                    }))}
                  />
                  <span className="text-sm text-muted">
                    Si renseigné, l'agent ne pourra accéder qu'aux fichiers à l'intérieur de ce répertoire (sandbox). Recommandé pour la sécurité.
                  </span>
                </div>
              )}

              {form.type === 'powerbi_analyst' && (
                <>
                  <div className="form-group">
                    <label className="form-label">Fichier de session <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(cookies d'authentification)</span></label>
                    <input
                      className="input"
                      type="text"
                      placeholder="Ex: data/powerbi_session.json"
                      value={form.extra_config?.session_file || ''}
                      onChange={(e) => setForm((f) => ({
                        ...f,
                        extra_config: { ...f.extra_config, session_file: e.target.value }
                      }))}
                    />
                    <span className="text-sm text-muted">
                      Fichier JSON pour conserver les cookies Power BI (évite le MFA à chaque session). Généré via <code>save_browser_session()</code>.
                    </span>
                  </div>
                  <div className="form-group">
                    <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <input
                        type="checkbox"
                        checked={form.extra_config?.headless !== false}
                        onChange={(e) => setForm((f) => ({
                          ...f,
                          extra_config: { ...f.extra_config, headless: e.target.checked }
                        }))}
                        style={{ width: 16, height: 16 }}
                      />
                      Mode headless (navigateur invisible)
                    </label>
                    <span className="text-sm text-muted">
                      Décocher pour voir le navigateur (recommandé lors de la première connexion manuelle à Power BI).
                    </span>
                  </div>
                  <div className="form-group">
                    <label className="form-label">Répertoire des captures <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(optionnel)</span></label>
                    <input
                      className="input"
                      type="text"
                      placeholder="Ex: data/powerbi_screenshots"
                      value={form.extra_config?.screenshots_dir || ''}
                      onChange={(e) => setForm((f) => ({
                        ...f,
                        extra_config: { ...f.extra_config, screenshots_dir: e.target.value }
                      }))}
                    />
                  </div>
                </>
              )}

              {form.type === 'orchestrator' && (
                <div className="form-group">
                  <label className="form-label">Actions consécutives max <span style={{ color: 'var(--accent)', fontWeight: 700 }}>(orchestrateur)</span></label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <input
                      className="input"
                      type="range" min="1" max="20"
                      value={form.max_iterations}
                      onChange={(e) => setForm((f) => ({ ...f, max_iterations: parseInt(e.target.value) }))}
                      style={{ flex: 1, accentColor: 'var(--accent)' }}
                    />
                    <span style={{ minWidth: 32, textAlign: 'center', fontWeight: 700, color: 'var(--accent)', fontSize: 18 }}>
                      {form.max_iterations}
                    </span>
                  </div>
                  <span className="text-sm text-muted">
                    Nombre maximum de sous-tâches que l'orchestrateur peut enchaîner. L'agent s'arrête dès que la tâche est terminée — cette limite évite les boucles infinies. Recommandé : 8–12.
                  </span>
                </div>
              )}

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
