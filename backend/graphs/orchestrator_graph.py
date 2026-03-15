"""
Graphe LangGraph Orchestrateur — Architecture ReAct (orchestrator_graph.py)
============================================================================
Workflow multi-agents suivant le pattern ReAct (Reasoning + Acting) :
le raisonneur LLM est appelé à chaque itération pour décider de la prochaine
action en s'appuyant sur toutes les observations accumulées.

Architecture du graphe (ReAct loop) :
--------------------------------------

                    ┌─────────────────┐
              START ─►  reasoner_node  │  ← Thought : raisonne sur l'état courant
                    └────────┬────────┘    et choisit la prochaine action
                             │
             ┌───────────────┼──────────────────────┐
             │ call_agent    │ human_validation       │ final_answer / cannot_fulfill
      ┌──────▼──────┐  ┌─────▼──────┐       ┌────────▼────────┐
      │ worker_node │  │human_feedback│      │ synthesizer_node │
      │  (Action)   │  │   _node     │       │  (Final Answer)  │
      └──────┬──────┘  └──────┬──────┘       └────────┬────────┘
             │  Observation   │                        │
             └────────────────┘                        │
             (retour à reasoner avec nouveau résultat)  │
                                              END ◄─────┘

Délégation réelle aux agents analystes :
  Quand une tâche a `agent_type = "clickhouse_analyst"` ou `"oracle_analyst"`,
  `worker_node` NE délègue PAS à un LLM générique. Il appelle directement le
  sous-pipeline analyst : analyst_node → sql_tool_node → synthesizer_node.
  Cela garantit l'exécution de vraies requêtes SQL et des résultats réels
  (sans placeholders ni données inventées).

Human-in-the-loop :
  Si une tâche porte `requires_human_approval: true`, le graphe s'interrompt
  sur `human_feedback_node` (via `interrupt_before` à la compilation).
  L'API peut alors renvoyer l'état courant au frontend, attendre la validation
  de l'utilisateur, puis reprendre l'exécution depuis ce point de contrôle
  (via le checkpointer LangGraph).
"""
import json
import logging
import uuid
from typing import Any, Dict, List, Literal, Optional, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from .state import OrchestratorState
from .llm_factory import build_llm
# Import des nœuds individuels du graphe analyste SQL pour appel direct
# (sans passer par le graphe compilé, afin de contrôler le retry loop ici)
from .analyst_graph import analyst_node, sql_tool_node, synthesizer_node as analyst_synthesizer_node
# Import des nœuds du graphe analyste de données pour délégation directe
from .data_analyst_graph import (
    planner_node as da_planner_node,
    sql_executor_node as da_sql_executor_node,
    analyst_node as da_analyst_node,
    synthesizer_node as da_synthesizer_node,
    _has_connection as da_has_connection,
    _auto_schema_context as da_auto_schema_context,
)
from backend.database import db, COLL_AGENTS, COLL_CONNECTIONS
from backend.tools.sql_clickhouse import ClickHouseSQLTool
from backend.tools.sql_oracle import OracleSQLTool

logger = logging.getLogger(__name__)

# ── System Prompts ────────────────────────────────────────────────────────────
# Les prompts définissent le comportement de chaque nœud LLM.
# Ils sont tous injectés en tant que SystemMessage (premier message du contexte).

REASONER_SYSTEM = """You are an expert orchestrator working in a ReAct (Reason + Act) loop.

At each step you receive:
- The user's original request
- All available specialist agents with their descriptions
- All prior observations (results from agents already called)

Your task: THINK about what has been done so far, then decide the single best NEXT action.

RULES:
- Read every agent's description carefully. Only call an agent if its description confirms it can handle the sub-task.
- Never hallucinate data — rely only on what specialist agents return.
- Never call the same agent with the exact same task description twice.
- If a prior task failed, you may retry once with a better description OR proceed to final_answer using available results.
- For PDF / rapport / report / document / synthesis requests: call report_writer LAST, after all analysis tasks are complete.
- For data queries: use the EXACT table/column names the user mentioned. If table names are unclear, use human_validation first.
- If the request requires capabilities not available in any listed agent, use cannot_fulfill immediately.

{agents_block}

Prior observations (results from agents already called):
{observations}

Current step: {step_num}

Respond ONLY with a valid JSON object. Choose exactly ONE action:

To call a specialist agent:
{{
  "thought": "<reasoning about what has been done and what the next best action is>",
  "action": "call_agent",
  "agent_type": "<type from agents_block>",
  "agent_id": "<id from agents_block, or null if none match>",
  "task_id": "step_{step_num}",
  "description": "<specific, actionable description with exact table/column/file names>"
}}

When all needed work is done and you can formulate a complete answer:
{{
  "thought": "<what was accomplished and why it fully answers the user's request>",
  "action": "final_answer"
}}

When you need clarification from the user before proceeding:
{{
  "thought": "<what is unclear or requires human input>",
  "action": "human_validation",
  "task_id": "human_{step_num}",
  "description": "<specific question for the user>"
}}

When the request requires capabilities not available in any listed agent:
{{
  "thought": "<what was needed but no agent can provide it>",
  "action": "cannot_fulfill",
  "reason": "<clear, honest explanation why this cannot be done with available agents>"
}}"""
# Note : {agents_block}, {observations} et {step_num} sont injectés dynamiquement
# par reasoner_node à chaque itération du loop ReAct.

ROUTER_SYSTEM = """You are a routing agent. Given the current task and available worker results,
decide the next action:
- "continue": proceed to next pending task
- "retry": the last task failed, retry with corrections
- "synthesize": all tasks done, generate final answer
- "human_input": must pause and ask the human for validation/input
- "end": nothing more to do

Respond ONLY with JSON: {"action": "<action>", "reason": "<brief reason>"}
"""
# Note : ROUTER_SYSTEM est défini mais non utilisé dans les nœuds actuels.
# Le routage est effectué directement par les fonctions Python `route_after_*`
# qui inspectent l'état, ce qui est plus déterministe et plus rapide qu'un LLM.

SYNTHESIZER_SYSTEM = """You are a synthesis expert. Compile all worker results into a coherent,
structured final answer for the user. Be comprehensive but concise.
Format your answer in Markdown with clear sections.

IMPORTANT: The worker results contain REAL data fetched from databases. Present this real data
accurately — do NOT replace data with placeholders or templates.

Always end your response with these two sections:

## 🔢 Actions Effectuées
Numbered list of every sub-task completed, based on the worker results provided.
For each action include: the task description AND which agent executed it (use the agent_name and agent_type fields from worker results).
Format each line as: "N. <task_description> → **<agent_name>** (<agent_type>)"

## 🎯 Score de Confiance
A global confidence score from 0 to 100 reflecting how reliably this multi-agent workflow answered the user's request.
Format: **Score : XX/100** — <one-line justification referencing tasks completed and agents involved>"""
# La mention "REAL data" est critique : sans elle, certains LLMs tendent à
# réécrire les données tabulaires avec des valeurs génériques.

CORRECTOR_SYSTEM = """You are a correction specialist. A sub-task failed.
Analyze the error and provide an improved, corrected version of the task instructions
that will help the worker agent succeed on the next attempt."""


# ── Helpers : délégation aux agents analystes ─────────────────────────────────

def _find_analyst_agent(agent_type: str, agent_id_hint: Optional[str] = None) -> Optional[str]:
    """
    Trouve l'ID du meilleur agent analyste actif correspondant au type demandé.

    Stratégie de résolution (priorité décroissante) :
      1. Si `agent_id_hint` est fourni (par le planner), vérifie que cet agent
         existe, qu'il a le bon type et qu'il est actif → le retourne.
      2. Sinon, parcourt tous les agents pour trouver le premier actif du bon type.
      3. Si aucun agent n'est trouvé, retourne None (le worker affichera une erreur).

    Args:
        agent_type: "clickhouse_analyst", "oracle_analyst" ou "data_analyst".
        agent_id_hint: ID suggéré par le planner (peut être None ou invalide).

    Returns:
        ID de l'agent à utiliser, ou None si aucun agent actif n'est trouvé.
    """
    if agent_id_hint:
        a = db.get(COLL_AGENTS, agent_id_hint)
        if a and a.get("type") == agent_type and a.get("is_active", True):
            return agent_id_hint
    for a in db.get_all(COLL_AGENTS):
        if a.get("type") == agent_type and a.get("is_active", True):
            return a["id"]
    return None


