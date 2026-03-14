"""
Graphe LangGraph Analyste SQL (analyst_graph.py)
=================================================
Workflow spécialisé pour interroger une base de données ClickHouse ou Oracle
à partir d'une question en langage naturel.

Architecture du graphe :
------------------------
                    ┌─────────────┐
              START ─► analyst_node │  ← génère ou corrige le SQL
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │sql_tool_node│  ← exécute le SQL sur la vraie DB
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │ succès     │ erreur      │ max retries atteints
       ┌──────▼──────┐    │        ┌────▼──────┐
       │synthesizer  │    └────────► analyst   │  (retry loop)
       │    _node    │    (retry)  │   _node   │
       └──────┬──────┘             └───────────┘
              │ max retries                │
              │                    ┌───────▼──────┐
              │                    │  error_node   │
              │                    └───────┬───────┘
              │                            │
              └──────────────► END ◄───────┘

Retry automatique :
  Quand `sql_tool_node` retourne une erreur, le message d'erreur ET le SQL
  fautif sont injectés dans le prompt du prochain appel à `analyst_node`,
  permettant au LLM de diagnostiquer et corriger la requête automatiquement.
  Jusqu'à `max_retries` tentatives (défaut : 3), puis `error_node`.

Utilisation :
  - Mode standalone : endpoint POST /api/agents/{agent_id}/chat
  - Mode délégué : appelé par l'orchestrateur via _run_analyst_subtask()
"""
import json
import logging
from typing import Any, Dict, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from .state import AnalystState
from .llm_factory import build_llm
from backend.tools.sql_clickhouse import ClickHouseSQLTool
from backend.tools.sql_oracle import OracleSQLTool
from backend.database import db, COLL_CONNECTIONS, COLL_AGENTS

logger = logging.getLogger(__name__)

# ── System Prompts ────────────────────────────────────────────────────────────
# Ces prompts définissent le comportement du nœud `analyst_node` selon le type
# de base de données. Ils sont injectés en tant que SystemMessage (premier
# message du contexte LLM) à chaque invocation.
#
# La variable `{schema_context}` est remplacée dynamiquement par les informations
# de schéma (liste de tables, colonnes, clés de tri) avant l'invocation du LLM.

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
Given a SQL query result and the original question, provide:
1. A clear narrative answer to the question
2. Key insights from the data
3. The SQL query used (in a code block)
4. Any caveats or limitations

Format your response in clean Markdown."""


# ── Helpers : résolution agent → outil SQL ───────────────────────────────────

def _get_sql_tool(agent_id: str) -> Optional[Any]:
    """
    Instancie l'outil SQL approprié (ClickHouse ou Oracle) pour un agent donné.

    Chaîne de résolution :
      agent_id → COLL_AGENTS[agent_id].connection_id
               → COLL_CONNECTIONS[connection_id]  (host, port, user, password…)
               → ClickHouseSQLTool ou OracleSQLTool(conn_cfg, row_limit)

    Le `row_limit` est configuré par agent (défaut : 1000) pour éviter de
    retourner des millions de lignes dans le contexte du LLM.

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


def _get_agent_system_prompt(agent_id: str, schema_context: str = "") -> str:
    """
    Construit le system prompt complet pour le nœud analyst.

    Combine trois sources :
      1. Le template de base (CLICKHOUSE_ANALYST_SYSTEM ou ORACLE_ANALYST_SYSTEM)
         selon le type de connexion associée à l'agent.
      2. Le contexte de schéma (`schema_context`) injecté dans le placeholder
         `{schema_context}` du template — listes de tables et colonnes réelles.
      3. Le prompt personnalisé de l'agent (`agent_cfg.system_prompt`) ajouté
         à la fin, permettant à chaque agent d'avoir des instructions métier
         spécifiques (ex : "Focus on the orders table", "Always filter by tenant_id=42").

    Args:
        agent_id: ID de l'agent dans COLL_AGENTS.
        schema_context: Chaîne décrivant le schéma, ou "" si non disponible.

    Returns:
        Le system prompt complet prêt à être utilisé comme SystemMessage.
    """
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
    conn_id = agent_cfg.get("connection_id")
    conn_cfg = db.get(COLL_CONNECTIONS, conn_id) if conn_id else {}
    conn_type = (conn_cfg or {}).get("type", "clickhouse")

    # Encapsule le schéma dans une section Markdown lisible par le LLM
    custom_prompt = agent_cfg.get("system_prompt", "")
    schema_block = f"\n## Available Schema\n{schema_context}" if schema_context else ""

    if conn_type == "oracle":
        base = ORACLE_ANALYST_SYSTEM.format(schema_context=schema_block)
    else:
        base = CLICKHOUSE_ANALYST_SYSTEM.format(schema_context=schema_block)

    return f"{base}\n\n{custom_prompt}".strip()


