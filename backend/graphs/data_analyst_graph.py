"""
Graphe LangGraph Analyste de Données (data_analyst_graph.py)
=============================================================
Agent expert en analyse de données, statistiques, profiling et business intelligence.

Contrairement à l'AnalystState (centré SQL), cet agent adopte une approche en
deux temps : il planifie d'abord l'analyse, puis l'exécute — avec ou sans DB.

Architecture du graphe :
------------------------
                    ┌──────────────┐
              START ─► planner_node  │  ← détermine le type d'analyse + SQL si besoin
                    └──────┬───────┘
                           │
            ┌──────────────┼──────────────────┐
            │ sql_queries  │                   │ pas de queries
            │ + connexion  │                   │ (ou pas de connexion DB)
     ┌──────▼──────────┐   │                   │
     │sql_executor_node│   │                   │
     └──────┬──────────┘   │                   │
            │              │                   │
            └──────────────┼───────────────────┘
                           │
                    ┌──────▼──────┐
                    │analyst_node │  ← analyse statistique, profiling, KPI, trends
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │synthesizer  │  ← narrative business + recommandations
                    │    _node    │
                    └──────┬──────┘
                           │
                          END

Capacités de l'agent :
  - Profiling : qualité des données, NULL rates, cardinalité, distributions
  - Statistiques : moyenne, médiane, écart-type, percentiles, corrélations, outliers
  - Tendances : évolution temporelle, MoM/YoY, saisonnalité, anomalies
  - KPIs : calcul et interprétation de métriques métier
  - Business : recommandations actionnables, benchmarks, comparaisons

Sans connexion DB : analyse la question avec le contexte de la conversation.
Avec connexion DB  : génère et exécute les requêtes SQL nécessaires, puis analyse.
"""
import json
import logging
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from .state import DataAnalystState
from .llm_factory import build_llm
from backend.tools.sql_clickhouse import ClickHouseSQLTool
from backend.tools.sql_oracle import OracleSQLTool
from backend.database import db, COLL_AGENTS, COLL_CONNECTIONS

logger = logging.getLogger(__name__)

# ── System Prompts ────────────────────────────────────────────────────────────

PLANNER_SYSTEM = """You are a senior data analyst and business intelligence expert.
Analyze the user's question and create a structured analysis plan.

## Your task
1. Identify the type of analysis needed:
   - "profiling"   : data quality, completeness, NULL rates, cardinality, value distributions
   - "statistical" : means, medians, std deviation, percentiles, correlations, outliers
   - "trends"      : time series, MoM/YoY growth rates, seasonality, anomalies
   - "kpi"         : business KPIs, performance metrics, target vs actual
   - "business"    : strategic insights, comparisons, recommendations, executive summary
   - "mixed"       : combination of multiple types (most thorough)

2. If a database connection is available, write the SQL queries to fetch the needed data.
   - Use specific, targeted queries (not SELECT *)
   - Plan separate queries for different dimensions of the analysis
   - Each query should answer one specific analytical question

3. Define the key metrics and KPIs to compute.

{schema_context}

Respond with a JSON object ONLY (no markdown fences):
{{
  "analysis_type": "statistical|profiling|trends|kpi|business|mixed",
  "approach": "Brief description of what you will analyze and how",
  "sql_queries": [
    {{
      "id": "q1",
      "description": "What this query answers",
      "sql": "SELECT col1, COUNT(*) as cnt FROM table GROUP BY col1 ORDER BY cnt DESC LIMIT 20"
    }}
  ],
  "metrics_to_compute": ["list of specific metrics/KPIs to calculate from the data"],
  "business_context": "What business question this analysis ultimately answers"
}}

If no DB connection is available, set sql_queries to [].
"""