def _get_sql_tool_for_agent(agent_id: str) -> Optional[Any]:
    """
    Instancie l'outil SQL (ClickHouse ou Oracle) pour un agent donné.

    Utilisé uniquement par `_auto_schema_context()` pour récupérer le schéma
    de la base avant de lancer le sous-pipeline analyste.

    Chaîne de résolution :
      COLL_AGENTS[agent_id] → connection_id
      → COLL_CONNECTIONS[connection_id] → type + credentials
      → ClickHouseSQLTool ou OracleSQLTool(conn_cfg, row_limit)

    Returns:
        L'outil SQL instancié, ou None si la chaîne est incomplète.
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


def _auto_schema_context(agent_id: str, task_description: str) -> str:
    """
    Génère automatiquement le contexte de schéma à injecter dans le prompt analyste.

    Cette fonction est appelée par `_run_analyst_subtask()` AVANT de lancer le LLM.
    Elle permet au LLM de connaître les colonnes réelles d'une table sans avoir à
    exécuter d'abord une requête DESCRIBE TABLE (ce qui évite un aller-retour).

    Algorithme :
      1. Liste toutes les tables de la base via `tool.list_tables()`.
      2. Compare chaque nom de table (insensible à la casse) avec la description
         de la tâche pour identifier les tables mentionnées.
      3. Si des tables sont mentionnées → appelle `tool.get_schema(table)` pour
         récupérer les colonnes, types, commentaires, clés de tri/partition.
      4. Si aucune table n'est mentionnée → retourne simplement la liste de toutes
         les tables (utile pour que le LLM choisisse lui-même).

    Limites de sécurité :
      - Maximum 5 tables avec schéma complet (évite les prompts trop longs).
      - Maximum 50 tables dans la liste générale.

    Args:
        agent_id: ID de l'agent → permet de trouver la connexion DB.
        task_description: Description de la tâche contenant les noms de tables.

    Returns:
        Chaîne Markdown décrivant le schéma, ou "" si la DB est inaccessible.
    """
    tool = _get_sql_tool_for_agent(agent_id)
    if not tool:
        return ""

    try:
        # list_tables() retourne directement une List[str]
        # (pas un dict avec success/tables comme d'autres outils)
        all_tables: List[str] = tool.list_tables()
        # Filtre les erreurs éventuelles (list_tables() peut retourner ["ERROR: ..."])
        all_tables = [t for t in all_tables if not t.startswith("ERROR:")]
    except Exception as e:
        logger.warning("Could not list tables for schema context: %s", e)
        return ""

    if not all_tables:
        return ""

    # Détection des tables mentionnées dans la description de la tâche
    task_lower = task_description.lower()
    mentioned = [t for t in all_tables if t.lower() in task_lower]

    # Aucune table spécifique → fournit la liste complète pour orienter le LLM
    if not mentioned:
        table_list = ", ".join(all_tables[:60])
        return f"Available tables ({len(all_tables)}): {table_list}"

    # Compact schema: column names only (évite la saturation de la fenêtre contextuelle)
    # Le LLM appellera get_schema(table, columns_filter=...) pour les types au besoin
    schema_parts = []
    for table in mentioned[:8]:  # limite à 8 tables pour ne pas saturer le prompt
        try:
            # get_schema() retourne {"table":..., "columns":[...], "metadata":{...}}
            # ou {"error": "..."} en cas d'échec
            schema_result = tool.get_schema(table)
            if "error" not in schema_result and schema_result.get("columns"):
                cols = schema_result["columns"]
                col_names = [c["name"] for c in cols]
                total = len(col_names)
                shown = col_names[:100]
                names_str = ", ".join(shown)
                if total > 100:
                    names_str += f" … (+{total - 100} more)"
                meta = schema_result.get("metadata", {})
                meta_info = ""
                if meta.get("sorting_key"):
                    meta_info += f" | ORDER BY: {meta['sorting_key']}"
                if meta.get("partition_key"):
                    meta_info += f" | PARTITION: {meta['partition_key']}"
                schema_parts.append(
                    f"Table `{table}` ({total} cols{meta_info}): {names_str}"
                )
        except Exception as e:
            logger.warning("Could not get schema for table %s: %s", table, e)

    if schema_parts:
        header = "Schema (compact — use get_schema with columns_filter for type details):\n"
        return header + "\n".join(schema_parts)
    # Fallback si le schéma est inaccessible mais les tables sont connues
    table_list = ", ".join(all_tables[:60])
    return f"Available tables ({len(all_tables)}): {table_list}"


def _run_analyst_subtask(agent_id: str, task_description: str) -> Dict[str, Any]:
    """
    Exécute le pipeline analyste complet pour une sous-tâche de données.

    C'est le cœur de la délégation réelle : au lieu d'appeler un LLM générique
    qui inventerait des résultats, cette fonction exécute la vraie chaîne :
      analyst_node → sql_tool_node → synthesizer_node

    Pourquoi ne pas réutiliser le graphe compilé `build_analyst_graph()` ?
    Les nœuds du graphe compilé passent par le checkpointer (MemorySaver) et
    nécessitent un `config` avec `thread_id`. En appelant les fonctions nœuds
    directement, on évite cette complexité tout en gardant le même comportement.
    Le retry loop est géré ici explicitement plutôt que par les edges LangGraph.

    Déroulement :
      1. `_auto_schema_context()` → schéma réel de la DB (avant tout appel LLM)
      2. Initialisation de l'état AnalystState
      3. Boucle retry (max `max_retries` tentatives) :
         a. `analyst_node(state)` → génère le SQL (en tenant compte des erreurs précédentes)
         b. `sql_tool_node(state)` → exécute le SQL sur la vraie DB
         c. Si succès → sort de la boucle
         d. Si échec → continue (l'erreur dans state["last_error"] sera lue au prochain tour)
      4. `synthesizer_node(state)` → transforme les données brutes en narrative Markdown

    Args:
        agent_id: ID de l'agent analyste configuré avec la bonne connexion DB.
        task_description: Description de la tâche en langage naturel (ex: "Compte
                          le nombre de lignes dans la table orders par jour").

    Returns:
        Dict contenant :
          - "success" (bool) : True si au moins une requête a réussi.
          - "result" (str) : Narrative Markdown finale (vraies données ou message d'erreur).
          - "sql_executed" (str|None) : Dernier SQL exécuté.
          - "row_count" (int) : Nombre de lignes retournées (0 si échec).
          - "error" (str|None) : Dernière erreur (None si succès).
    """
    max_retries = 3

    # Étape 1 : pré-chargement du schéma (avant tout appel LLM)
    # Cela évite un premier aller-retour SQL "DESCRIBE TABLE" et enrichit
    # immédiatement le prompt de l'analyste
    schema_context = _auto_schema_context(agent_id, task_description)
    logger.info("Running analyst subtask for agent %s: %s", agent_id, task_description[:100])
    logger.info("Schema context length: %d chars", len(schema_context))

    # Étape 2 : initialisation de l'état AnalystState
    # Correspond exactement à la TypedDict définie dans state.py
    state: Dict[str, Any] = {
        "messages": [HumanMessage(content=task_description)],  # question initiale
        "user_question": task_description,
        "generated_sql": None,       # sera rempli par analyst_node
        "query_result": None,        # sera rempli par sql_tool_node
        "retry_count": 0,
        "max_retries": max_retries,
        "last_error": None,          # rempli par sql_tool_node en cas d'erreur
        "final_answer": None,        # rempli par synthesizer_node
        "schema_context": schema_context,
        "agent_id": agent_id,
        "session_id": "orchestrator_subtask",  # ID factice (pas de checkpointer ici)
    }

    # Étape 3 : boucle analyst → sql avec retry automatique
    for attempt in range(max_retries + 1):
        try:
            # analyst_node génère le SQL (ou le corrige si last_error est renseigné)
            analyst_result = analyst_node(state)
            state.update(analyst_result)  # met à jour generated_sql + messages

            # sql_tool_node exécute le SQL sur la base réelle
            tool_result = sql_tool_node(state)
            state.update(tool_result)   # met à jour query_result, last_error, retry_count

            if state.get("query_result", {}).get("success"):
                logger.info("Analyst subtask succeeded on attempt %d", attempt + 1)
                break  # succès → on sort de la boucle et on passe à la synthèse

            if attempt < max_retries:
                logger.info(
                    "Analyst subtask attempt %d failed, retrying... error: %s",
                    attempt + 1, state.get("last_error")
                )
            # L'erreur reste dans state["last_error"] et sera lue par analyst_node
            # au prochain tour pour corriger le SQL

        except Exception as e:
            # Exception Python (pas une erreur SQL) : connexion impossible, timeout, etc.
            logger.error("Analyst subtask exception on attempt %d: %s", attempt + 1, e)
            state["last_error"] = str(e)
            if attempt >= max_retries:
                break

    # Étape 4 : synthèse narrative (même en cas d'échec partiel)
    try:
        synth_result = analyst_synthesizer_node(state)
        state.update(synth_result)  # met à jour final_answer
    except Exception as e:
        logger.error("Analyst synthesizer failed: %s", e)
        # Fallback minimal : le résultat brut sera retourné sans mise en forme
        state["final_answer"] = f"Analysis completed but synthesis failed: {e}"

    query_result = state.get("query_result", {})
    success = query_result.get("success", False)

    return {
        "success": success,
        # Si final_answer est None (edge case), on génère un message d'erreur explicite
        "result": state.get("final_answer") or (
            f"❌ Analysis failed after {max_retries} attempts.\nLast error: {state.get('last_error')}"
        ),
        "sql_executed": state.get("generated_sql"),
        "row_count": query_result.get("row_count", 0) if success else 0,
        "error": state.get("last_error") if not success else None,
    }


def _run_data_analyst_subtask(agent_id: str, task_description: str) -> Dict[str, Any]:
    """
    Exécute le pipeline data analyst complet pour une sous-tâche de l'orchestrateur.

    Pipeline exécuté :
      da_planner_node → [da_sql_executor_node →] da_analyst_node → da_synthesizer_node

    Contrairement à `_run_analyst_subtask` (centré SQL avec retry), ce pipeline :
      - Planifie d'abord l'analyse (type, métriques, SQL si pertinent)
      - Exécute jusqu'à N requêtes SQL en parallèle (multi-query)
      - Effectue une analyse statistique/business approfondie
      - Produit une synthèse narrative orientée décideur

    Args:
        agent_id: ID de l'agent data_analyst configuré.
        task_description: Description de la tâche analytique à réaliser.

    Returns:
        Dict avec "success", "result" (narrative Markdown), "data_fetched" (bool).
    """
    logger.info("Running data analyst subtask for agent %s: %s", agent_id, task_description[:100])

    # Pré-chargement du schéma pour le planner
    schema_context = da_auto_schema_context(agent_id, task_description)

    # Initialisation de l'état DataAnalystState
    state: Dict[str, Any] = {
        "messages": [HumanMessage(content=task_description)],
        "user_question": task_description,
        "analysis_plan": None,
        "sql_queries": None,
        "data_results": None,
        "analysis_output": None,
        "final_answer": None,
        "schema_context": schema_context,
        "agent_id": agent_id,
        "session_id": "orchestrator_subtask",
        "last_error": None,
    }

    try:
        # Étape 1 : planification (type d'analyse + SQL si pertinent)
        state.update(da_planner_node(state))

        # Étape 2 : exécution SQL si des requêtes ont été planifiées et DB disponible
        sql_queries = state.get("sql_queries") or []
        if sql_queries and da_has_connection(agent_id):
            state.update(da_sql_executor_node(state))

        # Étape 3 : analyse approfondie
        state.update(da_analyst_node(state))

        # Étape 4 : synthèse business
        state.update(da_synthesizer_node(state))

    except Exception as e:
        logger.error("Data analyst subtask failed: %s", e, exc_info=True)
        return {
            "success": False,
            "result": f"❌ Data analyst execution error: {e}",
            "data_fetched": False,
            "error": str(e),
        }

    data_results = state.get("data_results") or []
    data_fetched = any(r.get("success") for r in data_results)

    return {
        "success": True,
        "result": state.get("final_answer") or "Analysis completed without final synthesis.",
        "data_fetched": data_fetched,
        "queries_run": len(data_results),
    }


def _run_report_subtask(agent_id: str, task_description: str, session_context: str = "") -> Dict[str, Any]:
    """
    Exécute le pipeline report_writer pour générer un PDF professionnel.

    Appelle directement les nœuds du graphe report sans passer par le graphe
    compilé, pour rester cohérent avec le pattern des autres subtask runners.

    Args:
        agent_id: ID de l'agent report_writer (ou vide — le graph n'en a pas besoin).
        task_description: Description de la tâche (ex: "Génère un rapport PDF").
        session_context: Contexte textuel des tâches précédentes.

    Returns:
        Dict avec "success", "result" (message final), "report_id" (str|None).
    """
    from .report_graph import report_writer_node, pdf_node

    logger.info("Running report subtask: %s", task_description[:100])

    state: Dict[str, Any] = {
        "messages": [],
        "user_request": task_description,
        "session_context": session_context,
        "report_markdown": None,
        "pdf_path": None,
        "report_id": None,
        "final_answer": None,
        "agent_id": agent_id or "orchestrator",
        "session_id": "orchestrator_subtask",
    }

    try:
        state.update(report_writer_node(state))
        state.update(pdf_node(state))
    except Exception as e:
        logger.error("Report subtask failed: %s", e, exc_info=True)
        return {"success": False, "result": f"❌ Report generation error: {e}", "report_id": None, "error": str(e)}

    return {
        "success": True,
        "result": state.get("final_answer") or "Report generated.",
        "report_id": state.get("report_id"),
    }


def _run_file_manager_subtask(agent_id: str, task_description: str) -> Dict[str, Any]:
    """
    Exécute le pipeline File Manager pour une sous-tâche de l'orchestrateur.

    Appelle le graphe file_graph compilé en mode synchrone (invoke).
    Supporte : lecture, écriture, liste, recherche de fichiers/répertoires.

    Args:
        agent_id: ID de l'agent file_manager configuré.
        task_description: Description de l'opération à effectuer.

    Returns:
        Dict avec "success", "result" (narrative Markdown).
    """
    from .file_graph import build_file_graph

    logger.info("Running file manager subtask for agent %s: %s", agent_id, task_description[:100])
    graph = build_file_graph()
    thread_id = f"orch_file_{uuid.uuid4().hex[:8]}"
    state: Dict[str, Any] = {
        "messages": [HumanMessage(content=task_description)],
        "user_request": task_description,
        "final_answer": None,
        "iteration_count": 0,
        "agent_id": agent_id,
        "session_id": thread_id,
    }
    try:
        config = {"configurable": {"thread_id": thread_id}}
        final_state = graph.invoke(state, config=config)
        return {
            "success": True,
            "result": final_state.get("final_answer") or "File operation completed.",
        }
    except Exception as e:
        logger.error("File manager subtask failed: %s", e, exc_info=True)
        return {"success": False, "result": f"❌ File manager error: {e}", "error": str(e)}


def _run_powerbi_subtask(agent_id: str, task_description: str, session_id: str = "") -> Dict[str, Any]:
    """
    Exécute le pipeline Power BI Analyst pour une sous-tâche de l'orchestrateur.

    Appelle le graphe powerbi_graph compilé en mode synchrone (invoke).
    La session Playwright est identifiée par session_id pour réutiliser
    le contexte navigateur entre appels successifs.

    Args:
        agent_id: ID de l'agent powerbi_analyst configuré.
        task_description: Description de l'analyse à effectuer.
        session_id: ID de session orchestrateur (propagé au graphe Power BI).

    Returns:
        Dict avec "success", "result" (analyse Markdown).
    """
    from .powerbi_graph import build_powerbi_graph

    logger.info("Running PowerBI subtask for agent %s: %s", agent_id, task_description[:100])
    # Réutilise le session_id de l'orchestrateur pour que la session Playwright
    # persiste entre plusieurs sous-tâches Power BI de la même conversation.
    powerbi_session = session_id or f"orch_pbi_{uuid.uuid4().hex[:8]}"
    graph = build_powerbi_graph()
    state: Dict[str, Any] = {
        "messages": [HumanMessage(content=task_description)],
        "user_request": task_description,
        "final_answer": None,
        "iteration_count": 0,
        "agent_id": agent_id,
        "session_id": powerbi_session,
    }
    try:
        config = {"configurable": {"thread_id": powerbi_session}}
        final_state = graph.invoke(state, config=config)
        return {
            "success": True,
            "result": final_state.get("final_answer") or "Power BI analysis completed.",
        }
    except Exception as e:
        logger.error("PowerBI subtask failed: %s", e, exc_info=True)
        return {"success": False, "result": f"❌ Power BI agent error: {e}", "error": str(e)}


# ── Nœuds du graphe orchestrateur ────────────────────────────────────────────

def _build_agents_block() -> str:
    """Charge tous les agents spécialistes actifs et les formate pour les prompts LLM."""
    _specialist_types = (
        "clickhouse_analyst", "oracle_analyst", "data_analyst",
        "file_manager", "powerbi_analyst", "report_writer",
    )
    all_specialists = [
        a for a in db.get_all(COLL_AGENTS)
        if a.get("type") in _specialist_types and a.get("is_active", True)
    ]
    if not all_specialists:
        return "No specialist agents configured — use action=cannot_fulfill for data/file/report tasks."

    _categories = {
        "Data (SQL / analytics)": ("clickhouse_analyst", "oracle_analyst", "data_analyst"),
        "File management": ("file_manager",),
        "Power BI / BI dashboards": ("powerbi_analyst",),
        "Report generation": ("report_writer",),
    }
    blocks = []
    for cat_label, cat_types in _categories.items():
        agents_in_cat = [a for a in all_specialists if a.get("type") in cat_types]
        if agents_in_cat:
            lines = []
            for a in agents_in_cat:
                raw_desc = a.get("description") or ""
                if not raw_desc and a.get("system_prompt"):
                    raw_desc = a["system_prompt"][:200].replace("\n", " ")
                desc_part = f'  desc="{raw_desc[:150].strip()}"' if raw_desc else ""
                lines.append(f"    - name={a['name']}  type={a['type']}  id={a['id']}{desc_part}")
            blocks.append(f"  [{cat_label}]\n" + "\n".join(lines))
    return "Available specialist agents (read descriptions before acting):\n" + "\n".join(blocks)


def reasoner_node(state: OrchestratorState) -> Dict[str, Any]:
    """
    Nœud 1 — Raisonnement ReAct (Thought).

    Rôle :
        Appelé à chaque itération de la boucle ReAct. Le LLM reçoit :
          - La requête initiale de l'utilisateur
          - La liste de tous les agents disponibles (avec descriptions)
          - Toutes les observations accumulées (résultats des agents précédents)

        Il produit UNE décision parmi :
          - call_agent  : déléguer la prochaine sous-tâche à un agent spécialiste
          - final_answer: tout le travail nécessaire est fait, passer à la synthèse
          - human_validation : besoin de clarification humaine avant de continuer
          - cannot_fulfill : la demande dépasse les capacités des agents disponibles

    Position dans le workflow :
        START → reasoner (première itération, pas d'observations)
        worker → reasoner (itérations suivantes, observations accumulées)
        human_feedback → reasoner (après clarification humaine)

    Modifications de l'état :
        - `current_task` : tâche à exécuter (si call_agent) ou None (si final_answer)
        - `awaiting_human` : True uniquement pour human_validation
        - `final_answer` : renseigné uniquement pour cannot_fulfill (réponse directe)
        - `iteration` : incrémenté à chaque appel à call_agent
        - `messages` : décision du raisonneur
    """
    llm = build_llm()

    agents_block = _build_agents_block()

    # Construit le bloc d'observations depuis les résultats des agents précédents
    results = state.get("worker_results", [])
    step_num = len(results) + 1
    if results:
        obs_parts = []
        for r in results:
            status = "✅" if r.get("success") else "❌"
            excerpt = r["result"][:500] + ("…" if len(r["result"]) > 500 else "")
            obs_parts.append(
                f"{status} [{r['task_id']}] {r.get('agent_name', r.get('agent_type', '?'))}"
                f" — {r['task_description']}\n→ {excerpt}"
            )
        observations = "\n\n".join(obs_parts)
    else:
        observations = "No prior observations — this is the first step."

    system_content = REASONER_SYSTEM.format(
        agents_block=agents_block,
        observations=observations,
        step_num=step_num,
    )

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=f"User request: {state['user_request']}"),
    ]

    try:
        response = llm.invoke(messages)
        raw = response.content.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        decision = json.loads(raw)
        action = decision.get("action")
        thought = decision.get("thought", "")
        logger.info("Reasoner step %d: action=%s | thought: %s", step_num, action, thought[:120])

        if action == "final_answer":
            return {
                "current_task": None,
                "awaiting_human": False,
                "messages": [AIMessage(content=f"[Reasoner step {step_num}] All work done → synthesizing.")],
            }

        if action == "cannot_fulfill":
            reason = decision.get("reason", "No matching agent available.")
            return {
                "current_task": None,
                "awaiting_human": False,
                "final_answer": (
                    f"Je ne suis pas en mesure de répondre à cette demande.\n\n"
                    f"**Raison :** {reason}"
                ),
                "messages": [AIMessage(content=f"[Reasoner step {step_num}] Cannot fulfill: {reason}")],
            }

        if action == "human_validation":
            task = {
                "id": decision.get("task_id", f"human_{step_num}"),
                "description": decision.get("description", ""),
                "agent_type": "human_validation",
                "agent_id": None,
                "priority": step_num,
                "depends_on": [],
                "requires_human_approval": True,
            }
            return {
                "current_task": task,
                "awaiting_human": True,
                "messages": [AIMessage(content=f"[Reasoner step {step_num}] Human validation needed: {task['description'][:100]}")],
            }

        if action == "call_agent":
            task = {
                "id": decision.get("task_id", f"step_{step_num}"),
                "description": decision.get("description", ""),
                "agent_type": decision.get("agent_type", "orchestrator"),
                "agent_id": decision.get("agent_id"),
                "priority": step_num,
                "depends_on": [],
                "requires_human_approval": False,
            }
            return {
                "current_task": task,
                "awaiting_human": False,
                "iteration": state.get("iteration", 0) + 1,
                "messages": [AIMessage(content=f"[Reasoner step {step_num}] Calling {task['agent_type']}: {task['description'][:100]}")],
            }

        # Action inconnue → arrêt sécurisé
        logger.warning("Reasoner returned unknown action '%s' — stopping.", action)
        return {
            "current_task": None,
            "awaiting_human": False,
            "messages": [AIMessage(content=f"[Reasoner step {step_num}] Unknown action '{action}' — stopping.")],
        }

    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Reasoner failed to parse LLM response: %s", e)
        return {
            "current_task": None,
            "awaiting_human": False,
            "messages": [AIMessage(content=f"[Reasoner step {step_num}] Parse error: {e} — stopping.")],
        }


# ── Ancien planner_node (Plan-and-Execute, conservé pour référence) ────────────
def planner_node(state: OrchestratorState) -> Dict[str, Any]:
    """
    Nœud 1 — Planification et décomposition de la requête.

    Rôle :
        Interroge le LLM pour décomposer `state["user_request"]` en une liste
        ordonnée de sous-tâches (le « backlog »). Chaque tâche spécifie :
          - son type (`agent_type`) : quel spécialiste doit l'exécuter
          - son `agent_id` : ID exact de l'agent à utiliser pour les tâches data
          - sa priorité et ses dépendances (`depends_on`)
          - si elle nécessite une validation humaine

    Injection dynamique des agents :
        Avant de construire le prompt, ce nœud charge depuis la base tous les
        agents analystes actifs et les injecte dans le placeholder `{agents_block}`
        du PLANNER_SYSTEM. Le LLM peut alors assigner les bons `agent_id` dans
        le plan, ce qui permet à `worker_node` de les retrouver sans ambiguïté.

    Parsing du JSON :
        La réponse du LLM doit être un JSON pur. Les balises Markdown éventuelles
        (```json … ```) sont nettoyées avant le parsing. En cas d'échec de parsing,
        un fallback crée une tâche unique pointant vers le premier agent analyste
        disponible (ou "orchestrator" s'il n'y en a pas).

    Modifications de l'état :
        - `task_backlog` : liste des tâches planifiées.
        - `current_task` : None (pas encore de tâche en cours).
        - `worker_results` : [] (réinitialisé au début du workflow).
        - `iteration` : 0 (compteur remis à zéro).
        - `messages` : résumé du plan créé.
    """
    llm = build_llm()

    # Charge tous les agents spécialistes actifs (tous types sauf orchestrator/custom)
    _specialist_types = (
        "clickhouse_analyst", "oracle_analyst", "data_analyst",
        "file_manager", "powerbi_analyst", "report_writer",
    )
    all_specialists = [
        a for a in db.get_all(COLL_AGENTS)
        if a.get("type") in _specialist_types and a.get("is_active", True)
    ]
    # Alias utilisé dans le fallback (compatibilité avec l'ancienne variable)
    analysts = [a for a in all_specialists if a.get("type") in ("clickhouse_analyst", "oracle_analyst", "data_analyst")]

    if all_specialists:
        # Regroupe les agents par catégorie pour la lisibilité du prompt
        _categories = {
            "Data (SQL / analytics)": ("clickhouse_analyst", "oracle_analyst", "data_analyst"),
            "File management": ("file_manager",),
            "Power BI / BI dashboards": ("powerbi_analyst",),
            "Report generation": ("report_writer",),
        }
        blocks = []
        for cat_label, cat_types in _categories.items():
            agents_in_cat = [a for a in all_specialists if a.get("type") in cat_types]
            if agents_in_cat:
                lines = []
                for a in agents_in_cat:
                    # Include description (or a truncated system_prompt excerpt) so the
                    # planner can verify the agent actually handles the required task type.
                    raw_desc = a.get("description") or ""
                    if not raw_desc and a.get("system_prompt"):
                        # Derive a concise excerpt from the system prompt
                        raw_desc = a["system_prompt"][:200].replace("\n", " ")
                    desc_part = f'  desc="{raw_desc[:150].strip()}"' if raw_desc else ""
                    lines.append(f"    - name={a['name']}  type={a['type']}  id={a['id']}{desc_part}")
                blocks.append(f"  [{cat_label}]\n" + "\n".join(lines))
        agents_block = "Available specialist agents (read descriptions before planning):\n" + "\n".join(blocks)
    else:
        agents_block = "No specialist agents configured — use agent_type=orchestrator for all tasks."

    # Substitution du placeholder {agents_block} dans le template PLANNER_SYSTEM
    system_content = PLANNER_SYSTEM.format(agents_block=agents_block)

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=f"User request: {state['user_request']}"),
    ]

    try:
        response = llm.invoke(messages)
        raw = response.content.strip()

        # Nettoyage des balises Markdown que certains LLMs ajoutent malgré les instructions
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        plan = json.loads(raw)
        tasks = plan.get("tasks", [])

        logger.info("Planner created %d tasks", len(tasks))

        return {
            "messages": [AIMessage(content=f"Plan created: {plan['analysis']}\n\nTasks: {len(tasks)}")],
            "task_backlog": tasks,
            "current_task": None,
            "worker_results": [],
            "iteration": 0,
        }
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Planner failed to parse JSON: %s", e)
        # Fallback : tâche unique dirigée vers le premier analyste disponible
        agent_type = "orchestrator"
        agent_id = None
        if analysts:
            agent_type = analysts[0]["type"]
            agent_id = analysts[0]["id"]

        fallback_task = {
            "id": "task_1",
            "description": state["user_request"],
            "agent_type": agent_type,
            "agent_id": agent_id,
            "priority": 1,
            "depends_on": [],
            "requires_human_approval": False,
        }
        return {
            "messages": [AIMessage(content="Could not parse detailed plan, proceeding with single task.")],
            "task_backlog": [fallback_task],
            "current_task": None,
            "worker_results": [],
            "iteration": 0,
        }


def task_dispatcher_node(state: OrchestratorState) -> Dict[str, Any]:
    """
    Nœud 2 — Sélection de la prochaine tâche à exécuter.

    Rôle :
        Parcourt le backlog et identifie la prochaine tâche exécutable en
        respectant l'ordre de priorité et les dépendances entre tâches.

    Gestion des dépendances :
        Une tâche n'est éligible que si toutes les tâches listées dans son
        champ `depends_on` ont déjà un résultat dans `worker_results`.
        Cela permet des workflows séquentiels (ex : analyser d'abord les
        données brutes, puis agréger les résultats).

    Human-in-the-loop :
        Si la prochaine tâche porte `requires_human_approval: true`, le nœud
        met `awaiting_human = True` et `route_after_dispatcher` bascule vers
        `human_feedback_node` qui déclenche l'interruption LangGraph.

    Fin de backlog :
        Si toutes les tâches sont terminées (ou si aucune tâche n'est éligible),
        `current_task` est mis à None → `route_after_dispatcher` bascule vers
        `synthesizer_node`.

    Modifications de l'état :
        - `current_task` : prochaine tâche à exécuter (ou None si backlog vide).
        - `awaiting_human` : True si approbation requise.
        - `iteration` : incrémenté (+1 à chaque passage).
    """
    backlog = state.get("task_backlog", [])
    # Ensemble des IDs de tâches déjà exécutées (succès OU échec)
    completed_ids = {r["task_id"] for r in state.get("worker_results", [])}

    # Tri par priorité (1 = la plus haute), puis vérification des dépendances
    next_task = None
    for task in sorted(backlog, key=lambda t: t.get("priority", 99)):
        if task["id"] in completed_ids:
            continue  # déjà exécutée
        deps = task.get("depends_on", [])
        if all(dep in completed_ids for dep in deps):
            next_task = task  # toutes les dépendances sont satisfaites
            break

    if next_task and next_task.get("requires_human_approval"):
        # Interruption humaine : message d'attente + flag awaiting_human
        return {
            "current_task": next_task,
            "awaiting_human": True,
            "messages": [
                AIMessage(
                    content=f"⚠️ **Human approval required** for task: {next_task['description']}\n\nPlease confirm to proceed."
                )
            ],
        }

    return {
        "current_task": next_task,        # None si backlog épuisé
        "awaiting_human": False,
        "iteration": state.get("iteration", 0) + 1,
    }


def worker_node(state: OrchestratorState) -> Dict[str, Any]:
    """
    Nœud 3 — Exécution de la tâche courante.

    C'est le nœud le plus important : il décide comment exécuter la tâche
    selon son `agent_type`.

    Deux chemins d'exécution :

    ── A) Agent analyste data (clickhouse_analyst / oracle_analyst) ──────────
      1. `_find_analyst_agent(agent_type, agent_id_hint)` → ID de l'agent
      2. `_run_analyst_subtask(agent_id, description)` → pipeline SQL réel :
             analyst_node → sql_tool_node → synthesizer_node
         Le résultat contient de vraies données issues de la base de données.
      Le résultat est ajouté à `worker_results` avec les métadonnées SQL
      (sql_executed, row_count) pour traçabilité.

    ── B) Worker LLM générique (orchestrator) ───────────────────────────────
      Pour les tâches non-data (rédaction, agrégation logique, formatage…),
      un LLM générique est invoqué avec :
        - Le contexte des tâches précédentes (résultats dans worker_results)
        - La description de la tâche courante
      Ce chemin NE doit PAS être utilisé pour des questions de données, car
      le LLM inventerait des chiffres plutôt que d'interroger la base.

    Gestion des erreurs :
        Tout échec (agent non trouvé, exception SQL, erreur LLM) est capturé
        et ajouté à `worker_results` avec `success: False`. Le nœud ne lève
        jamais d'exception lui-même — l'erreur est propagée via l'état pour
        que `route_after_worker` puisse décider de retry ou de continuer.

    Modifications de l'état :
        - `worker_results` : nouvelle entrée ajoutée (succès ou échec).
        - `last_error` : None si succès, message d'erreur sinon.
        - `messages` : statut de la tâche.
    """
    task = state.get("current_task")
    if not task:
        # Edge case : dispatcher a mis current_task à None mais worker est appelé
        return {"worker_results": state.get("worker_results", [])}

    agent_type = task.get("agent_type", "orchestrator")

    # ── Chemin 0 : tâche non réalisable (aucun agent disponible) ─────────────
    # Le planificateur utilise ce type quand il ne trouve aucun agent capable
    # de traiter la sous-tâche. On retourne un message clair sans appeler le LLM
    # pour éviter toute hallucination.
    if agent_type == "cannot_fulfill":
        reason = task.get("description", "Cette action n'est pas prise en charge par les agents disponibles.")
        result_entry = {
            "task_id": task["id"],
            "task_description": task["description"],
            "agent_type": agent_type,
            "agent_id": None,
            "agent_name": "Orchestrateur",
            "result": (
                f"⚠️ Je ne suis pas en mesure de répondre à cette demande : "
                f"aucun agent configuré ne peut traiter ce type de tâche.\n\n"
                f"**Détail :** {reason}"
            ),
            "success": False,
            "error": "cannot_fulfill: no matching agent available",
        }
        updated_results = state.get("worker_results", []) + [result_entry]
        return {
            "worker_results": updated_results,
            "messages": [AIMessage(content=f"Task '{task['id']}' cannot be fulfilled — no matching agent.")],
            "last_error": result_entry["error"],
        }

    # ── Chemin A1 : délégation au pipeline data analyst (analyse + business) ─
    if agent_type == "data_analyst":
        agent_id = task.get("agent_id") or _find_analyst_agent(agent_type)
        agent_cfg = db.get(COLL_AGENTS, agent_id) if agent_id else None
        agent_name = agent_cfg.get("name", agent_id) if agent_cfg else agent_id or "data_analyst"
        if not agent_id:
            result_entry = {
                "task_id": task["id"],
                "task_description": task["description"],
                "agent_type": agent_type,
                "agent_id": None,
                "agent_name": "—",
                "result": "❌ No active data_analyst agent found. Please create and configure one.",
                "success": False,
                "error": "No active data_analyst agent available.",
            }
        else:
            try:
                subtask_result = _run_data_analyst_subtask(agent_id, task["description"])
                result_entry = {
                    "task_id": task["id"],
                    "task_description": task["description"],
                    "agent_type": agent_type,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "result": subtask_result["result"],
                    "success": subtask_result["success"],
                    "data_fetched": subtask_result.get("data_fetched", False),
                    "queries_run": subtask_result.get("queries_run", 0),
                }
                if not subtask_result["success"]:
                    result_entry["error"] = subtask_result.get("error")
            except Exception as e:
                logger.error("Data analyst subtask raised exception: %s", e, exc_info=True)
                result_entry = {
                    "task_id": task["id"],
                    "task_description": task["description"],
                    "agent_type": agent_type,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "result": f"❌ Data analyst execution error: {e}",
                    "success": False,
                    "error": str(e),
                }

        updated_results = state.get("worker_results", []) + [result_entry]
        last_error = result_entry.get("error") if not result_entry["success"] else None
        return {
            "worker_results": updated_results,
            "messages": [AIMessage(content=f"Task '{task['id']}' {'completed' if result_entry['success'] else 'failed'}.")],
            "last_error": last_error,
        }

    # ── Chemin A2 : délégation au pipeline analyste SQL réel ─────────────────
    if agent_type in ("clickhouse_analyst", "oracle_analyst"):
        # Résolution de l'agent : priorité à l'agent_id suggéré par le planner
        agent_id = task.get("agent_id") or _find_analyst_agent(agent_type)
        agent_cfg = db.get(COLL_AGENTS, agent_id) if agent_id else None
        agent_name = agent_cfg.get("name", agent_id) if agent_cfg else agent_id or agent_type
        if not agent_id:
            # Aucun agent actif du bon type → erreur explicite
            result_entry = {
                "task_id": task["id"],
                "task_description": task["description"],
                "agent_type": agent_type,
                "agent_id": None,
                "agent_name": "—",
                "result": f"❌ No active {agent_type} agent found. Please create and configure one.",
                "success": False,
                "error": f"No active {agent_type} agent available.",
            }
        else:
            try:
                # Exécution réelle du sous-pipeline analyste
                subtask_result = _run_analyst_subtask(agent_id, task["description"])
                result_entry = {
                    "task_id": task["id"],
                    "task_description": task["description"],
                    "agent_type": agent_type,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "result": subtask_result["result"],        # narrative Markdown avec vraies données
                    "success": subtask_result["success"],
                    "sql_executed": subtask_result.get("sql_executed"),  # pour traçabilité
                    "row_count": subtask_result.get("row_count", 0),
                }
                if not subtask_result["success"]:
                    result_entry["error"] = subtask_result.get("error")
            except Exception as e:
                # Exception inattendue (bug Python, pas une erreur SQL)
                logger.error("Analyst subtask raised exception: %s", e, exc_info=True)
                result_entry = {
                    "task_id": task["id"],
                    "task_description": task["description"],
                    "agent_type": agent_type,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "result": f"❌ Analyst execution error: {e}",
                    "success": False,
                    "error": str(e),
                }

        # Ajout du résultat au tableau cumulatif (immutable list pattern de LangGraph)
        updated_results = state.get("worker_results", []) + [result_entry]
        last_error = result_entry.get("error") if not result_entry["success"] else None
        return {
            "worker_results": updated_results,
            "messages": [AIMessage(content=f"Task '{task['id']}' {'completed' if result_entry['success'] else 'failed'}.")],
            "last_error": last_error,
        }

    # ── Chemin A3 : délégation au pipeline report_writer ─────────────────────
    if agent_type == "report_writer":
        agent_id = task.get("agent_id") or _find_analyst_agent("report_writer")
        agent_cfg = db.get(COLL_AGENTS, agent_id) if agent_id else None
        agent_name = agent_cfg.get("name", agent_id) if agent_cfg else "Rédacteur PDF"
        # Build session_context from all prior worker results
        session_context = "\n\n".join(
            f"**{r['task_description']}**\n{r['result']}"
            for r in state.get("worker_results", [])
            if r.get("success")
        )
        try:
            subtask_result = _run_report_subtask(agent_id or "", task["description"], session_context)
            result_entry = {
                "task_id": task["id"],
                "task_description": task["description"],
                "agent_type": agent_type,
                "agent_id": agent_id,
                "agent_name": agent_name,
                "result": subtask_result["result"],
                "success": subtask_result["success"],
                "report_id": subtask_result.get("report_id"),
            }
            if not subtask_result["success"]:
                result_entry["error"] = subtask_result.get("error")
        except Exception as e:
            logger.error("Report subtask raised exception: %s", e, exc_info=True)
            result_entry = {
                "task_id": task["id"],
                "task_description": task["description"],
                "agent_type": agent_type,
                "agent_id": agent_id if agent_id else None,
                "agent_name": agent_name,
                "result": f"❌ Report generation error: {e}",
                "success": False,
                "error": str(e),
            }
        updated_results = state.get("worker_results", []) + [result_entry]
        last_error = result_entry.get("error") if not result_entry["success"] else None
        return {
            "worker_results": updated_results,
            "messages": [AIMessage(content=f"Task '{task['id']}' {'completed' if result_entry['success'] else 'failed'}.")],
            "last_error": last_error,
        }

    # ── Chemin A4 : délégation au pipeline File Manager ──────────────────────
    if agent_type == "file_manager":
        agent_id = task.get("agent_id") or _find_analyst_agent("file_manager")
        agent_cfg = db.get(COLL_AGENTS, agent_id) if agent_id else None
        agent_name = agent_cfg.get("name", agent_id) if agent_cfg else "Gestionnaire Fichiers"
        if not agent_id:
            result_entry = {
                "task_id": task["id"],
                "task_description": task["description"],
                "agent_type": agent_type,
                "agent_id": None,
                "agent_name": "—",
                "result": "❌ No active file_manager agent found. Please create one first.",
                "success": False,
                "error": "No active file_manager agent available.",
            }
        else:
            try:
                subtask_result = _run_file_manager_subtask(agent_id, task["description"])
                result_entry = {
                    "task_id": task["id"],
                    "task_description": task["description"],
                    "agent_type": agent_type,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "result": subtask_result["result"],
                    "success": subtask_result["success"],
                }
                if not subtask_result["success"]:
                    result_entry["error"] = subtask_result.get("error")
            except Exception as e:
                logger.error("File manager subtask raised exception: %s", e, exc_info=True)
                result_entry = {
                    "task_id": task["id"],
                    "task_description": task["description"],
                    "agent_type": agent_type,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "result": f"❌ File manager execution error: {e}",
                    "success": False,
                    "error": str(e),
                }
        updated_results = state.get("worker_results", []) + [result_entry]
        last_error = result_entry.get("error") if not result_entry["success"] else None
        return {
            "worker_results": updated_results,
            "messages": [AIMessage(content=f"Task '{task['id']}' {'completed' if result_entry['success'] else 'failed'}.")],
            "last_error": last_error,
        }

    # ── Chemin A5 : délégation au pipeline Power BI Analyst ──────────────────
    if agent_type == "powerbi_analyst":
        agent_id = task.get("agent_id") or _find_analyst_agent("powerbi_analyst")
        agent_cfg = db.get(COLL_AGENTS, agent_id) if agent_id else None
        agent_name = agent_cfg.get("name", agent_id) if agent_cfg else "Analyste Power BI"
        if not agent_id:
            result_entry = {
                "task_id": task["id"],
                "task_description": task["description"],
                "agent_type": agent_type,
                "agent_id": None,
                "agent_name": "—",
                "result": "❌ No active powerbi_analyst agent found. Please create one first.",
                "success": False,
                "error": "No active powerbi_analyst agent available.",
            }
        else:
            try:
                # Propagate orchestrator session_id so Playwright session persists
                subtask_result = _run_powerbi_subtask(agent_id, task["description"], state.get("session_id", ""))
                result_entry = {
                    "task_id": task["id"],
                    "task_description": task["description"],
                    "agent_type": agent_type,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "result": subtask_result["result"],
                    "success": subtask_result["success"],
                }
                if not subtask_result["success"]:
                    result_entry["error"] = subtask_result.get("error")
            except Exception as e:
                logger.error("PowerBI subtask raised exception: %s", e, exc_info=True)
                result_entry = {
                    "task_id": task["id"],
                    "task_description": task["description"],
                    "agent_type": agent_type,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "result": f"❌ Power BI agent execution error: {e}",
                    "success": False,
                    "error": str(e),
                }
        updated_results = state.get("worker_results", []) + [result_entry]
        last_error = result_entry.get("error") if not result_entry["success"] else None
        return {
            "worker_results": updated_results,
            "messages": [AIMessage(content=f"Task '{task['id']}' {'completed' if result_entry['success'] else 'failed'}.")],
            "last_error": last_error,
        }

    # ── Chemin B : worker LLM générique pour les tâches non-data ────────────
    llm = build_llm()
    # Construit le contexte depuis les résultats des tâches précédentes
    # Le LLM peut ainsi s'appuyer sur les données déjà analysées pour
    # rédiger un résumé, une conclusion ou effectuer un calcul logique
    context = "\n\n".join(
        f"[Task {r['task_id']}]: {r['result']}"
        for r in state.get("worker_results", [])
    )

    messages = [
        SystemMessage(
            content="You are a skilled assistant. Complete the assigned task thoroughly and accurately. "
            "Use any prior context provided."
        ),
        HumanMessage(
            content=f"Previous context:\n{context}\n\nYour task:\n{task['description']}"
        ),
    ]

    try:
        response = llm.invoke(messages)
        result_entry = {
            "task_id": task["id"],
            "task_description": task["description"],
            "agent_type": agent_type,
            "agent_id": state.get("agent_id"),
            "agent_name": "Orchestrateur",
            "result": response.content,
            "success": True,
        }
        updated_results = state.get("worker_results", []) + [result_entry]
        return {
            "worker_results": updated_results,
            "messages": [AIMessage(content=f"Task '{task['id']}' completed.")],
            "last_error": None,
        }
    except Exception as e:
        error_entry = {
            "task_id": task["id"],
            "task_description": task["description"],
            "agent_type": agent_type,
            "agent_id": state.get("agent_id"),
            "agent_name": "Orchestrateur",
            "result": f"ERROR: {e}",
            "success": False,
            "error": str(e),
        }
        updated_results = state.get("worker_results", []) + [error_entry]
        return {
            "worker_results": updated_results,
            "last_error": str(e),
            "messages": [AIMessage(content=f"Task '{task['id']}' failed: {e}")],
        }


def corrector_node(state: OrchestratorState) -> Dict[str, Any]:
    """
    Nœud 4 — Correction des instructions avant un retry.

    Atteint uniquement si `route_after_worker` détecte un échec ET que le
    nombre de retries n'est pas épuisé (< 3 tentatives par tâche).

    Rôle :
        Demande au LLM d'analyser le message d'erreur (`state["last_error"]`)
        et la description de la tâche ayant échoué, puis de produire une
        description corrigée qui permettra au worker de réussir au prochain essai.

    Cas d'usage typique pour les tâches orchestrateur génériques :
        - Tâche trop vague → instruction plus précise
        - Mauvais format demandé → instruction reformatée
        (Pour les tâches analytiques data, le retry est géré dans _run_analyst_subtask)

    Modifications de l'état :
        - `current_task` : même tâche avec `description` remplacée par la
          version corrigée du LLM.
        - `messages` : confirmation de l'application de la correction.
    """
    llm = build_llm()
    task = state.get("current_task", {})
    error = state.get("last_error", "Unknown error")

    messages = [
        SystemMessage(content=CORRECTOR_SYSTEM),
        HumanMessage(
            content=f"Task that failed:\n{task.get('description', '')}\n\nError:\n{error}\n\n"
            "Provide corrected instructions."
        ),
    ]

    response = llm.invoke(messages)
    # Crée une copie de la tâche avec la description corrigée
    # (les autres champs comme agent_type, agent_id, depends_on restent identiques)
    corrected_task = dict(task)
    corrected_task["description"] = response.content

    return {
        "current_task": corrected_task,
        "messages": [AIMessage(content=f"Correction applied for task '{task.get('id', '?')}'.")],
    }


def synthesizer_node(state: OrchestratorState) -> Dict[str, Any]:
    """
    Nœud 5 — Synthèse finale de tous les résultats workers.

    Rôle :
        Compile tous les résultats des workers (dans `state["worker_results"]`)
        en une réponse finale cohérente et structurée en Markdown.

        Le LLM reçoit :
          - La requête originale de l'utilisateur
          - Les résultats de chaque tâche (incluant les vraies données SQL)
          - L'instruction explicite de NE PAS remplacer les données réelles
            par des placeholders (critique pour éviter les régressions)

    Position dans le workflow :
        Appelé depuis `route_after_dispatcher` (backlog épuisé ou limite
        d'itérations atteinte) ou depuis `route_after_worker` (toutes tâches
        terminées). C'est toujours le dernier nœud avant END.

    Modifications de l'état :
        - `final_answer` : réponse Markdown complète retournée à l'utilisateur.
        - `messages` : le même contenu ajouté au fil de messages LangChain.
    """
    llm = build_llm()

    # Formate chaque résultat worker avec sa description pour contexte
    results_text = "\n\n".join(
        f"**{r['task_description']}**\n{r['result']}"
        for r in state.get("worker_results", [])
    )

    messages = [
        SystemMessage(content=SYNTHESIZER_SYSTEM),
        HumanMessage(
            content=f"Original request: {state['user_request']}\n\n"
            f"Worker results:\n{results_text}\n\n"
            "Generate the final comprehensive answer based on the REAL data above."
        ),
    ]

    response = llm.invoke(messages)

    # Extraire le report_id depuis les worker_results si un rapport PDF a été généré
    report_id = None
    for r in state.get("worker_results", []):
        if r.get("report_id"):
            report_id = r["report_id"]
            break

    return {
        "final_answer": response.content,
        "messages": [AIMessage(content=response.content)],
        "report_id": report_id,
    }


def human_feedback_node(state: OrchestratorState) -> Dict[str, Any]:
    """
    Nœud 6 — Point d'interruption pour validation humaine.

    Ce nœud est déclaré dans `interrupt_before=["human_feedback"]` lors de la
    compilation du graphe. Cela signifie que LangGraph interrompt l'exécution
    AVANT d'entrer dans ce nœud, sauvegarde l'état complet via le checkpointer,
    et rend la main à l'appelant (l'API FastAPI).

    L'API peut alors :
      1. Retourner l'état courant au frontend (question + tâche en attente)
      2. Afficher une modale de confirmation à l'utilisateur
      3. Sur validation, reprendre le graphe via `graph.invoke(None, config)`
         qui reprend depuis le checkpoint et exécute ce nœud (qui ne fait que
         remettre `awaiting_human = False`)

    Modifications de l'état :
        - `awaiting_human` : False (réinitialisation après validation humaine).
    """
    # Ce nœud est intentionnellement minimal — toute la logique d'interruption
    # est gérée par LangGraph via interrupt_before à la compilation
    return {"awaiting_human": False}


# ── Fonctions de routage (edges conditionnelles) ─────────────────────────────
# Ces fonctions sont des routeurs purs : elles inspectent l'état et retournent
# une chaîne de caractères correspondant au nom du prochain nœud.
# Elles sont plus rapides et déterministes que de demander au LLM de router.

def route_after_reasoner(state: OrchestratorState) -> Literal["worker", "synthesizer", "human_feedback"]:
    """
    Routage ReAct après chaque décision du raisonneur.

    Priorités :
      1. "synthesizer" si final_answer est déjà renseigné (cannot_fulfill ou
         action final_answer du raisonneur).
      2. "human_feedback" si awaiting_human est True.
      3. "synthesizer" si current_task est None (raisonneur n'a plus d'action)
         ou si la limite max_iterations est atteinte (anti-boucle infinie).
      4. "worker" sinon (raisonneur a choisi call_agent).
    """
    # cannot_fulfill ou erreur de parsing → réponse déjà dans final_answer
    if state.get("final_answer"):
        return "synthesizer"

    if state.get("awaiting_human"):
        return "human_feedback"

    if state.get("current_task") is None:
        return "synthesizer"

    max_iter = state.get("max_iterations", 10)
    if state.get("iteration", 0) >= max_iter:
        logger.warning(
            "Reasoner hit max_iterations (%d) — forcing synthesizer.",
            state.get("iteration", 0),
        )
        return "synthesizer"

    return "worker"


def route_after_worker(state: OrchestratorState) -> Literal["dispatcher", "corrector", "synthesizer"]:
    """
    Décide la suite après l'exécution d'une tâche par le worker.

    Priorités de routage :
      1. "corrector" si la dernière tâche a échoué ET qu'il reste des tentatives
         (< max_retries échecs pour cette même tâche).
      2. "synthesizer" si toutes les tâches du backlog ont un résultat,
         OU si aucune tâche éligible n'existe (dépendances non satisfaites = blocage).
      3. "dispatcher" pour passer à la prochaine tâche du backlog.

    Anti-doublons :
        `completed_ids` est un SET de task_id déjà présents dans worker_results.
        Le dispatcher ne sélectionnera jamais une tâche déjà dans ce set.

    Anti-blocage (deadlock sur dépendances) :
        Si toutes les tâches restantes ont des dépendances non satisfaites, aucune
        ne sera éligible → le dispatcher met current_task=None → on synthétise
        avec ce qu'on a.
    """
    results = state.get("worker_results", [])
    backlog = state.get("task_backlog", [])
    # Tâches ayant au moins une entrée dans worker_results (succès ou échec)
    seen_ids = {r["task_id"] for r in results}
    # Tâches avec au moins un résultat RÉUSSI (pour évaluer les dépendances)
    success_ids = {r["task_id"] for r in results if r.get("success")}

    # ── Gestion des échecs avec retry ──────────────────────────────────────
    if results and not results[-1].get("success", True):
        last_result = results[-1]
        last_task_id = last_result["task_id"]
        last_agent_type = last_result.get("agent_type", "")
        retry_count = sum(1 for r in results if r["task_id"] == last_task_id)
        max_retries = 3
        # Le corrector ne peut pas corriger les erreurs de génération PDF, de
        # navigation Playwright ou de fichiers — ces agents ont leur propre gestion
        # d'erreur interne. On passe directement à la tâche suivante.
        non_correctable = {"report_writer", "file_manager", "powerbi_analyst"}
        if retry_count < max_retries and last_agent_type not in non_correctable:
            return "corrector"
        # Max retries atteints ou type non corrigeable → on la considère terminée (échec)
        logger.warning(
            "Task '%s' (type=%s) failed after %d retries — skipping.",
            last_task_id, last_agent_type, retry_count
        )

    # ── Toutes les tâches backlog ont un résultat → synthèse ───────────────
    all_attempted = all(t["id"] in seen_ids for t in backlog)
    if all_attempted:
        return "synthesizer"

    # ── Détection de blocage : tâches restantes avec dépendances non satisfaites ─
    remaining = [t for t in backlog if t["id"] not in seen_ids]
    # Une tâche est éligible si toutes ses dépendances sont dans success_ids
    any_eligible = any(
        all(dep in success_ids for dep in t.get("depends_on", []))
        for t in remaining
    )
    if not any_eligible:
        # Aucune tâche ne peut avancer → synthèse avec ce qu'on a
        logger.warning(
            "No eligible tasks remaining (dependency deadlock or all failed). "
            "Forcing synthesizer. Remaining: %s",
            [t["id"] for t in remaining],
        )
        return "synthesizer"

    # ── Il reste des tâches éligibles → retour au dispatcher ───────────────
    return "dispatcher"


# ── Construction du graphe LangGraph ─────────────────────────────────────────

def build_orchestrator_graph():
    """
    Assemble et compile le graphe LangGraph orchestrateur.

    Structure du graphe compilé :
        START → planner → dispatcher → [worker | synthesizer | human_feedback]
                                worker → [dispatcher | corrector | synthesizer]
                               corrector → worker
                          human_feedback → dispatcher
                             synthesizer → END

    Checkpointing :
        Utilise `MemorySaver` (stockage en mémoire) comme checkpointer.
        Chaque invocation de nœud est sauvegardée, ce qui permet :
          - L'interruption human-in-the-loop (reprise depuis l'état sauvegardé)
          - L'inspection de l'état intermédiaire pour le débogage
          - La reprise après timeout (si on passait à un checkpointer persistant)

    interrupt_before :
        Le graphe est configuré pour s'interrompre AVANT d'entrer dans
        `human_feedback_node`. L'appelant doit fournir un `config` avec un
        `thread_id` unique pour que le checkpointer puisse isoler les états
        de différentes conversations simultanées.

    Returns:
        Graphe LangGraph compilé, invocable via `.invoke(initial_state, config)`.
    """
    builder = StateGraph(OrchestratorState)

    # ── Enregistrement des nœuds ─────────────────────────────────────────────
    # Architecture ReAct : START → reasoner ⟷ worker (boucle) → synthesizer
    builder.add_node("reasoner", reasoner_node)              # Thought : raisonnement
    builder.add_node("worker", worker_node)                  # Action  : exécution agent
    builder.add_node("synthesizer", synthesizer_node)        # Final Answer : synthèse
    builder.add_node("human_feedback", human_feedback_node)  # Interruption humaine

    # ── Edges fixes ──────────────────────────────────────────────────────────
    builder.add_edge(START, "reasoner")          # Entrée → premier Thought
    builder.add_edge("worker", "reasoner")       # Observation → Thought suivant
    builder.add_edge("human_feedback", "reasoner")  # Après clarification → Thought
    builder.add_edge("synthesizer", END)

    # ── Edge conditionnelle depuis le raisonneur ──────────────────────────────
    # Le raisonneur décide : call_agent → worker | final_answer → synthesizer
    builder.add_conditional_edges(
        "reasoner",
        route_after_reasoner,
        {
            "worker": "worker",
            "synthesizer": "synthesizer",
            "human_feedback": "human_feedback",
        },
    )

    # ── Compilation avec checkpointer et points d'interruption ──────────────
    checkpointer = MemorySaver()
    graph = builder.compile(
        checkpointer=checkpointer,
        # Interruption AVANT human_feedback (permet la validation externe)
        interrupt_before=["human_feedback"],
    )

    return graph
