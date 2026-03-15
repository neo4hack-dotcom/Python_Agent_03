# 🤖 Python Agent Platform

Plateforme multi-agents basée sur **LangGraph**, **FastAPI** et **React**, permettant de créer, configurer et interagir avec des agents IA spécialisés — orchestrateur ReAct, analystes SQL, analyste de données business, gestionnaire de fichiers et analyste Power BI.

---

## 📋 Table des matières

- [Fonctionnalités](#-fonctionnalités)
- [Architecture](#-architecture)
- [Agents disponibles](#-agents-disponibles)
- [Interface utilisateur](#-interface-utilisateur)
- [Stack technique](#-stack-technique)
- [Prérequis](#-prérequis)
- [Installation Windows](#-installation-windows-recommandé)
- [Installation Linux / macOS](#-installation--démarrage-linux--macos)
- [Configuration LLM](#-configuration-llm)
- [Sécurité SQL (Guardrails)](#-sécurité-sql-guardrails)
- [Export / Import de configuration](#-export--import-de-configuration)
- [API REST](#-api-rest)
- [Structure du projet](#-structure-du-projet)

---

## ✨ Fonctionnalités

### Orchestrateur ReAct (agent manager)

| Capacité | Description |
|----------|-------------|
| **Boucle ReAct** | Raisonne à chaque étape en voyant toutes les observations passées — le plan émerge dynamiquement |
| **Lecture des capacités** | Charge les descriptions de chaque agent avant de planifier — n'appelle un agent que s'il peut répondre au besoin |
| **Délégation réelle** | Exécute les vrais pipelines SQL/data/PDF/fichiers (jamais de réponse LLM inventée) |
| **Refus honnête** | Si aucun agent ne peut traiter une demande, répond clairement sans halluciner (`cannot_fulfill`) |
| **PDF prêt** | Émet un événement `pdf_ready` au frontend avec lien de téléchargement après génération |
| **Human-in-the-loop** | Interruption pour validation humaine avant action critique (`interrupt_before`) |
| **Synthèse finale** | Compile tous les résultats agents en réponse Markdown structurée avec score de confiance |
| **Anti-boucle** | Limite configurable d'itérations (défaut 10) |

### Analyste de données 🧠

| Capacité | Description |
|----------|-------------|
| **Analyse statistique** | Distributions, percentiles P10→P99, outliers 2σ, corrélations |
| **Data Profiling** | Qualité des données, taux de NULL, cardinalité, couverture temporelle |
| **Tendances** | MoM/YoY, saisonnalité, détection d'anomalies |
| **KPIs métier** | Calcul, interprétation et benchmark de métriques business |
| **Recommandations** | Insights chiffrés, actions concrètes, points de vigilance |
| **Sans DB** | Fonctionne sans connexion — analyse les données fournies dans la conversation |
| **Avec DB** | Génère et exécute plusieurs requêtes SQL ciblées, puis analyse les données réelles |

### Analystes SQL (ClickHouse / Oracle)

| Capacité | Description |
|----------|-------------|
| **ReAct SQL** | Boucle dynamique : `list_tables` → `get_schema` → `execute_query` → ajustement |
| **Auto-correction** | L'erreur DB est réinjectée dans le prompt pour retry automatique (max 8 itérations) |
| **SQL optimisé** | Colonnes explicites, filtres sur clés, fonctions natives (`uniq()`, `argMax()`…) |
| **Toolkit configurable** | Activer/désactiver `list_tables`, `get_schema`, `execute_query`, `check_query` par agent |

### Gestionnaire de fichiers 🗂️

| Capacité | Description |
|----------|-------------|
| **Navigation** | Liste, lecture et recherche dans le système de fichiers |
| **Formats supportés** | txt, csv, xlsx, docx, parquet et fichiers texte génériques |
| **Écriture** | Création et modification de fichiers selon les permissions configurées |

### Analyste Power BI 📈

| Capacité | Description |
|----------|-------------|
| **Automation navigateur** | Navigation Playwright dans les rapports Power BI |
| **Captures d'écran** | Capture et analyse de visuels de dashboards |
| **Session partagée** | Partage la session Playwright avec l'orchestrateur pour continuité |

### Rédacteur PDF 📄

| Capacité | Description |
|----------|-------------|
| **Rapport Markdown→PDF** | Génère un PDF professionnel à partir du contenu de la session |
| **Événement frontend** | Émet `pdf_ready` → bouton de téléchargement direct dans le chat |
| **Contexte automatique** | Agrège les résultats de tous les agents précédents dans le rapport |

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         React Frontend                                   │
│  [Chat + SSE]  [Agents]  [Connexions DB]  [Config LLM]  [Export/Import] │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │ HTTP / SSE streaming
┌───────────────────────────▼─────────────────────────────────────────────┐
│                      FastAPI Backend                                      │
│  /api/chat  /api/agents  /api/connections  /api/report  /api/config      │
└──────────┬────────────────────────────────────────┬─────────────────────┘
           │                                        │
┌──────────▼──────────┐              ┌──────────────▼──────────────────────┐
│  LangGraph Graphs   │              │  JSON Local DB                       │
│                     │              │  agents · connections · sessions      │
│  Orchestrateur      │              │  messages · llm_config               │
│  (ReAct loop)       │              └──────────────────────────────────────┘
│  ┌──────────────┐   │
│  │  reasoner    │   │   ← Thought : LLM raisonne avec toutes les observations
│  │  worker      │◄──┼── ← Action  : délègue au bon pipeline agent
│  │  synthesizer │   │   ← Final Answer : compile les résultats
│  │  human_fb    │   │
│  └──────────────┘   │
│                     │
│  Analyste SQL       │    analyst_node → sql_tool_node → synthesizer_node
│  Data Analyst       │    planner → [sql_executor →] analyst → synthesizer
│  Report Writer      │    report_writer_node → pdf_node
│  File Manager       │    file_tool_node
│  Power BI           │    playwright_node → screenshot_node
└─────────────────────┘
           │
┌──────────▼──────────┐
│   LLM HTTP          │
│  (OpenAI-compat.)   │
│  Ollama / LM Studio │
│  OpenAI / vLLM…     │
└─────────────────────┘
```

### Graphe Orchestrateur — Boucle ReAct

```
                     ┌─────────────────────┐
               START ─►   reasoner_node     │  Thought : reçoit user_request +
                     │   (LLM à chaque      │  agents_block (avec descriptions) +
                     │    itération)        │  toutes les observations passées
                     └──────────┬──────────┘
                                │
          ┌─────────────────────┼────────────────────────┐
          │ call_agent          │ human_validation        │ final_answer
          │                     │                         │ cannot_fulfill
   ┌──────▼──────┐     ┌────────▼────────┐      ┌────────▼──────────┐
   │ worker_node │     │ human_feedback  │      │ synthesizer_node  │
   │  (Action)   │     │    _node        │      │  (Final Answer)   │
   └──────┬──────┘     └────────┬────────┘      └────────┬──────────┘
          │  Observation        │                         │
          └─────────────────────┘                         │
          (retour à reasoner avec nouveau résultat)        │
                                                 END ◄─────┘
```

**Chemins d'exécution dans `worker_node` :**

```
agent_type=cannot_fulfill   → refus honnête immédiat (0 appel LLM)
agent_type=data_analyst     → _run_data_analyst_subtask() → planner→sql→analyst→synthesizer
agent_type=clickhouse_analyst│
agent_type=oracle_analyst    → _run_analyst_subtask()    → ReAct SQL loop (max 8 iter)
agent_type=report_writer    → _run_report_subtask()      → report_writer_node → pdf_node
agent_type=file_manager     → _run_file_manager_subtask()
agent_type=powerbi_analyst  → _run_powerbi_subtask()     → Playwright navigation
agent_type=orchestrator     → LLM générique (raisonnement inter-étapes)
```

### Graphe Analyste de Données

```
START → [planner] ──(SQL + DB disponible)──► [sql_executor] ─┐
              │                                                 │
              └──────────(pas de SQL / pas de DB)─────────────┤
                                                               ▼
                                                         [analyst]
                                                               ▼
                                                       [synthesizer] → END
```

### Graphe Analyste SQL (ClickHouse / Oracle)

```
START → [agent_react] ──(tool_calls)──► [tools_react] ─┐
               ▲                                         │
               └──── (itération < 8) ───────────────────┘
               │
               └──── (final answer / max iter) ──► END
```

---

## 🤖 Agents disponibles

### 🌐 Orchestrateur (agent manager)

Agent central qui raisonne, délègue, observe et synthétise selon le pattern **ReAct**.

**Fonctionnement :**
1. `reasoner_node` : reçoit la requête + descriptions de tous les agents actifs + résultats précédents → choisit la prochaine action
2. `worker_node` : exécute l'agent sélectionné (SQL réel, analyse data, PDF, fichiers, Power BI)
3. Retour au `reasoner_node` avec le nouveau résultat comme observation
4. `synthesizer_node` : compile tous les résultats en réponse finale Markdown

**Règle de sélection des agents :**
- Lit la `description` de chaque agent avant de planifier
- N'assigne un agent que si sa description confirme qu'il peut traiter la sous-tâche
- Si aucun agent ne correspond → `cannot_fulfill` → réponse honnête sans hallucination

**Exemple de requête :**
```
Analyse complète de la table orders :
profiling des données, top clients par CA ce trimestre,
et génère un rapport PDF avec les recommandations.
```

---

### 🧠 Analyste de Données

Expert en analyse statistique, profiling et business intelligence. Complémentaire (non redondant) avec les analystes SQL.

**Pipeline :** `planner → [sql_executor →] analyst → synthesizer`

**Types d'analyses :**

| Type | Description |
|------|-------------|
| `statistical` | Distributions, percentiles P10→P99, outliers 2σ, corrélations |
| `profiling` | Qualité des données, taux de NULL, cardinalité, couverture temporelle |
| `trends` | MoM/YoY, saisonnalité, détection d'anomalies et pics |
| `kpi` | Calcul et interprétation de métriques business |
| `business` | Recommandations stratégiques, comparaisons, diagnostic |
| `mixed` | Combinaison (analyse la plus complète) |

**Différence vs analyste SQL :**
- L'analyste SQL = *récupération précise de données* ("Combien de commandes ce mois ?")
- L'analyste de données = *insight business* ("Qu'est-ce qui explique notre churn ?")
- L'analyste de données fonctionne **sans connexion DB** (analyse sur connaissances domaine)

**Exemple (avec DB) :**
```
Profiling complet de la table events :
distribution des types, taux de NULL, top utilisateurs actifs.
```

**Exemple (sans DB) :**
```
Voici mes données de ventes Q3 (CSV collé ici).
Analyse les tendances et donne 3 recommandations.
```

---

### 📊 Analyste ClickHouse

Agent SQL expert ClickHouse avec boucle ReAct dynamique.

**Directives SQL :**
- ❌ Jamais de `SELECT *` — colonnes explicites uniquement
- ✅ Filtres `WHERE` sur clés de partitionnement / `ORDER BY`
- ✅ Fonctions natives : `uniq()`, `any()`, `argMax()`, `topK()`
- ✅ Gestion temps : `toStartOfDay()`, `toYYYYMM()`, `BETWEEN`

---

### 🔮 Analyste Oracle

Même architecture que l'analyste ClickHouse, adapté pour Oracle.

**Spécificités :** `TRUNC()`, `TO_DATE()`, `SYSDATE`, `OVER PARTITION BY`, `ROWNUM` / `FETCH FIRST n ROWS ONLY`

---

### 📄 Rédacteur PDF

Génère un rapport PDF professionnel à partir du contenu de la session courante.

- Agrège automatiquement tous les résultats des agents précédents en contexte
- Émet un événement `pdf_ready` → bouton de téléchargement dans le chat
- Utilisé systématiquement comme **dernière étape** par l'orchestrateur quand l'utilisateur demande un rapport

---

### 🗂️ Gestionnaire de Fichiers

Navigation, lecture et écriture dans le système de fichiers local.

- Formats : txt, csv, xlsx, docx, parquet…
- Commandes : liste, lecture, écriture, recherche, création de dossiers

---

### 📈 Analyste Power BI

Navigation automatisée dans les rapports Power BI via Playwright.

- Capture et analyse de visuels de dashboards
- Partage la session navigateur avec l'orchestrateur pour continuité entre étapes

---

### 🤖 Custom

Agent générique configurable — rôle et comportement définis entièrement par le `system_prompt`.

---

## 🎨 Interface utilisateur

### Couleurs par type d'agent

Chaque type d'agent a une couleur et une icône dédiées, visibles dans les bulles de chat, les cartes de la page Agents et la sidebar :

| Agent | Icône | Couleur |
|-------|-------|---------|
| Orchestrateur (Manager) | 🌐 + icône Network SVG | Indigo `#6366f1` |
| ClickHouse | 📊 | Amber `#f59e0b` |
| Oracle | 🔮 | Violet `#8b5cf6` |
| Data Analyst | 🧠 | Emerald `#10b981` |
| Rapport PDF | 📄 | Blue `#2563eb` |
| Fichiers | 🗂️ | Cyan `#0891b2` |
| Power BI | 📈 | Orange `#f97316` |
| Custom | 🤖 | Slate `#64748b` |

### Bulles de chat

- **Bordure gauche colorée** (3 px) avec la couleur du type d'agent
- **Label expéditeur** au-dessus de chaque message : point coloré + type en majuscules
- Badge **MANAGER** pour l'orchestrateur
- Icône SVG `Network` (lucide) pour l'avatar de l'orchestrateur avec halo lumineux

### Événements SSE affichés en temps réel

| Événement | Affichage |
|-----------|-----------|
| `token` | Texte en streaming avec curseur clignotant |
| `final` | Réponse complète en Markdown |
| `sql` | Bloc SQL coloré avec bouton Copier |
| `query_result` | Tableau paginé dépliable avec compteur de lignes |
| `pdf_ready` | Bouton de téléchargement PDF (gradient bleu) |
| `screenshot_ready` | Captures d'écran Power BI avec bordure orange |
| `human_validation` | Bannière orange d'attente de validation |
| `error` | Message d'erreur rouge avec ❌ |

### Panneau de logs agent (AgentLogPanel)

Panneau latéral droit affichant en temps réel le détail des événements : tokens LLM, appels d'outils, résultats SQL, erreurs. Exportable, avec auto-scroll et compteur par type.

---

## 🛠 Stack technique

| Couche | Technologies |
|--------|-------------|
| **Backend** | Python 3.11+, FastAPI 0.115, Uvicorn |
| **Agents** | LangGraph 0.2, LangChain 0.3, MemorySaver |
| **LLM** | Tout endpoint OpenAI-compatible (Ollama, LM Studio, vLLM, OpenAI…) |
| **ClickHouse** | `clickhouse-connect` 0.8 |
| **Oracle** | `oracledb` 2.5 |
| **Power BI** | `playwright` (Chromium headless) |
| **PDF** | Génération via pipeline Markdown → PDF |
| **Export** | `pandas` + `openpyxl` |
| **Frontend** | React 18, Vite 6, React Router 6, Lucide React |
| **Communication** | REST + SSE streaming |
| **Stockage** | JSON fichiers locaux (thread-safe, atomic write) |

---

## 📦 Prérequis

| Outil | Version minimale | Lien |
|-------|-----------------|------|
| **Python** | 3.11+ | [python.org](https://www.python.org/downloads/) — ✅ cocher *"Add to PATH"* |
| **Node.js** | 18 LTS+ | [nodejs.org](https://nodejs.org/) |
| **LLM local** | — | [Ollama](https://ollama.ai) / [LM Studio](https://lmstudio.ai) |
| *(Optionnel)* ClickHouse | — | Pour les agents analystes SQL ClickHouse |
| *(Optionnel)* Oracle | — | Pour les agents analystes SQL Oracle |
| *(Optionnel)* Playwright | — | Pour l'agent Power BI (`playwright install chromium`) |

---

## 🪟 Installation Windows (recommandé)

> Tous les scripts sont des fichiers `.bat` — aucun outil supplémentaire requis.

### Étape 1 — Cloner le dépôt

```cmd
git clone https://github.com/neo4hack-dotcom/Python_Agent_03.git
cd Python_Agent_03
```

### Étape 2 — Installation complète (une seule fois)

Double-cliquez sur **`install.bat`** ou depuis CMD :

```cmd
install.bat
```

Ce script effectue automatiquement :
1. ✅ Détection de Python (`python` / `py`)
2. ✅ Création d'un environnement virtuel `.venv`
3. ✅ `pip install -r requirements.txt`
4. ✅ Détection de Node.js / npm
5. ✅ `npm install` dans `frontend/`
6. ✅ `npm run build` (compile le frontend React)
7. ✅ Création de raccourcis sur le **bureau Windows**

### Étape 3 — Lancer l'application

**Option A — Raccourci bureau** (créé par install.bat) :
- Double-cliquez sur 🖥️ **`Agent Platform`** sur votre bureau

**Option B — Depuis CMD** :
```cmd
launch.bat
```

Le navigateur s'ouvre automatiquement sur **http://localhost:8000**

---

### Scripts Windows disponibles

| Fichier | Rôle | Usage |
|---------|------|-------|
| `install.bat` | Installation complète + raccourci bureau | Une seule fois |
| `launch.bat` | Lancement production (serveur + navigateur) | Utilisation quotidienne |
| `launch_dev.bat` | Mode dev — 2 fenêtres séparées (hot reload) | Développement |
| `stop.bat` | Arrêter tous les serveurs de l'app | Arrêt propre |
| `create_shortcut.vbs` | Recrée les raccourcis bureau | Si raccourcis perdus |

### Résolution de problèmes Windows

| Problème | Solution |
|----------|----------|
| `'python' n'est pas reconnu` | Réinstaller Python en cochant **"Add to PATH"** |
| `'node' n'est pas reconnu` | Réinstaller Node.js LTS depuis nodejs.org |
| Port 8000 déjà utilisé | Lancer `stop.bat` puis `launch.bat` |
| Erreur `EACCES` / antivirus | Ajouter le dossier du projet aux exclusions antivirus |
| Fenêtre CMD qui se ferme | Lancer `launch.bat` depuis CMD, pas en double-clic |
| `venv` introuvable | Relancer `install.bat` |

---

## 🐧 Installation & Démarrage Linux / macOS

### 1. Cloner le dépôt

```bash
git clone https://github.com/neo4hack-dotcom/Python_Agent_03.git
cd Python_Agent_03
```

### 2. Mode développement (hot reload)

```bash
bash start_dev.sh
```

- Frontend : **http://localhost:3000**
- API Docs : **http://localhost:8000/api/docs**

### 3. Mode production

```bash
bash start.sh
```

- Application complète : **http://localhost:8000**

### Installation manuelle

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd frontend && npm install && npm run build && cd ..

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## ⚙️ Configuration LLM

La plateforme ne dépend d'aucun fournisseur de LLM en dur — tout endpoint **compatible OpenAI** est supporté.

### Via l'interface (recommandé)

Naviguez vers **Config LLM** dans la sidebar :

| Champ | Description | Exemple |
|-------|-------------|---------|
| Provider | Fournisseur LLM | `ollama` |
| URL de base | Endpoint HTTP | `http://localhost:11434/v1` |
| Modèle | Nom du modèle | `llama3.2` / `qwen2.5` |
| Clé API | Dummy pour usage local | `ollama` |
| Température | Créativité (0–2) | `0.1` |
| Tokens max | Longueur max de réponse | `4096` |

### Exemples de configuration

**Ollama**
```
URL : http://localhost:11434/v1
Clé : ollama
```

**LM Studio**
```
URL : http://localhost:1234/v1
Clé : lm-studio
```

**OpenAI**
```
URL : https://api.openai.com/v1
Clé : sk-xxxxxxxxxxxx
```

---

## 🔒 Sécurité SQL (Guardrails)

Toutes les requêtes SQL passent par un pipeline de sécurité avant exécution :

```python
# 1. Validation : SELECT/WITH/EXPLAIN uniquement
if not SELECT_PATTERN.match(sql):
    return {"error": "SECURITY: Only SELECT queries allowed"}

# 2. Blocage des mots-clés dangereux
if re.search(r"\b(DROP|TRUNCATE|DELETE|INSERT|UPDATE|ALTER)\b", sql):
    return {"error": "SECURITY: Blocked keyword detected"}

# 3. Injection automatique de LIMIT si absent
if "LIMIT" not in sql:
    sql += f"\nLIMIT {row_limit}"  # défaut: 1000 (analyste SQL) / 5000 (data analyst)

# 4. Plafonnement du LIMIT existant si > hard cap
# 5. Retour de l'erreur DB brute pour auto-correction LLM
```

| Guardrail | Comportement |
|-----------|-------------|
| Opérations bloquées | DROP, TRUNCATE, DELETE, INSERT, UPDATE, CREATE, ALTER |
| Limite de lignes | Injectée automatiquement (configurable par agent) |
| Anti-injection | Validation regex + échappement client |
| Feedback d'erreur | Erreur DB complète retournée au LLM pour auto-correction |
| Dry-run | Mode `EXPLAIN` disponible avant exécution réelle |

---

## 📤 Export / Import de configuration

La sidebar propose un bouton **Export / Import config** pour sauvegarder et restaurer la configuration complète.

### Export
- Télécharge un fichier JSON : agents, connexions DB, configuration LLM
- Option d'inclure ou masquer les mots de passe des connexions

### Import
- **Mode Merge** *(défaut)* : ajoute les nouvelles entrées sans écraser l'existant
- **Mode Replace** : remplace entièrement la configuration (avec avertissement)
- Rapport d'import : nombre d'éléments créés par catégorie

---

## 🌐 API REST

Documentation interactive disponible sur **`/api/docs`** (Swagger UI).

### Endpoints principaux

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET/PUT` | `/api/llm-config` | Lire/écrire la config LLM |
| `POST` | `/api/llm-config/test` | Tester la connexion LLM |
| `GET` | `/api/llm-config/models` | Lister les modèles disponibles |
| `GET/POST` | `/api/agents` | Lister/créer des agents |
| `GET/PUT/DELETE` | `/api/agents/{id}` | Lire/modifier/supprimer un agent |
| `GET/POST` | `/api/connections` | Lister/créer des connexions DB |
| `POST` | `/api/connections/{id}/test` | Tester une connexion |
| `GET` | `/api/connections/{id}/tables` | Lister les tables |
| `GET` | `/api/connections/{id}/schema/{table}` | Schéma d'une table |
| `POST` | `/api/chat/message` | Envoyer un message (streaming SSE) |
| `GET` | `/api/chat/sessions/{agent_id}` | Sessions d'un agent |
| `GET` | `/api/report/{id}/download` | Télécharger un rapport PDF généré |
| `POST` | `/api/export/excel/query` | Exporter une requête en Excel |
| `POST` | `/api/export/excel/session/{id}` | Exporter une session en Excel |
| `GET` | `/api/config/export` | Exporter la configuration complète (JSON) |
| `POST` | `/api/config/import` | Importer une configuration (merge ou replace) |

### Exemple — Chat streaming SSE

```bash
curl -X POST http://localhost:8000/api/chat/message \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "uuid-de-l-agent",
    "message": "Analyse statistique de la table orders et génère un rapport PDF",
    "stream": true
  }'
```

Réponse NDJSON (orchestrateur) :
```json
{"type": "token",        "content": "[Reasoner step 1] Calling data_analyst: profiling table orders..."}
{"type": "query_result", "row_count": 500, "columns": ["date","revenue"], "sql": "SELECT ..."}
{"type": "token",        "content": "[Reasoner step 2] Calling report_writer: generating PDF..."}
{"type": "final",        "content": "## 📋 Résumé Exécutif\n\n..."}
{"type": "pdf_ready",    "report_id": "abc123", "download_url": "/api/report/abc123/download"}
```

Réponse NDJSON (analyste SQL direct) :
```json
{"type": "sql",          "content": "SELECT product_id, sum(revenue) FROM orders ..."}
{"type": "query_result", "row_count": 10, "columns": [...], "rows": [...]}
{"type": "final",        "content": "## Résultats\n\nVoici le top 10..."}
```

---

## 📁 Structure du projet

```
Python_Agent_03/
│
├── backend/
│   ├── main.py                    # FastAPI app, CORS, routing statique
│   ├── database/
│   │   └── json_db.py             # DB JSON thread-safe (atomic write)
│   ├── graphs/
│   │   ├── state.py               # TypedDicts : OrchestratorState, DataAnalystState, AnalystState
│   │   ├── llm_factory.py         # Build ChatOpenAI depuis config DB (lazy loading)
│   │   ├── orchestrator_graph.py  # Graphe orchestrateur ReAct (reasoner→worker→synthesizer)
│   │   ├── data_analyst_graph.py  # Graphe analyste data (planner→sql_executor→analyst→synthesizer)
│   │   ├── analyst_graph.py       # Graphe analyste SQL ReAct (agent_react→tools_react, max 8 iter)
│   │   ├── report_graph.py        # Graphe rédacteur PDF (report_writer_node→pdf_node)
│   │   ├── file_manager_graph.py  # Graphe gestionnaire de fichiers
│   │   └── powerbi_graph.py       # Graphe analyste Power BI (Playwright)
│   ├── models/
│   │   ├── agent.py               # AgentConfig, AgentType (8 types)
│   │   ├── connection.py          # ConnectionConfig, ConnectionType
│   │   ├── llm_config.py          # LLMConfig, LLMProvider
│   │   └── chat.py                # ChatMessage, ChatSession, ChatRequest
│   ├── routers/
│   │   ├── agents.py              # CRUD agents + prompts par défaut
│   │   ├── connections.py         # CRUD connexions + test + schéma
│   │   ├── chat.py                # REST SSE + sessions (routing par agent_type)
│   │   ├── llm_config.py          # Config LLM + test + modèles disponibles
│   │   ├── export.py              # Export Excel (requête / session)
│   │   ├── report.py              # Download rapports PDF
│   │   └── config.py              # Export / Import configuration JSON
│   └── tools/
│       ├── sql_clickhouse.py      # ClickHouse tool (guardrails, schema, list_tables)
│       └── sql_oracle.py          # Oracle tool (guardrails, schema)
│
├── frontend/
│   ├── index.html                 # Entry point Vite
│   ├── vite.config.js             # Proxy /api → :8000
│   ├── package.json
│   └── src/
│       ├── App.jsx                # Router principal
│       ├── services/
│       │   └── api.js             # Axios + streamChat() SSE
│       ├── components/
│       │   ├── Sidebar.jsx        # Navigation + liste agents (couleurs par type)
│       │   ├── AgentModal.jsx     # Formulaire création/édition (8 types d'agents)
│       │   ├── AgentLogPanel.jsx  # Panneau logs temps réel (token, sql, tool_call…)
│       │   ├── ConnectionModal.jsx# Formulaire connexion DB
│       │   ├── ConfigModal.jsx    # Export / Import configuration JSON
│       │   ├── SchemaExplorer.jsx # Explorateur de tables/colonnes
│       │   ├── DataTable.jsx      # Tableau paginé + export Excel
│       │   └── Toast.jsx          # Notifications toast
│       ├── pages/
│       │   ├── ChatPage.jsx       # Chat SSE + sessions + AGENT_META (couleurs/icônes)
│       │   ├── AgentsPage.jsx     # Gestion des agents (cartes colorées par type)
│       │   ├── ConnectionsPage.jsx# Gestion des connexions DB
│       │   └── LLMConfigPage.jsx  # Configuration LLM HTTP
│       └── styles/
│           └── layout.css         # Thème dark + classes agent-icon-* par type
│
├── data/                          # JSON DB locale (gitignored)
│   └── .gitkeep
│
├── requirements.txt
├── install.bat                    # Installation complète Windows
├── launch.bat                     # Lancement production Windows
├── launch_dev.bat                 # Lancement développement Windows
├── stop.bat                       # Arrêt des serveurs Windows
├── create_shortcut.vbs            # Création raccourcis bureau Windows
├── start.sh                       # Démarrage production Linux/macOS
├── start_dev.sh                   # Démarrage développement Linux/macOS
└── README.md
```

---

## 🗄️ Bases de données locales (JSON)

| Fichier | Contenu |
|---------|---------|
| `agents.json` | Configurations des agents (id, type, prompt, connexion, limites) |
| `connections.json` | Connexions DB (hôte, port, credentials) |
| `llm_config.json` | Configuration LLM active (URL, modèle, température) |
| `sessions.json` | Historique des sessions de chat |
| `messages.json` | Messages des sessions (user + assistant + metadata) |

Les écritures sont atomiques (fichier `.tmp` → remplacement) pour éviter la corruption.

---

## 📤 Export Excel

| Mode | Feuilles générées |
|------|------------------|
| **Requête** | **Data** : résultats avec headers colorés, colonnes auto-dimensionnées · **Metadata** : date, SQL, row count |
| **Session** | **Chat History** : tous les messages (timestamp, rôle, contenu) · **Query_N** : données de chaque requête exécutée |

---

## 🔧 Développement

### Ajouter un nouvel agent

1. Créer le graphe dans `backend/graphs/<nom>_graph.py` (nœuds + routing + `build_<nom>_graph()`)
2. Ajouter le `TypedDict` d'état dans `backend/graphs/state.py`
3. Ajouter le type dans `backend/models/agent.py` → `AgentType`
4. Ajouter le prompt par défaut dans `backend/routers/agents.py` → `DEFAULT_PROMPTS`
5. Ajouter le runner SSE dans `backend/routers/chat.py` → `_run_<nom>()` + `_get_runner()`
6. Mettre à jour `AGENT_TYPES` dans `frontend/src/components/AgentModal.jsx`
7. Ajouter l'entrée dans `TYPE_LABELS` + `AGENT_META` dans `AgentsPage.jsx` et `ChatPage.jsx`
8. Ajouter la classe CSS `agent-icon-<type>` dans `frontend/src/styles/layout.css`

### Ajouter un nouveau type de connexion

1. Créer un tool dans `backend/tools/sql_<db>.py` (implémenter `execute`, `get_schema`, `list_tables`, `test_connection`)
2. Ajouter le type dans `backend/models/connection.py` → `ConnectionType`
3. Enregistrer le tool dans `backend/routers/connections.py` → `_build_tool()`

---

## 📄 Licence

MIT — voir [LICENSE](LICENSE)