ANALYST_SYSTEM = """You are a world-class data analyst combining deep statistical expertise with sharp business acumen.

Given the data and context, perform a comprehensive, rigorous analysis.

## Statistical Analysis
- Central tendency: mean, median, mode — and whether they diverge (skewness signal)
- Dispersion: std deviation, variance, IQR, min/max range
- Percentiles: P10, P25, P50, P75, P90, P95, P99
- Outliers: values beyond 2σ or 1.5×IQR — flag them explicitly
- Correlations: identify pairs of variables that move together

## Data Profiling (when data is available)
- Total row count and NULL counts per column
- Cardinality: unique value counts (low = categorical, high = continuous/ID)
- Top N values with their frequencies and percentages
- Date ranges: min/max dates, gaps, coverage
- Data quality issues: inconsistent formats, suspicious values

## Trend & Pattern Analysis (for time series data)
- Overall trend direction (growing/declining/stable)
- Growth rates: absolute and relative (MoM, YoY, WoW)
- Seasonality: day-of-week, monthly, quarterly patterns
- Anomalies: unexpected spikes, drops, or gaps

## KPI Computation
- Calculate every metric listed in the analysis plan
- Compare to typical baselines when possible
- Flag metrics that are significantly above or below expected ranges

Always back every statement with numbers from the data. Never invent figures.
Present intermediate calculations clearly so the reader can verify them.
"""

SYNTHESIZER_SYSTEM = """You are an expert business analyst and data storyteller.
Transform the technical analysis into a compelling, actionable executive report.

Structure your response EXACTLY as follows:

## 📋 Résumé Exécutif
2-3 sentences capturing the single most important finding and its business implication.

## 🔍 Insights Clés
- Bullet list of the top 5-7 data-driven findings
- Each bullet must include a specific number or metric
- Ordered from most to least impactful

## 📊 Analyse Détaillée
Full structured analysis with:
- Tables for comparative data
- Metrics with their values clearly stated
- Patterns and anomalies explained

## 💡 Recommandations Business
Numbered list of concrete, actionable recommendations.
Each recommendation should include: What to do → Why (data evidence) → Expected impact.

## ⚠️ Limites & Points de Vigilance
- Data quality issues discovered
- Caveats about interpretation
- What additional data would improve this analysis

## 🔢 Actions Effectuées
Numbered list of every concrete action taken during this analysis.
Include for each action which agent executed it (the agent name is provided in the context).
Format: "N. <action description> → **{agent_label}**"

## 🎯 Score de Confiance
A confidence score from 0 to 100 reflecting how complete, accurate and data-backed this analysis is.
Format: **Score : XX/100** — <one-line justification>

Format in clean Markdown. Be specific — always reference actual numbers from the analysis.
Write in French unless the question was in English.
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_sql_tool(agent_id: str) -> Optional[Any]:
    """
    Instancie l'outil SQL pour l'agent (ClickHouse ou Oracle).
    Retourne None si l'agent n'a pas de connexion DB configurée.
    """
    agent_cfg = db.get(COLL_AGENTS, agent_id)
    if not agent_cfg:
        return None
    conn_id = agent_cfg.get("connection_id")
    if not conn_id:
        return None
    conn_cfg = db.get(COLL_CONNECTIONS, conn_id)
    if not conn_cfg:
        return None
    # Limite plus élevée pour l'analyse (besoin de plus de données que pour une simple requête)
    row_limit = agent_cfg.get("row_limit", 5000)
    conn_type = conn_cfg.get("type", "clickhouse")
    if conn_type == "clickhouse":
        return ClickHouseSQLTool(conn_cfg, row_limit=row_limit)
    elif conn_type == "oracle":
        return OracleSQLTool(conn_cfg, row_limit=row_limit)
    return None


def _has_connection(agent_id: str) -> bool:
    """Vérifie si l'agent a une connexion DB valide configurée."""
    return _get_sql_tool(agent_id) is not None


def _auto_schema_context(agent_id: str, task_description: str = "") -> str:
    """
    Récupère la liste des tables et le schéma des tables mentionnées dans la tâche.
    Injecté dans le prompt du planner pour qu'il puisse écrire du SQL pertinent.
    """
    tool = _get_sql_tool(agent_id)
    if not tool:
        return ""

    try:
        all_tables: List[str] = tool.list_tables()
        all_tables = [t for t in all_tables if not t.startswith("ERROR:")]
    except Exception as e:
        logger.warning("Could not list tables: %s", e)
        return ""

    if not all_tables:
        return ""

    # Tables mentionnées dans la description de la tâche
    task_lower = task_description.lower()
    mentioned = [t for t in all_tables if t.lower() in task_lower]

    if not mentioned:
        return f"Available tables: {', '.join(all_tables[:50])}"

    schema_parts = []
    for table in mentioned[:5]:
        try:
            schema_result = tool.get_schema(table)
            if "error" not in schema_result and schema_result.get("columns"):
                cols = schema_result["columns"]
                col_lines = "\n".join(
                    f"  - {c['name']} ({c['type']})" + (f"  -- {c['comment']}" if c.get("comment") else "")
                    for c in cols
                )
                meta = schema_result.get("metadata", {})
                meta_info = ""
                if meta.get("sorting_key"):
                    meta_info += f"\n  Sorting key: {meta['sorting_key']}"
                if meta.get("partition_key"):
                    meta_info += f"\n  Partition key: {meta['partition_key']}"
                schema_parts.append(f"Table `{table}`:\n{col_lines}{meta_info}")
        except Exception as e:
            logger.warning("Could not get schema for %s: %s", table, e)

    if schema_parts:
        return "\n\n".join(schema_parts)
    return f"Available tables: {', '.join(all_tables[:50])}"


