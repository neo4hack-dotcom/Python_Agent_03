"""
Graphe LangGraph Analyste SQL — Architecture ReAct (analyst_graph.py)
=====================================================================
Workflow ReAct (Reasoning + Acting) pour interroger une base de données
ClickHouse ou Oracle à partir d'une question en langage naturel.

Architecture du graphe ReAct (build_analyst_graph) :
-----------------------------------------------------
                     ┌────────────────────┐
               START ─► agent_react_node  │  ← LLM + outils liés
                     └────────┬───────────┘
                              │ tool_calls présents ?
                    ┌─────────┴─────────┐
                    │ oui               │ non (ou limite iter.)
             ┌──────▼──────┐    ┌───────▼──────┐
             │tools_react  │    │    END        │
             │    _node    │    │(final_answer  │
             └──────┬──────┘    │  dans state)  │
                    │           └───────────────┘
                    └──────────► agent_react_node (boucle)

Cycle ReAct :
  1. L'agent (LLM) raisonne et choisit l'outil à appeler (tool_calls)
  2. Les outils s'exécutent (list_tables, get_schema, execute_query, check_query)
  3. Les résultats sont injectés comme ToolMessages dans le contexte
  4. L'agent raisonne à nouveau — soit appelle d'autres outils, soit répond
  5. Quand il n'y a plus de tool_calls, la réponse finale est dans l'AIMessage

Outils disponibles (SQLDatabaseToolkit-like) :
  - list_tables    : Lister toutes les tables de la base
  - get_schema     : Obtenir schéma (colonnes, types, ORDER BY, PARTITION)
  - execute_query  : Exécuter un SELECT (JSON structuré retourné)
  - check_query    : Valider la syntaxe avec EXPLAIN

Garde anti-boucle :
  iteration_count est incrémenté à chaque passage dans agent_react_node.
  Si iteration_count >= 8, on sort de la boucle même si tool_calls présents.

Backward compatibility (pour l'orchestrateur) :
  Les anciennes fonctions analyst_node, sql_tool_node, synthesizer_node,
  error_node sont CONSERVÉES et exportées. L'orchestrateur les appelle
  directement dans _run_analyst_subtask() sans passer par le graphe compilé.
"""
import json
import logging
from typing import Any, Dict, Literal, Optional, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode

from .state import AnalystState
from .llm_factory import build_llm
from backend.tools.sql_clickhouse import ClickHouseSQLTool
from backend.tools.sql_oracle import OracleSQLTool
from backend.database import db, COLL_CONNECTIONS, COLL_AGENTS

logger = logging.getLogger(__name__)

# ── System Prompts (legacy nodes + ReAct) ─────────────────────────────────────

CLICKHOUSE_ANALYST_SYSTEM = """You are a senior ClickHouse data analyst. Your expertise:

## SQL Directives
- NEVER use SELECT *. Always name explicit columns.
- Apply WHERE filters on partition keys or primary keys first.
- Use ClickHouse native functions: uniq() instead of COUNT(DISTINCT), any(), argMax(), topK().
- Use toStartOfDay(), toStartOfHour(), toYYYYMM() for time aggregations.
- Avoid JOINs when possible. Use IN (SELECT ...) for subqueries or Dictionaries.
- Always format SQL: UPPERCASE keywords, proper indentation.
- Add LIMIT clause (max rows per your configuration).

## Output Format
1. Brief strategy explanation (1-2 sentences)
2. Optimized SQL block
3. Performance note (e.g., "Uses primary key — will be fast")

{schema_context}

If you receive a database error, analyze it carefully and fix the SQL.
Respond with ONLY the SQL query (no markdown fences) when asked for SQL.
"""

ORACLE_ANALYST_SYSTEM = """You are a senior Oracle database analyst. Your expertise:

## SQL Directives
- NEVER use SELECT *. Always name explicit columns.
- Use proper Oracle date functions: TRUNC(), TO_DATE(), SYSDATE.
- Use analytic functions (OVER PARTITION BY) for window calculations.
- Avoid full table scans — use indexed columns in WHERE clauses.
- Use bind variables style in explanations but write literal values in SQL.
- Format SQL: UPPERCASE keywords, proper indentation.

{schema_context}

If you receive a database error, analyze it and fix the SQL.
Respond with ONLY the SQL query (no markdown fences) when asked for SQL.
"""

