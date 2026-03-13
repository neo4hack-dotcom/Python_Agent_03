# 🤖 Python Agent Platform

Plateforme multi-agents basée sur **LangGraph**, **FastAPI** et **React**, permettant de créer, configurer et interagir avec des agents IA spécialisés — notamment un orchestrateur et des analystes de bases de données ClickHouse / Oracle.

---

## 📋 Table des matières

- [Fonctionnalités](#-fonctionnalités)
- [Architecture](#-architecture)
- [Stack technique](#-stack-technique)
- [Prérequis](#-prérequis)
- [Installation Windows](#-installation-windows-recommandé)
- [Installation Linux / macOS](#-installation--démarrage-linux--macos)
- [Configuration LLM](#-configuration-llm)
- [Agents disponibles](#-agents-disponibles)
- [Sécurité SQL (Guardrails)](#-sécurité-sql-guardrails)
- [API REST](#-api-rest)
- [Structure du projet](#-structure-du-projet)

---

## ✨ Fonctionnalités

### Agent Orchestrateur
| Capacité | Description |
|----------|-------------|
| **Planification** | Décompose une requête complexe en sous-tâches ordonnées (backlog) |
| **Routage dynamique** | Sélectionne automatiquement l'agent spécialisé le plus adapté |
| **Fan-out / Fan-in** | Exécution parallèle de tâches via l'API `Send` de LangGraph |
| **Gestion d'état** | State partagé entre nœuds avec `MemorySaver` (checkpoint & reprise) |
| **Auto-correction** | Boucle de correction si une sous-tâche échoue (max 3 tentatives) |
| **Human-in-the-loop** | Interruption stratégique pour validation humaine avant action critique |
| **Synthèse** | Agrégation cohérente de tous les résultats workers en réponse finale |

### Agent Analyste ClickHouse / Oracle
| Capacité | Description |
|----------|-------------|
| **SQL optimisé** | Génère des requêtes respectant les best practices (colonnes explicites, filtres sur clés) |
| **Retry loop** | Cycle `analyst → sql_tool → [synthèse / correction]` jusqu'à 3 tentatives |
| **Auto-correction SQL** | Le message d'erreur ClickHouse est réinjecté dans le prompt pour auto-correction |
| **Injection de schéma** | Le contexte de schéma (types, clés primaires, partitions) est fourni dynamiquement |
| **Fonctions natives** | Encourage `uniq()`, `argMax()`, `topK()`, `toStartOfDay()` etc. |
| **Explain Plan** | Mode dry-run `EXPLAIN` avant exécution réelle |
| **Audit système** | Interrogation de `system.query_log` pour identifier les requêtes lentes |

### Interface utilisateur
- Chat en streaming temps réel (WebSocket + SSE)
- Affichage du SQL généré avec coloration syntaxique
- Tableau de données paginé dans le chat
- Export Excel des résultats (requête ou session complète)
- Gestion des connexions DB avec test en direct et explorateur de schéma
- Configuration LLM HTTP avec détection des modèles disponibles

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         React Frontend                           │
│   [Chat]  [Agents]  [Connexions DB]  [Config LLM]               │
└───────────────────────┬─────────────────────────────────────────┘
                        │ HTTP / WebSocket
┌───────────────────────▼─────────────────────────────────────────┐
│                      FastAPI Backend                              │
│  /api/chat  /api/agents  /api/connections  /api/llm-config       │
│  /api/export                                                      │
└──────────┬───────────────────────────────┬───────────────────────┘
           │                               │
┌──────────▼──────────┐      ┌─────────────▼──────────────────────┐
│  LangGraph          │      │  JSON Local DB                      │
│  Orchestrateur      │      │  agents.json / connections.json     │
│  ┌──────────────┐   │      │  sessions.json / llm_config.json    │
│  │   Planner    │   │      └─────────────────────────────────────┘
│  │  Dispatcher  │   │
│  │   Worker     │◄──┼──── Agents Workers (ClickHouse, Oracle)
│  │  Corrector   │   │
│  │  Synthesizer │   │
│  └──────────────┘   │
│                     │
│  Analyste SQL       │
│  ┌──────────────┐   │
│  │   Analyst    │   │
│  │   SQL Tool   │───┼──── ClickHouse / Oracle
│  │  Synthesizer │   │
│  └──────────────┘   │
└─────────────────────┘
           │
┌──────────▼──────────┐
│   LLM Local HTTP    │
│  Ollama / LM Studio │
│  (OpenAI-compat.)   │
└─────────────────────┘
```

### Graphe LangGraph — Orchestrateur

```
START
  │
  ▼
[planner] ──────────────────────────────────────────────────────┐
  │                                                              │
  ▼                                                              │
[dispatcher] ──(human required)──► [human_feedback] ─────────┐  │
  │                                                            │  │
  ├──(task pending)──► [worker] ──(success)──► [dispatcher] ──┘  │
  │                        │                                      │
  │                    (failure)                                  │
  │                        │                                      │
  │                        ▼                                      │
  │                  [corrector] ──► [worker]                     │
  │                                                               │
  └──(all done)──► [synthesizer] ──► END ────────────────────────┘
```

### Graphe LangGraph — Analyste SQL

```
START
  │
  ▼
[analyst] ──► [sql_tool] ──(success)──────────► [synthesizer] ──► END
                   │
                (error, retry < 3)
                   │
                   └──────────────────────────► [analyst]  (avec erreur injectée)
                   │
                (error, retry >= 3)
                   │
                   └──────────────────────────► [error_handler] ──► END
```

---

## 🛠 Stack technique

| Couche | Technologies |
|--------|-------------|
| **Backend** | Python 3.11+, FastAPI 0.115, Uvicorn |
| **Agents** | LangGraph 0.2, LangChain 0.3, MemorySaver |
| **LLM** | Tout endpoint OpenAI-compatible (Ollama, LM Studio, vLLM…) |
| **ClickHouse** | `clickhouse-connect` 0.8 |
| **Oracle** | `oracledb` 2.5 |
| **Export** | pandas + openpyxl |
| **Frontend** | React 18, Vite 6, React Router 6 |
| **Communication** | REST + WebSocket streaming + SSE |
| **Stockage** | JSON fichiers locaux (thread-safe, atomic write) |

---

## 📦 Prérequis

| Outil | Version minimale | Lien |
|-------|-----------------|------|
| **Python** | 3.11+ | [python.org](https://www.python.org/downloads/) — ✅ cocher *"Add to PATH"* |
| **Node.js** | 18 LTS+ | [nodejs.org](https://nodejs.org/) |
| **LLM local** | — | [Ollama](https://ollama.ai) / [LM Studio](https://lmstudio.ai) |
| *(Optionnel)* ClickHouse | — | Pour les agents analystes |
| *(Optionnel)* Oracle | — | Pour les agents analystes |

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

### Récréer les raccourcis manuellement

```cmd
cscript create_shortcut.vbs
```

---

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

Naviguez vers **Config LLM** dans la sidebar et renseignez :

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
URL  : http://localhost:11434/v1
Clé  : ollama
```

**LM Studio**
```
URL  : http://localhost:1234/v1
Clé  : lm-studio
```

**vLLM**
```
URL  : http://localhost:8000/v1
Clé  : (quelconque)
```

**OpenAI**
```
URL  : https://api.openai.com/v1
Clé  : sk-xxxxxxxxxxxx
```

---

## 🤖 Agents disponibles

### 🎯 Orchestrateur

Agent central qui décompose, délègue, synchronise et synthétise.

**Capacités LangGraph :**
- Nœud `planner` : analyse l'intention et génère un backlog JSON de sous-tâches
- Nœud `dispatcher` : sélectionne la prochaine tâche exécutable (gestion des dépendances)
- Nœud `worker` : exécute la tâche avec contexte des résultats précédents
- Nœud `corrector` : analyse l'erreur et produit des instructions corrigées
- Nœud `synthesizer` : compile tous les résultats en réponse Markdown structurée
- Nœud `human_feedback` : point d'interruption LangGraph (`interrupt_before`)

**Exemple de requête :**
```
Analyse les ventes du mois dernier, compare avec N-1,
identifie les 3 produits les plus performants et génère un résumé exécutif.
```

---

### 📊 Analyste ClickHouse

Agent SQL expert ClickHouse avec boucle de retry automatique.

**Directives SQL intégrées :**
- ❌ Jamais de `SELECT *` — colonnes explicites uniquement
- ✅ Filtres `WHERE` sur clés de partitionnement / `ORDER BY`
- ✅ Fonctions natives : `uniq()`, `any()`, `argMax()`, `topK()`
- ✅ Gestion temps : `toStartOfDay()`, `toYYYYMM()`, `BETWEEN`
- ✅ Jointures minimisées, dictionnaires privilégiés
- ✅ SQL formaté (MAJUSCULES, indentation)

**Exemple de requête :**
```
Montre-moi le top 10 des clients par chiffre d'affaires
sur les 30 derniers jours, groupé par région.
```

---

### 🔮 Analyste Oracle

Même architecture que l'analyste ClickHouse, adapté pour Oracle.

**Spécificités Oracle :**
- `TRUNC()`, `TO_DATE()`, `SYSDATE` pour les dates
- Fonctions analytiques `OVER PARTITION BY`
- Pagination avec `ROWNUM` / `FETCH FIRST n ROWS ONLY`
- Jointures avec hints si nécessaire

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
    sql += f"\nLIMIT {row_limit}"  # défaut: 1000

# 4. Plafonnement du LIMIT existant si > hard cap
# 5. Retour de l'erreur ClickHouse brute pour auto-correction
```

| Guardrail | Comportement |
|-----------|-------------|
| Opérations bloquées | DROP, TRUNCATE, DELETE, INSERT, UPDATE, CREATE, ALTER |
| Limite de lignes | Injectée automatiquement (configurable, défaut 1000) |
| Anti-injection | Validation regex + échappement client |
| Feedback d'erreur | Erreur DB complète retournée au LLM pour correction |
| Dry-run | Mode `EXPLAIN` disponible avant exécution réelle |

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
| `WS` | `/api/chat/ws/{agent_id}` | Chat WebSocket temps réel |
| `GET` | `/api/chat/sessions/{agent_id}` | Sessions d'un agent |
| `POST` | `/api/export/excel/query` | Exporter une requête en Excel |
| `POST` | `/api/export/excel/session/{id}` | Exporter une session en Excel |

### Exemple — Chat streaming

```bash
curl -X POST http://localhost:8000/api/chat/message \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "uuid-de-l-agent",
    "message": "Top 10 produits ce mois ?",
    "stream": true
  }'
```

Réponse (NDJSON stream) :
```json
{"type": "token", "content": "Je vais analyser..."}
{"type": "sql", "content": "SELECT product_id, sum(revenue)..."}
{"type": "query_result", "row_count": 10, "columns": [...], "rows": [...]}
{"type": "final", "content": "## Résultats\n\nVoici le top 10..."}
```

---

## 📁 Structure du projet

```
Python_Agent_03/
│
├── backend/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app, CORS, routing statique
│   ├── agents/
│   │   └── __init__.py
│   ├── database/
│   │   └── json_db.py             # DB JSON thread-safe (atomic write)
│   ├── graphs/
│   │   ├── state.py               # TypedDict états LangGraph
│   │   ├── llm_factory.py         # Build ChatOpenAI depuis config DB
│   │   ├── orchestrator_graph.py  # Graphe orchestrateur complet
│   │   └── analyst_graph.py       # Graphe analyste SQL avec retry
│   ├── models/
│   │   ├── agent.py               # AgentConfig, AgentCreate, AgentType
│   │   ├── connection.py          # ConnectionConfig, ConnectionType
│   │   ├── llm_config.py          # LLMConfig, LLMProvider
│   │   └── chat.py                # ChatMessage, ChatSession, ChatRequest
│   ├── routers/
│   │   ├── agents.py              # CRUD agents + templates
│   │   ├── connections.py         # CRUD connexions + test + schéma
│   │   ├── chat.py                # REST + WebSocket + sessions
│   │   ├── llm_config.py          # Config LLM + test + modèles
│   │   └── export.py              # Export Excel (requête / session)
│   └── tools/
│       ├── sql_clickhouse.py      # ClickHouse tool (guardrails, schema, audit)
│       └── sql_oracle.py          # Oracle tool (guardrails, schema)
│
├── frontend/
│   ├── vite.config.js             # Proxy /api → :8000
│   ├── package.json
│   └── src/
│       ├── App.jsx                # Router principal
│       ├── main.jsx
│       ├── services/
│       │   └── api.js             # Axios + streamChat() generator
│       ├── styles/
│       │   ├── global.css         # Tokens CSS, composants utilitaires
│       │   └── layout.css         # Sidebar, pages, cards
│       ├── components/
│       │   ├── Sidebar.jsx        # Navigation + liste agents
│       │   ├── AgentModal.jsx     # Formulaire création/édition agent
│       │   ├── ConnectionModal.jsx# Formulaire connexion DB
│       │   ├── SchemaExplorer.jsx # Explorateur de tables/colonnes
│       │   ├── DataTable.jsx      # Tableau paginé + export Excel
│       │   └── Toast.jsx          # Notifications toast
│       └── pages/
│           ├── ChatPage.jsx       # Chat streaming + sessions
│           ├── AgentsPage.jsx     # Gestion des agents
│           ├── ConnectionsPage.jsx# Gestion des connexions DB
│           └── LLMConfigPage.jsx  # Configuration LLM HTTP
│
├── data/                          # JSON DB locale (gitignored)
│   └── .gitkeep
│
├── requirements.txt
├── start.sh                       # Démarrage production
├── start_dev.sh                   # Démarrage développement
└── README.md
```

---

## 🗄️ Bases de données locales (JSON)

La plateforme stocke toutes ses configurations dans des fichiers JSON dans `data/` :

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

Deux modes d'export disponibles :

### Export d'une requête
- Feuille **Data** : résultats avec headers colorés, colonnes auto-dimensionnées
- Feuille **Metadata** : date d'export, nombre de lignes, SQL utilisé

### Export d'une session complète
- Feuille **Chat History** : tous les messages (timestamp, rôle, contenu)
- Feuilles **Query_N** : données de chaque requête exécutée dans la session

---

## 🔧 Développement

### Ajouter un nouvel agent

1. Créer un nœud dans `backend/graphs/` ou étendre `analyst_graph.py`
2. Ajouter le type dans `backend/models/agent.py` → `AgentType`
3. Ajouter le prompt par défaut dans `backend/routers/agents.py` → `DEFAULT_PROMPTS`
4. Mettre à jour `AGENT_TYPES` dans `frontend/src/components/AgentModal.jsx`

### Ajouter un nouveau type de connexion

1. Créer un tool dans `backend/tools/sql_<db>.py` (implémenter `execute`, `get_schema`, `test_connection`)
2. Ajouter le type dans `backend/models/connection.py` → `ConnectionType`
3. Enregistrer le tool dans `backend/routers/connections.py` → `_build_tool()`

---

## 📄 Licence

MIT — voir [LICENSE](LICENSE)