# ── Nœuds du graphe ───────────────────────────────────────────────────────────

def planner_node(state: DataAnalystState) -> Dict[str, Any]:
    """
    Nœud 1 — Planification de l'analyse.

    Rôle :
        Interprète la question de l'utilisateur et produit un plan d'analyse
        structuré (type d'analyse, métriques à calculer, requêtes SQL si pertinent).

    Injection du contexte de schéma :
        Le système prompt inclut le schéma des tables si l'agent a une connexion
        DB, ce qui permet au LLM de générer du SQL précis dès le premier essai.
        Un message indique aussi explicitement si une connexion DB est disponible
        ou non, pour guider le LLM dans sa décision d'écrire ou non du SQL.

    Parsing du JSON :
        La réponse est un JSON parsé. En cas d'échec (LLM n'a pas respecté le
        format), un plan de fallback est créé pour une analyse business générale.

    Modifications de l'état :
        - `analysis_plan` : plan parsé.
        - `sql_queries` : liste des requêtes SQL (vide si pas de connexion).
        - `messages` : résumé du plan.
    """
    llm = build_llm()

    has_db = _has_connection(state["agent_id"])
    schema_ctx = state.get("schema_context", "")
    schema_block = f"\n## Available Schema\n{schema_ctx}" if schema_ctx else ""

    # On remplace le placeholder {schema_context} dans le template
    system_content = PLANNER_SYSTEM.replace("{schema_context}", schema_block)
    # Indication explicite sur la disponibilité d'une connexion DB
    db_hint = (
        "\n\n✅ A database connection IS configured for this agent. "
        "Write SQL queries to fetch the data needed for analysis."
        if has_db
        else "\n\n⚠️ No database connection configured. "
             "Set sql_queries to [] and analyze based on the question and context."
    )
    system_content += db_hint

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=f"Analytical question: {state['user_question']}"),
    ]

    try:
        response = llm.invoke(messages)
        raw = response.content.strip()
        # Nettoyage des balises Markdown éventuelles
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        plan = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("Planner JSON parse failed: %s — using fallback plan", e)
        plan = {
            "analysis_type": "business",
            "approach": "Direct analysis based on available context and general expertise",
            "sql_queries": [],
            "metrics_to_compute": [],
            "business_context": state["user_question"],
        }

    sql_queries = plan.get("sql_queries", [])
    logger.info(
        "Data analyst plan: type=%s, sql_queries=%d",
        plan.get("analysis_type"), len(sql_queries)
    )

    return {
        "analysis_plan": plan,
        "sql_queries": sql_queries,
        "messages": [
            AIMessage(content=(
                f"Plan d'analyse créé : {plan.get('approach', '')}\n"
                f"Type : {plan.get('analysis_type', 'mixed')} | "
                f"Requêtes SQL : {len(sql_queries)}"
            ))
        ],
    }