SYNTHESIZER_SYSTEM = """You are a data analyst communicator.
Given a SQL query result and the original question, provide a structured Markdown response with these sections:

1. A clear narrative answer to the question
2. Key insights from the data
3. The SQL query used (in a ```sql code block)
4. Any caveats or limitations

Then ALWAYS end your response with these two sections:

## 🔢 Actions Effectuées
Numbered list of every concrete action taken to answer this question (e.g. "1. Listed available tables", "2. Retrieved schema for table X", "3. Executed SQL query", "4. Synthesized results").

## 🎯 Score de Confiance
A confidence score from 0 to 100 for the accuracy and completeness of this answer, with a one-line justification.
Format: **Score : XX/100** — <reason>

Format the full response in clean Markdown. Write in French unless the question was in English."""

# ReAct system prompt — instructs the LLM to use tools in the right order
REACT_SYSTEM_CLICKHOUSE = """You are a senior ClickHouse data analyst using tools to answer questions.

## Workflow (always follow this order):
1. Call `list_tables` to see available tables
2. Call `get_schema` with relevant table names to understand columns and ORDER BY keys
3. Write optimized SQL and call `execute_query`
4. If the query fails, read the error, fix the SQL, and retry with `execute_query`
5. When you have the data, produce a comprehensive Markdown analysis

## ClickHouse SQL Rules (enforce strictly):
- NEVER SELECT * — always explicit columns
- Use `uniqCombined(col)` instead of `COUNT(DISTINCT col)` — 3-10x faster
- ALWAYS filter on ORDER BY (sorting_key) columns in WHERE clause
- Default LIMIT 100 unless more rows are truly needed
- Time functions: `toStartOfDay(ts)`, `toStartOfHour(ts)`, `toYYYYMM(ts)`
- Aggregations: `argMax(val, ts)`, `topK(10)(col)`, `any(col)`
- Avoid JOINs; prefer `IN (SELECT ...)` subqueries
- Handle `Array` columns with `arrayJoin()` or `arraySum()`, `arrayFilter()`

## Final Answer Format:
When done (no more tool calls needed), write a comprehensive Markdown response:
- Executive summary answering the question
- Key metrics with exact numbers
- SQL used (in a ```sql code block)
- Data table (from execute_query results)
- Insights and recommendations

Always end with:

## 🔢 Actions Effectuées
Numbered list of every tool call and action taken during this session (e.g. "1. Called list_tables", "2. Retrieved schema for table X", "3. Executed SQL query (N rows returned)", "4. Synthesized results").

## 🎯 Score de Confiance
**Score : XX/100** — <one-line justification based on data quality and query success>

{schema_context}
{custom_prompt}"""

REACT_SYSTEM_ORACLE = """You are a senior Oracle database analyst using tools to answer questions.

## Workflow (always follow this order):
1. Call `list_tables` to see available tables
2. Call `get_schema` with relevant table names to understand column types
3. Write optimized SQL and call `execute_query`
4. If the query fails, read the error, fix the SQL, and retry
5. When you have the data, produce a comprehensive Markdown analysis

## Oracle SQL Rules:
- NEVER SELECT * — always explicit columns
- Use indexed columns in WHERE clauses (avoid full table scans)
- Date functions: TRUNC(date_col), TO_DATE(..., 'YYYY-MM-DD'), SYSDATE
- Analytic: ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...)
- Pagination: FETCH FIRST n ROWS ONLY
- String: SUBSTR(), NVL(), TO_CHAR(), DECODE()

## Final Answer Format:
When done, write a comprehensive Markdown response with:
- Executive summary, key metrics, SQL used, data table, insights.

Always end with:

## 🔢 Actions Effectuées
Numbered list of every tool call and action taken during this session.

## 🎯 Score de Confiance
**Score : XX/100** — <one-line justification based on data quality and query success>

{schema_context}
{custom_prompt}"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_sql_tool(agent_id: str) -> Optional[Any]:
    """
    Instancie l'outil SQL approprié (ClickHouse ou Oracle) pour un agent donné.

    Chaîne de résolution :
      agent_id → COLL_AGENTS[agent_id].connection_id
               → COLL_CONNECTIONS[connection_id]  (host, port, user, password…)
               → ClickHouseSQLTool ou OracleSQLTool(conn_cfg, row_limit)

    Returns:
        L'outil SQL instancié, ou None si l'agent/connexion est introuvable.
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
    row_limit = agent_cfg.get("row_limit", 1000)
    conn_type = conn_cfg.get("type", "clickhouse")
    if conn_type == "clickhouse":
        return ClickHouseSQLTool(conn_cfg, row_limit=row_limit)
    elif conn_type == "oracle":
        return OracleSQLTool(conn_cfg, row_limit=row_limit)
    return None