# ── Nœuds du graphe ───────────────────────────────────────────────────────────

def analyst_node(state: AnalystState) -> Dict[str, Any]:
    """
    Nœud 1 — Génération (ou correction) du SQL.

    Rôle :
        Interroge le LLM pour produire une requête SQL correspondant à la
        question de l'utilisateur. En mode retry, il reçoit l'erreur de la
        tentative précédente et corrige le SQL.

    Flux de messages envoyés au LLM :
        [SystemMessage(system_prompt + schéma)]
        + [HumanMessage(question)] (premier appel)
        + [HumanMessage(erreur + SQL fautif)] (appels suivants en retry)

    Post-traitement :
        Le SQL retourné par le LLM peut être encapsulé dans des balises
        Markdown (```sql … ```). Ce nœud les supprime pour obtenir le SQL brut
        qui sera exécuté par `sql_tool_node`.

    Modifications de l'état :
        - `generated_sql` : le SQL nettoyé prêt à l'exécution.
        - `messages` : ajout du message AIMessage avec le SQL généré.
    """
    llm = build_llm()

    # Construit le system prompt avec le schéma de la base (si disponible)
    system_prompt = _get_agent_system_prompt(
        state["agent_id"],
        state.get("schema_context", ""),
    )

    # Récupère l'historique des messages (question initiale + erreurs précédentes)
    history = list(state.get("messages", []))
    if not history:
        # Premier appel : seule la question de l'utilisateur est dans le contexte
        history = [HumanMessage(content=state["user_question"])]

    # En mode retry : injecte l'erreur et le SQL fautif pour que le LLM corrige
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

    # Nettoyage des balises Markdown éventuelles (le LLM peut les ajouter
    # malgré l'instruction "no markdown fences")
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
    Nœud 2 — Exécution SQL sur la base de données réelle.

    Rôle :
        Exécute `state["generated_sql"]` contre la base de données configurée
        pour l'agent via `_get_sql_tool()`. L'outil SQL applique automatiquement :
          - Validation de sécurité (uniquement SELECT/WITH/EXPLAIN autorisés)
          - Injection d'un LIMIT si absent (ou plafonnement si trop élevé)
          - Conversion des types Python en types JSON-safe (datetime → ISO string)
          - Formatage du résultat en tableau Markdown

    Résultats possibles :
        Succès → `query_result["success"] = True`, données dans `query_result["rows"]`
        Échec  → `query_result["success"] = False`, message dans `query_result["error"]`
                 `last_error` est renseigné → déclenche un retry via `route_after_tool`

    Modifications de l'état :
        - `query_result` : dict complet du résultat (voir AnalystState.query_result).
        - `last_error` : None si succès, message d'erreur sinon.
        - `retry_count` : incrémenté de 1 en cas d'erreur.
        - `messages` : résumé du résultat (nombre de lignes ou message d'erreur).
    """
    sql = state.get("generated_sql", "")
    if not sql:
        # Cas dégénéré : `analyst_node` n'a produit aucun SQL
        return {
            "query_result": {"success": False, "error": "No SQL was generated."},
            "last_error": "No SQL was generated.",
        }

    # Instancie l'outil SQL (ClickHouse ou Oracle) depuis la config de l'agent
    tool = _get_sql_tool(state["agent_id"])
    if not tool:
        return {
            "query_result": {
                "success": False,
                "error": "No database connection configured for this agent.",
            },
            "last_error": "No database connection configured.",
        }

    # Exécution réelle : appel HTTP vers la base de données
    result = tool.execute(sql)
    error = result.get("error") if not result.get("success") else None

    return {
        "query_result": result,
        "last_error": error,
        # Incrémente uniquement en cas d'erreur (pour le compteur de retry)
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
    Nœud 3 — Synthèse narrative du résultat SQL.

    Rôle :
        Transforme le résultat brut de la base de données (tableau de lignes)
        en une réponse narrative en Markdown, lisible par l'utilisateur.

        Le LLM reçoit :
          - La question originale de l'utilisateur
          - Le SQL exécuté
          - Le résultat formaté en tableau Markdown (ou le message d'erreur)
          - Un éventuel avertissement (ex : résultat tronqué à N lignes)

        Même en cas d'échec SQL (max retries atteints), ce nœud peut être
        appelé depuis `error_node` pour formater proprement le message d'erreur.

    Modifications de l'état :
        - `final_answer` : réponse Markdown complète retournée à l'utilisateur.
        - `messages` : le même contenu ajouté au fil de messages LangChain.
    """
    llm = build_llm()
    result = state.get("query_result", {})

    # Formate le résultat pour le LLM :
    # - Succès : tableau Markdown des données réelles
    # - Échec  : message d'erreur explicatif
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
            # Ajoute un avertissement si le résultat a été tronqué (LIMIT atteint)
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
    Nœud terminal — Gestion de l'épuisement des tentatives.

    Atteint quand `retry_count >= max_retries` après des échecs SQL répétés.
    Retourne un message d'erreur structuré incluant :
      - Le nombre de tentatives effectuées
      - La dernière erreur reçue de la base de données
      - Le dernier SQL tenté (pour faciliter le débogage)
      - Des conseils de résolution

    Ce nœud ne produit pas de réponse inventée — il documente honnêtement l'échec.
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


# ── Fonctions de routage (edges conditionnelles) ─────────────────────────────

def route_after_tool(
    state: AnalystState,
) -> Literal["analyst", "synthesizer", "error_handler"]:
    """
    Décide la prochaine étape après l'exécution SQL.

    Appelée par LangGraph après chaque exécution de `sql_tool_node`.
    Les trois chemins possibles :

      "analyst"      : la requête a échoué ET on n'a pas encore atteint la
                       limite de retry → retour à `analyst_node` pour corriger
                       le SQL (l'erreur est dans `state["last_error"]`).

      "synthesizer"  : la requête a réussi (`query_result["success"] == True`)
                       → passage à la synthèse narrative.

      "error_handler": la requête a échoué ET `retry_count >= max_retries`
                       → abandon avec message d'erreur formaté.

    Args:
        state: État courant du graphe analyste.

    Returns:
        Nom du prochain nœud (clé de la dict de routing dans `add_conditional_edges`).
    """
    result = state.get("query_result", {})
    max_retries = state.get("max_retries", 3)
    retry_count = state.get("retry_count", 0)

    if result.get("success"):
        return "synthesizer"

    if retry_count >= max_retries:
        return "error_handler"

    # Encore des tentatives disponibles : retour à l'analyste pour correction
    return "analyst"


# ── Construction du graphe LangGraph ─────────────────────────────────────────

def build_analyst_graph():
    """
    Assemble et compile le graphe LangGraph analyste SQL.

    Structure compilée :
        START → analyst → sql_tool → [synthesizer | analyst (retry) | error_handler] → END

    Checkpointing :
        Utilise `MemorySaver` comme checkpointer en mémoire. Cela permet à
        LangGraph de sauvegarder l'état après chaque nœud — utile pour :
          - Le débogage (inspection de l'état intermédiaire)
          - Les futures extensions (reprise après interruption)
        En production, on pourrait remplacer par SqliteSaver ou RedisSaver
        pour persister l'état entre redémarrages du serveur.

    Returns:
        Un graphe LangGraph compilé, prêt à être invoqué avec `.invoke(state)`.
    """
    builder = StateGraph(AnalystState)

    # Enregistrement des nœuds (fonctions Python → nœuds du graphe)
    builder.add_node("analyst", analyst_node)          # génération/correction SQL
    builder.add_node("sql_tool", sql_tool_node)        # exécution SQL réelle
    builder.add_node("synthesizer", synthesizer_node)  # synthèse narrative
    builder.add_node("error_handler", error_node)      # gestion épuisement retry

    # Edge fixe : entrée → génération SQL
    builder.add_edge(START, "analyst")
    # Edge fixe : génération SQL → exécution SQL
    builder.add_edge("analyst", "sql_tool")

    # Edge conditionnelle : après exécution SQL, choisir la suite selon succès/erreur
    builder.add_conditional_edges(
        "sql_tool",           # nœud source
        route_after_tool,     # fonction de décision
        {
            "analyst": "analyst",              # retry
            "synthesizer": "synthesizer",      # succès
            "error_handler": "error_handler",  # abandon
        },
    )

    # Edges finaux vers END
    builder.add_edge("synthesizer", END)
    builder.add_edge("error_handler", END)

    # Compilation avec checkpointer mémoire
    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    return graph
