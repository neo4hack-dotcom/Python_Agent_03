"""
Définitions des états partagés entre les graphes LangGraph.

Dans LangGraph, chaque graphe possède un « état » (State) qui est un dictionnaire
typé (TypedDict) circulant de nœud en nœud. Chaque nœud reçoit l'état complet en
entrée et retourne un dictionnaire partiel contenant uniquement les clés qu'il
modifie. LangGraph fusionne ensuite automatiquement ce dictionnaire partiel dans
l'état global (pattern « reducer »).

Le champ `messages` utilise le réducteur spécial `add_messages` de LangGraph :
au lieu de remplacer la liste, il l'accumule — comme un fil de conversation
persistant tout au long du graphe.
"""
from typing import Annotated, Any, Dict, List, Optional, Sequence
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class OrchestratorState(TypedDict):
    """
    État du graphe orchestrateur (orchestrator_graph.py).

    Ce graphe prend en charge les workflows multi-agents :
      1. Le planner décompose la requête en sous-tâches.
      2. Le dispatcher choisit la prochaine tâche à exécuter.
      3. Le worker exécute la tâche (via un LLM générique ou un agent spécialiste).
      4. Le corrector corrige les erreurs avant un éventuel retry.
      5. Le synthesizer compile tous les résultats en une réponse finale.

    Cycle principal :
      START → planner → dispatcher ⇄ worker ⇄ corrector
                       ↓ (toutes tâches terminées)
                    synthesizer → END
    """

    # ── Fil de messages LangChain ────────────────────────────────────────────
    # `add_messages` est un réducteur : les nouveaux messages sont AJOUTÉS à la
    # liste existante plutôt que de la remplacer. Cela conserve l'historique
    # complet des échanges entre nœuds (plans, résultats intermédiaires, etc.).
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # ── Entrée utilisateur ───────────────────────────────────────────────────
    # La requête brute saisie par l'utilisateur. Immutable après initialisation.
    user_request: str

    # ── Backlog de tâches (planification) ───────────────────────────────────
    # Liste ordonnée de tâches générée par `planner_node`.
    # Chaque tâche est un dict contenant au minimum :
    #   { "id": str, "description": str, "agent_type": str,
    #     "agent_id": str|None, "priority": int,
    #     "depends_on": List[str], "requires_human_approval": bool }
    task_backlog: List[Dict[str, Any]]

    # ── Tâche en cours d'exécution ───────────────────────────────────────────
    # Renseigné par `task_dispatcher_node` avant chaque appel à `worker_node`.
    # Vaut None quand toutes les tâches sont terminées (signal pour basculer
    # vers le synthesizer).
    current_task: Optional[Dict[str, Any]]

    # ── Résultats collectés par les workers ──────────────────────────────────
    # Liste accumulée de dicts, un par tâche exécutée :
    #   { "task_id": str, "task_description": str, "agent_type": str,
    #     "result": str, "success": bool,
    #     "sql_executed": str|None, "row_count": int|None,
    #     "error": str|None }
    # Pour les agents analytiques, `result` contient les données réelles
    # issues de la base de données (pas un placeholder LLM).
    worker_results: List[Dict[str, Any]]

    # ── Réponse finale ───────────────────────────────────────────────────────
    # Narrative Markdown générée par `synthesizer_node` en compilant tous les
    # résultats des workers. C'est la valeur retournée à l'utilisateur.
    final_answer: Optional[str]

    # ── Human-in-the-loop ───────────────────────────────────────────────────
    # Mis à True par `task_dispatcher_node` lorsqu'une tâche porte le flag
    # `requires_human_approval`. Le graphe s'interrompt alors sur le nœud
    # `human_feedback` (interrupt_before) et attend une intervention externe.
    awaiting_human: bool

    # ── Compteur d'itérations (protection anti-boucle) ──────────────────────
    # Incrémenté à chaque passage dans `task_dispatcher_node`.
    # Quand il atteint `max_iterations`, le graphe bascule directement vers
    # le synthesizer sans exécuter de nouvelles tâches.
    iteration: int

    # ── Limite d'itérations ──────────────────────────────────────────────────
    # Valeur configurable (défaut : 10). Évite les boucles infinies en cas de
    # plan mal formé ou de dépendances cycliques entre tâches.
    max_iterations: int

    # ── Dernière erreur (utilisée par corrector_node) ────────────────────────
    # Contient le message d'erreur de la dernière tâche échouée.
    # `corrector_node` l'analyse pour générer des instructions corrigées,
    # puis le remet à None après application de la correction.
    last_error: Optional[str]

    # ── Identifiants de session ──────────────────────────────────────────────
    # `agent_id` : ID de l'agent orchestrateur dans la base de données locale.
    # `session_id` : ID de la session de conversation (utilisé par le
    #   checkpointer MemorySaver pour isoler les états entre conversations).
    agent_id: str
    session_id: str