def _get_conn_type(agent_id: str) -> str:
    """Retourne le type de connexion ('clickhouse' ou 'oracle') de l'agent."""
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
    conn_id = agent_cfg.get("connection_id")
    if not conn_id:
        return "clickhouse"
    conn_cfg = db.get(COLL_CONNECTIONS, conn_id) or {}
    return conn_cfg.get("type", "clickhouse")


def _get_agent_system_prompt(agent_id: str, schema_context: str = "") -> str:
    """
    Construit le system prompt complet pour le nœud analyst (legacy).

    Combine le template de base, le contexte de schéma, et le prompt
    personnalisé de l'agent. Utilisé par les nœuds legacy analyst_node.
    """
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
    conn_id = agent_cfg.get("connection_id")
    conn_cfg = db.get(COLL_CONNECTIONS, conn_id) if conn_id else {}
    conn_type = (conn_cfg or {}).get("type", "clickhouse")
    custom_prompt = agent_cfg.get("system_prompt", "")
    schema_block = f"\n## Available Schema\n{schema_context}" if schema_context else ""
    if conn_type == "oracle":
        base = ORACLE_ANALYST_SYSTEM.format(schema_context=schema_block)
    else:
        base = CLICKHOUSE_ANALYST_SYSTEM.format(schema_context=schema_block)
    return f"{base}\n\n{custom_prompt}".strip()


def _build_react_tools(agent_id: str) -> List:
    """
    Construit la liste des outils LangChain pour le cycle ReAct.

    Résout la connexion de l'agent et crée les outils appropriés
    (ClickHouse ou Oracle) en tenant compte du toolkit configuré
    pour l'agent (si toolkit_id est défini).

    Returns:
        Liste de fonctions @tool, ou liste vide si pas de connexion.
    """
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
    conn_id = agent_cfg.get("connection_id")
    if not conn_id:
        return []
    conn_cfg = db.get(COLL_CONNECTIONS, conn_id)
    if not conn_cfg:
        return []

    conn_type = conn_cfg.get("type", "clickhouse")
    row_limit = agent_cfg.get("row_limit", 100)

    # Résoudre le toolkit (enable/disable + overrides)
    toolkit_id = agent_cfg.get("toolkit_id")
    enabled_tools = None
    description_overrides = {}
    if toolkit_id:
        from backend.database import COLL_TOOLKITS
        toolkit_cfg = db.get(COLL_TOOLKITS, toolkit_id)
        if toolkit_cfg:
            enabled_tools = [
                t["name"] for t in toolkit_cfg.get("tools", [])
                if t.get("enabled", True)
            ]
            description_overrides = {
                t["name"]: t["description"]
                for t in toolkit_cfg.get("tools", [])
                if t.get("description")
            }

    if conn_type == "clickhouse":
        from backend.tools.langchain_clickhouse_tools import make_clickhouse_tools
        sql_tool = ClickHouseSQLTool(conn_cfg, row_limit=row_limit)
        return make_clickhouse_tools(sql_tool, enabled_tools, description_overrides)
    elif conn_type == "oracle":
        from backend.tools.langchain_oracle_tools import make_oracle_tools
        sql_tool = OracleSQLTool(conn_cfg, row_limit=row_limit)
        return make_oracle_tools(sql_tool, enabled_tools, description_overrides)
    return []