def sql_executor_node(state: DataAnalystState) -> Dict[str, Any]:
    """
    Nœud 2 (optionnel) — Exécution des requêtes SQL planifiées.

    Atteint uniquement si `route_after_planner` détecte des requêtes SQL ET
    une connexion DB disponible. Exécute chaque requête indépendamment et
    collecte les résultats (succès ou erreur par requête).

    Différence avec sql_tool_node de l'analyst_graph :
        - Exécute plusieurs requêtes en séquence (multi-query)
        - Chaque résultat garde sa description métier pour le contexte
        - Les erreurs par requête ne bloquent pas les autres requêtes
        - Pas de retry : si une requête échoue, on continue avec les autres

    Modifications de l'état :
        - `data_results` : liste des résultats (un par requête SQL).
        - `messages` : résumé de l'exécution (N requêtes, M réussies).
    """
    sql_queries = state.get("sql_queries", [])
    if not sql_queries:
        return {
            "data_results": [],
            "messages": [AIMessage(content="Aucune requête SQL à exécuter.")],
        }

    tool = _get_sql_tool(state["agent_id"])
    if not tool:
        return {
            "data_results": [],
            "last_error": "Connexion DB non disponible.",
            "messages": [AIMessage(content="⚠️ Connexion base de données non disponible.")],
        }

    data_results = []
    for q in sql_queries:
        sql = q.get("sql", "").strip()
        if not sql:
            continue
        try:
            result = tool.execute(sql)
            data_results.append({
                "query_id": q.get("id", ""),
                "description": q.get("description", ""),
                "sql": sql,
                "success": result.get("success", False),
                "columns": result.get("columns", []),
                "rows": result.get("rows", []),
                "row_count": result.get("row_count", 0),
                "markdown_table": result.get("markdown_table", ""),
                "warning": result.get("warning"),
                "error": result.get("error"),
            })
            logger.info(
                "Query %s: %s rows", q.get("id"), result.get("row_count", 0)
                if result.get("success") else f"FAILED — {result.get('error')}"
            )
        except Exception as e:
            logger.error("SQL execution error for query %s: %s", q.get("id"), e)
            data_results.append({
                "query_id": q.get("id", ""),
                "description": q.get("description", ""),
                "sql": sql,
                "success": False,
                "error": str(e),
            })

    successful = sum(1 for r in data_results if r.get("success"))
    return {
        "data_results": data_results,
        "messages": [
            AIMessage(content=f"Exécution SQL : {len(data_results)} requêtes, {successful} réussies.")
        ],
    }


def analyst_node(state: DataAnalystState) -> Dict[str, Any]:
    """
    Nœud 3 — Analyse approfondie des données.

    Rôle :
        Effectue l'analyse réelle (statistique, profiling, tendances, KPIs)
        sur les données fetched par sql_executor_node, ou sur la base de la
        question seule si aucune donnée SQL n'est disponible.

    Construction du contexte de données :
        Les résultats SQL sont convertis en tableaux Markdown et regroupés
        par description métier. Si plusieurs requêtes ont été exécutées,
        chaque jeu de résultats est présenté avec son contexte (description
        de la requête + SQL utilisé + tableau de résultats).

    Analyses réalisées par le LLM :
        - Distribution et statistiques descriptives
        - Détection d'outliers et d'anomalies
        - Tendances et patterns
        - Calcul des KPIs listés dans le plan
        - Corrélations et relations entre variables

    Modifications de l'état :
        - `analysis_output` : analyse technique complète (input du synthesizer).
        - `messages` : contenu de l'analyse.
    """
    llm = build_llm()

    # Construction du contexte de données pour le LLM
    data_results = state.get("data_results") or []
    if data_results:
        parts = []
        for r in data_results:
            if r.get("success"):
                parts.append(
                    f"### {r.get('description', r.get('query_id', 'Dataset'))}\n"
                    f"**SQL :** `{r.get('sql', '')}`\n"
                    f"**Rows :** {r.get('row_count', 0)}"
                    + (f" ⚠️ {r.get('warning')}" if r.get("warning") else "")
                    + f"\n\n{r.get('markdown_table', '_No data_')}"
                )
            else:
                parts.append(
                    f"### {r.get('description', '')} — ❌ Erreur\n"
                    f"`{r.get('error', 'Unknown error')}`"
                )
        data_context = "\n\n---\n\n".join(parts)
    else:
        data_context = (
            "_Aucune donnée SQL disponible. "
            "Réalise l'analyse sur la base de la question et de tes connaissances._"
        )

    plan = state.get("analysis_plan") or {}
    metrics = ", ".join(plan.get("metrics_to_compute", [])) or "à déterminer selon les données"

    messages = [
        SystemMessage(content=ANALYST_SYSTEM),
        HumanMessage(
            content=(
                f"## Question\n{state['user_question']}\n\n"
                f"## Plan d'analyse\n"
                f"- Type : {plan.get('analysis_type', 'mixed')}\n"
                f"- Approche : {plan.get('approach', '')}\n"
                f"- Métriques à calculer : {metrics}\n"
                f"- Contexte métier : {plan.get('business_context', '')}\n\n"
                f"## Données\n{data_context}"
            )
        ),
    ]

    response = llm.invoke(messages)
    return {
        "analysis_output": response.content,
        "messages": [AIMessage(content=response.content)],
    }