class AnalystState(TypedDict):
    """
    État du graphe analyste ClickHouse/Oracle (analyst_graph.py).

    Ce graphe gère un workflow SQL avec retry automatique :
      analyst_node  →  sql_tool_node  →  synthesizer_node
           ↑                ↓ (erreur, < max_retries)
           └────────────────┘
           (error_node si max_retries atteint)

    Le nœud `analyst_node` génère du SQL en connaissant le schéma de la base.
    Le nœud `sql_tool_node` exécute ce SQL contre la vraie base de données.
    En cas d'erreur SQL, le résultat (code d'erreur + SQL fautif) est renvoyé
    à `analyst_node` qui corrige la requête automatiquement.
    """

    # ── Fil de messages LangChain ────────────────────────────────────────────
    # Accumulateur de messages (même réducteur que OrchestratorState).
    # Contient : question initiale, SQL généré, résultats, erreurs éventuelles.
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # ── Question analytique de l'utilisateur ────────────────────────────────
    # La question en langage naturel à laquelle le graphe doit répondre
    # en exécutant du SQL. Ex : "Combien de commandes par jour ce mois-ci ?"
    user_question: str

    # ── SQL généré par l'agent ───────────────────────────────────────────────
    # Renseigné (et potentiellement re-généré à chaque retry) par `analyst_node`.
    # Le SQL est nettoyé des balises markdown (```sql) avant stockage.
    generated_sql: Optional[str]

    # ── Résultat brut de la requête SQL ─────────────────────────────────────
    # Dict retourné par `ClickHouseSQLTool.execute()` ou `OracleSQLTool.execute()` :
    #   { "success": bool, "columns": List[str], "rows": List[List],
    #     "row_count": int, "markdown_table": str,
    #     "sql_executed": str, "error": str|None, "warning": str|None }
    query_result: Optional[Dict[str, Any]]

    # ── Compteur de tentatives ───────────────────────────────────────────────
    # Incrémenté par `sql_tool_node` à chaque échec d'exécution SQL.
    # Quand retry_count >= max_retries, le graphe bascule vers `error_node`.
    retry_count: int

    # ── Nombre maximum de tentatives ────────────────────────────────────────
    # Configurable par agent (défaut : 3). Au-delà, `error_node` renvoie un
    # message d'erreur formaté avec le dernier SQL tenté.
    max_retries: int

    # ── Dernière erreur SQL ──────────────────────────────────────────────────
    # Message d'erreur retourné par la base de données.
    # Injecté dans le prompt de `analyst_node` au retry suivant pour que
    # le LLM puisse diagnostiquer et corriger le SQL.
    last_error: Optional[str]

    # ── Réponse finale ───────────────────────────────────────────────────────
    # Narrative Markdown générée par `synthesizer_node`, comprenant :
    #   - Réponse à la question en langage naturel
    #   - Insights clés issus des données
    #   - Le SQL utilisé (dans un bloc de code)
    #   - Éventuels avertissements (ex : résultat tronqué)
    final_answer: Optional[str]

    # ── Contexte de schéma pré-injecté ──────────────────────────────────────
    # Chaîne décrivant les tables et colonnes disponibles dans la base.
    # Générée par `_auto_schema_context()` dans l'orchestrateur avant
    # d'appeler ce graphe, ou par l'endpoint /api/agents/{id}/chat en
    # mode standalone. Injectée dans le system prompt de `analyst_node`.
    schema_context: Optional[str]

    # ── Identifiants de configuration ────────────────────────────────────────
    # `agent_id` : ID de l'agent analyste dans la base locale — utilisé pour
    #   retrouver la connexion DB (host, port, user, password, database)
    #   et les paramètres de l'agent (row_limit, system_prompt personnalisé).
    # `session_id` : ID de session pour le checkpointer LangGraph.
    agent_id: str
    session_id: str