def _build_react_system_prompt(agent_id: str, schema_context: str = "") -> str:
    """Construit le system prompt ReAct avec les directives tools + schéma + prompt custom."""
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
    conn_id = agent_cfg.get("connection_id")
    conn_cfg = db.get(COLL_CONNECTIONS, conn_id) if conn_id else {}
    conn_type = (conn_cfg or {}).get("type", "clickhouse")
    custom_prompt = agent_cfg.get("system_prompt", "")
    schema_block = f"\n## Available Schema (pre-loaded)\n{schema_context}" if schema_context else ""
    template = REACT_SYSTEM_ORACLE if conn_type == "oracle" else REACT_SYSTEM_CLICKHOUSE
    return template.format(schema_context=schema_block, custom_prompt=custom_prompt).strip()


# ── Nœuds ReAct (graphe compilé) ──────────────────────────────────────────────

def agent_react_node(state: AnalystState) -> Dict[str, Any]:
    """
    Nœud agent ReAct — Raisonnement et sélection d'outils.

    Rôle :
        Interroge le LLM avec les outils liés (bind_tools). Le LLM décide :
        - Appeler un ou plusieurs outils (tool_calls dans l'AIMessage)
        - Répondre directement (pas de tool_calls = réponse finale)

    Contexte LLM envoyé :
        [SystemMessage(react_prompt + schéma)]
        + [HumanMessage(question)] au premier appel
        + historique complet (HumanMessage + AIMessage + ToolMessages) aux suivants

    Quand final_answer est set :
        Quand le LLM ne génère plus de tool_calls (a obtenu les données nécessaires),
        son message content devient la réponse finale narrative.

    Garde anti-boucle :
        iteration_count est incrémenté à chaque appel. Si >= 8, la réponse
        actuelle est forcée comme finale (même si tool_calls présents).
    """
    agent_id = state["agent_id"]
    llm = build_llm()

    # Construire les outils pour cet agent
    tools = _build_react_tools(agent_id)

    # Binder les outils au LLM si disponibles
    if tools:
        llm = llm.bind_tools(tools)

    # Construire le system prompt ReAct
    system_prompt = _build_react_system_prompt(
        agent_id, state.get("schema_context", "")
    )

    # Construire l'historique de messages
    history = list(state.get("messages", []))
    if not history:
        # Premier appel : initialiser avec la question
        history = [HumanMessage(content=state["user_question"])]

    full_messages = [SystemMessage(content=system_prompt)] + history

    # Appel LLM
    response = llm.invoke(full_messages)

    iteration = state.get("iteration_count", 0) + 1
    updates: Dict[str, Any] = {
        "messages": [response],
        "iteration_count": iteration,
    }

    # Si pas de tool_calls OU limite atteinte → c'est la réponse finale
    has_tool_calls = bool(getattr(response, "tool_calls", None))
    if not has_tool_calls or iteration >= 8:
        updates["final_answer"] = response.content

    return updates


def tools_react_node(state: AnalystState) -> Dict[str, Any]:
    """
    Nœud outils ReAct — Exécution des appels d'outils.

    Rôle :
        Exécute tous les tool_calls de l'AIMessage précédent via ToolNode.
        Les résultats sont ajoutés comme ToolMessages dans l'état.

    Extraction des données SQL :
        Après exécution, les ToolMessages issus de `execute_query` sont
        parsés pour extraire le SQL exécuté et le résultat structuré.
        Ces données sont stockées dans `generated_sql` et `query_result`
        pour compatibilité avec le streaming SSE de chat.py.

    Gestion des erreurs :
        Si aucun outil n'est disponible (pas de connexion DB), retourne
        un message d'erreur formaté comme ToolMessage.
    """
    agent_id = state["agent_id"]
    tools = _build_react_tools(agent_id)

    if not tools:
        # Pas de connexion DB — fabriquer un ToolMessage d'erreur
        last_msg = state["messages"][-1] if state.get("messages") else None
        tool_call_id = "no_connection"
        if last_msg and hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            tool_call_id = last_msg.tool_calls[0].get("id", "no_connection")
        return {
            "messages": [
                ToolMessage(
                    content="No database connection is configured for this agent. Cannot execute SQL tools.",
                    tool_call_id=tool_call_id,
                )
            ]
        }

    # Créer un ToolNode dynamique avec les outils de cet agent
    tool_node = ToolNode(tools)
    result = tool_node.invoke(state)

    # Post-traitement : extraire SQL + résultats des ToolMessages execute_query
    updates = dict(result)  # contient {"messages": [ToolMessage, ...]}
    for msg in result.get("messages", []):
        if not isinstance(msg, ToolMessage):
            continue
        if msg.name != "execute_query":
            continue
        try:
            data = json.loads(msg.content)
            if data.get("success"):
                updates["generated_sql"] = data.get("sql_executed", "")
                # Reconstruire un query_result compatible avec chat.py
                updates["query_result"] = {
                    "success": True,
                    "columns": data.get("columns", []),
                    "rows": data.get("rows", []),
                    "row_count": data.get("row_count", 0),
                    "markdown_table": data.get("markdown_table", ""),
                    "sql_executed": data.get("sql_executed", ""),
                    "warning": data.get("warning"),
                }
        except (json.JSONDecodeError, AttributeError):
            pass

    return updates