def synthesizer_node(state: DataAnalystState) -> Dict[str, Any]:
    """
    Nœud 4 — Synthèse business et recommandations.

    Rôle :
        Transforme l'analyse technique produite par analyst_node en un rapport
        Markdown structuré et orienté business, lisible par un décideur.

        Le rapport comprend :
          - Résumé exécutif (2-3 phrases)
          - Insights clés avec métriques (bullet points)
          - Analyse détaillée avec tables
          - Recommandations actionnables (What/Why/Impact)
          - Limites et points de vigilance

    Modifications de l'état :
        - `final_answer` : rapport Markdown complet retourné à l'utilisateur.
        - `messages` : même contenu ajouté au fil de messages.
    """
    from backend.database import db, COLL_AGENTS
    llm = build_llm()
    agent_cfg = db.get(COLL_AGENTS, state.get("agent_id", "")) or {}
    agent_label = agent_cfg.get("name") or state.get("agent_id") or "Agent Data Analyst"

    system = SYNTHESIZER_SYSTEM.replace("{agent_label}", agent_label)
    messages = [
        SystemMessage(content=system),
        HumanMessage(
            content=(
                f"## Agent\n**{agent_label}**\n\n"
                f"## Question originale\n{state['user_question']}\n\n"
                f"## Analyse technique\n{state.get('analysis_output', '')}"
            )
        ),
    ]

    response = llm.invoke(messages)
    return {
        "final_answer": response.content,
        "messages": [AIMessage(content=response.content)],
    }


# ── Fonction de routage ────────────────────────────────────────────────────────

def route_after_planner(state: DataAnalystState) -> Literal["sql_executor", "analyst"]:
    """
    Décide si on doit fetcher des données SQL ou aller directement à l'analyse.

    Conditions pour passer par sql_executor :
      1. Le planner a produit au moins une requête SQL (`sql_queries` non vide)
      2. L'agent a une connexion DB valide configurée (`_has_connection`)

    Si l'une des deux conditions n'est pas remplie → analyse directe sans SQL.
    (ex : agent sans DB, question de business générale, calcul sur données du chat)
    """
    sql_queries = state.get("sql_queries") or []
    if sql_queries and _has_connection(state["agent_id"]):
        return "sql_executor"
    return "analyst"


# ── Construction du graphe LangGraph ─────────────────────────────────────────

def build_data_analyst_graph():
    """
    Assemble et compile le graphe LangGraph de l'analyste de données.

    Structure compilée :
        START → planner → [sql_executor →] analyst → synthesizer → END

    L'edge après `planner` est conditionnelle selon la présence de requêtes SQL
    et d'une connexion DB. Le nœud `sql_executor` est optionnel (bypass possible).
    Les deux chemins convergent vers `analyst`.

    Returns:
        Graphe LangGraph compilé, invocable avec `.invoke(state)` ou `.astream(state)`.
    """
    builder = StateGraph(DataAnalystState)

    # Enregistrement des nœuds
    builder.add_node("planner", planner_node)           # planification de l'analyse
    builder.add_node("sql_executor", sql_executor_node) # exécution SQL multi-requêtes
    builder.add_node("analyst", analyst_node)           # analyse statistique + KPI
    builder.add_node("synthesizer", synthesizer_node)   # narrative business

    # Edge fixe : entrée → planification
    builder.add_edge(START, "planner")

    # Edge conditionnelle : planification → SQL ou analyse directe
    builder.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "sql_executor": "sql_executor",
            "analyst": "analyst",
        },
    )

    # Edge fixe : SQL → analyse (après avoir fetché les données)
    builder.add_edge("sql_executor", "analyst")
    # Edge fixe : analyse → synthèse
    builder.add_edge("analyst", "synthesizer")
    # Edge fixe : synthèse → fin
    builder.add_edge("synthesizer", END)

    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)
    return graph