# ── Routing ReAct ──────────────────────────────────────────────────────────────

def route_react(state: AnalystState) -> Literal["tools", "__end__"]:
    """
    Décide la prochaine étape après agent_react_node.

    Retourne "tools" si l'AIMessage contient des tool_calls ET que la
    limite d'itérations n'est pas atteinte.
    Retourne END sinon (réponse finale déjà dans final_answer).
    """
    msgs = state.get("messages", [])
    if not msgs:
        return END
    last = msgs[-1]
    has_tool_calls = bool(getattr(last, "tool_calls", None))
    iteration = state.get("iteration_count", 0)
    if has_tool_calls and iteration < 8:
        return "tools"
    return END


# ── Nœuds legacy (conservés pour l'orchestrateur) ─────────────────────────────
# Ces fonctions sont importées directement par orchestrator_graph.py dans
# _run_analyst_subtask(). Elles constituent le pipeline "linéaire" original :
#   analyst_node → sql_tool_node → synthesizer_node (avec retry)

def analyst_node(state: AnalystState) -> Dict[str, Any]:
    """
    Nœud 1 — Génération (ou correction) du SQL (pipeline legacy).

    Utilisé par l'orchestrateur dans _run_analyst_subtask().
    Génère ou corrige le SQL à partir de la question et du schéma.
    En mode retry : reçoit l'erreur précédente et corrige le SQL.
    """
    llm = build_llm()
    system_prompt = _get_agent_system_prompt(
        state["agent_id"], state.get("schema_context", "")
    )
    history = list(state.get("messages", []))
    if not history:
        history = [HumanMessage(content=state["user_question"])]
    if state.get("last_error"):
        history.append(
            HumanMessage(
                content=f"The previous SQL failed with this error:\n```\n{state['last_error']}\n```\n\n"
                f"SQL that failed:\n```sql\n{state.get('generated_sql', '')}\n```\n\n"
                "Please analyze the error and write a corrected SQL query."
            )
        )
    full_messages = [SystemMessage(content=system_prompt)] + history
    response = llm.invoke(full_messages)
    sql = response.content.strip()
    # Nettoyage des balises Markdown
    if "```sql" in sql:
        sql = sql.split("```sql")[1].split("```")[0].strip()
    elif "```" in sql:
        sql = sql.split("```")[1].split("```")[0].strip()
    return {
        "generated_sql": sql,
        "messages": [AIMessage(content=f"Generated SQL:\n```sql\n{sql}\n```")],
    }


def sql_tool_node(state: AnalystState) -> Dict[str, Any]:
    """
    Nœud 2 — Exécution SQL sur la base de données réelle (pipeline legacy).

    Utilisé par l'orchestrateur dans _run_analyst_subtask().
    Exécute `state["generated_sql"]` et gère les erreurs / retry.
    """
    sql = state.get("generated_sql", "")
    if not sql:
        return {
            "query_result": {"success": False, "error": "No SQL was generated."},
            "last_error": "No SQL was generated.",
        }
    tool = _get_sql_tool(state["agent_id"])
    if not tool:
        return {
            "query_result": {
                "success": False,
                "error": "No database connection configured for this agent.",
            },
            "last_error": "No database connection configured.",
        }
    result = tool.execute(sql)
    error = result.get("error") if not result.get("success") else None
    return {
        "query_result": result,
        "last_error": error,
        "retry_count": state.get("retry_count", 0) + (1 if error else 0),
        "messages": [
            AIMessage(
                content=f"Query executed. Rows: {result.get('row_count', 0)}"
                if result.get("success")
                else f"Query failed: {error}"
            )
        ],
    }


def synthesizer_node(state: AnalystState) -> Dict[str, Any]:
    """
    Nœud 3 — Synthèse narrative du résultat SQL (pipeline legacy).

    Utilisé par l'orchestrateur dans _run_analyst_subtask().
    Transforme le résultat brut en réponse Markdown narrative.
    """
    llm = build_llm()
    result = state.get("query_result", {})
    result_summary = (
        result.get("markdown_table", "No data")
        if result.get("success")
        else f"Query failed: {result.get('error')}"
    )
    messages = [
        SystemMessage(content=SYNTHESIZER_SYSTEM),
        HumanMessage(
            content=f"Question: {state['user_question']}\n\n"
            f"SQL used:\n```sql\n{state.get('generated_sql', '')}\n```\n\n"
            f"Result:\n{result_summary}\n\n"
            + (f"⚠️ Warning: {result.get('warning')}" if result.get("warning") else "")
        ),
    ]
    response = llm.invoke(messages)
    return {
        "final_answer": response.content,
        "messages": [AIMessage(content=response.content)],
    }


def error_node(state: AnalystState) -> Dict[str, Any]:
    """
    Nœud terminal — Gestion de l'épuisement des tentatives (pipeline legacy).

    Utilisé par l'orchestrateur quand retry_count >= max_retries.
    """
    return {
        "final_answer": (
            f"❌ Unable to complete the analysis after {state.get('retry_count', 0)} attempts.\n\n"
            f"**Last error:** {state.get('last_error', 'Unknown')}\n\n"
            f"**Last SQL attempted:**\n```sql\n{state.get('generated_sql', '')}\n```\n\n"
            "Please check the database connection and schema, then retry."
        ),
        "messages": [AIMessage(content="Max retries exceeded.")],
    }


def route_after_tool(
    state: AnalystState,
) -> Literal["analyst", "synthesizer", "error_handler"]:
    """
    Routage après sql_tool_node (pipeline legacy — utilisé par l'orchestrateur).
    """
    result = state.get("query_result", {})
    max_retries = state.get("max_retries", 3)
    retry_count = state.get("retry_count", 0)
    if result.get("success"):
        return "synthesizer"
    if retry_count >= max_retries:
        return "error_handler"
    return "analyst"


# ── Construction du graphe LangGraph ReAct ────────────────────────────────────

def build_analyst_graph():
    """
    Assemble et compile le graphe LangGraph analyste SQL en mode ReAct.

    Architecture ReAct compilée :
        START → agent_react → [tools_react → agent_react (boucle)] → END

    Avantages du ReAct vs pipeline linéaire :
        - Le LLM explore dynamiquement le schéma avant d'écrire le SQL
        - Correction automatique : si execute_query échoue, le LLM lit l'erreur
          JSON et réessaie avec du SQL corrigé sans nœud dédié
        - Plus flexible : le LLM peut appeler list_tables puis get_schema
          puis execute_query dans l'ordre qu'il juge optimal

    Nœuds legacy (analyst_node, sql_tool_node, synthesizer_node) :
        Exportés séparément pour usage direct par l'orchestrateur dans
        _run_analyst_subtask(). Non inclus dans ce graphe compilé.

    Returns:
        Graphe LangGraph compilé avec MemorySaver, prêt à être invoqué.
    """
    builder = StateGraph(AnalystState)

    # Nœuds ReAct
    builder.add_node("agent", agent_react_node)   # LLM + outils liés
    builder.add_node("tools", tools_react_node)   # ToolNode dynamique

    # Flux de base : START → agent
    builder.add_edge(START, "agent")

    # Routage conditionnel depuis agent : tools ou END
    builder.add_conditional_edges(
        "agent",
        route_react,
        {
            "tools": "tools",   # Le LLM veut utiliser un outil
            END: END,           # Réponse finale → sortie
        },
    )

    # Boucle : après exécution des outils, retour à l'agent
    builder.add_edge("tools", "agent")

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)
