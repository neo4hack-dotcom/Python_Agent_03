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

    # ── ID du rapport PDF généré ─────────────────────────────────────────────
    # Renseigné par `synthesizer_node` s'il détecte un résultat report_writer
    # dans les worker_results. Utilisé par le runner SSE pour émettre un
    # événement `pdf_ready` au frontend afin d'afficher le bouton de téléchargement.
    report_id: Optional[str]

    # ── Identifiants de session ──────────────────────────────────────────────
    # `agent_id` : ID de l'agent orchestrateur dans la base de données locale.
    # `session_id` : ID de la session de conversation (utilisé par le
    #   checkpointer MemorySaver pour isoler les états entre conversations).
    agent_id: str
    session_id: str


class DataAnalystState(TypedDict):
    """
    État du graphe Analyste de Données (data_analyst_graph.py).

    Cet agent est un expert polyvalent capable de :
      - Analyser des données statistiquement (distributions, corrélations, outliers)
      - Réaliser un profiling de données (qualité, complétude, cardinalité)
      - Identifier des tendances et patterns temporels
      - Calculer des KPIs et métriques métier
      - Produire des recommandations business actionnables

    Contrairement à l'AnalystState (centré sur le SQL), le DataAnalystState
    gère un workflow en deux temps :
      1. Planification : le LLM détermine le type d'analyse et les données nécessaires
      2. Optionnel : exécution SQL si l'agent a une connexion DB configurée
      3. Analyse approfondie sur les données récupérées
      4. Synthèse business

    L'agent fonctionne AUSSI sans connexion DB (analyse de données fournies
    dans la conversation, datasets copiés-collés, questions business générales).

    Cycle principal :
      START → planner → [sql_executor →] analyst → synthesizer → END
    """

    # ── Fil de messages LangChain ────────────────────────────────────────────
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # ── Question de l'utilisateur ────────────────────────────────────────────
    # La question analytique en langage naturel. Ex :
    #   "Analyse la distribution des ventes par région ce trimestre"
    #   "Quels sont les produits qui sous-performent ?"
    user_question: str

    # ── Plan d'analyse (output du planner_node) ──────────────────────────────
    # Dict JSON parsé contenant :
    #   { "analysis_type": "statistical|profiling|trends|kpi|business|mixed",
    #     "approach": str, "sql_queries": [...], "metrics_to_compute": [...],
    #     "business_context": str }
    analysis_plan: Optional[Dict[str, Any]]

    # ── Requêtes SQL planifiées ───────────────────────────────────────────────
    # Liste de dicts : [{"id": str, "description": str, "sql": str}]
    # Générée par planner_node si l'agent a une connexion DB.
    # Vide si l'agent n'a pas de connexion ou si aucune donnée SQL n'est requise.
    sql_queries: Optional[List[Dict[str, Any]]]

    # ── Résultats des requêtes SQL ────────────────────────────────────────────
    # Liste de dicts par requête :
    #   { "query_id": str, "description": str, "sql": str,
    #     "success": bool, "columns": list, "rows": list,
    #     "row_count": int, "markdown_table": str, "error": str|None }
    # Renseigné par sql_executor_node, vide si pas de connexion DB.
    data_results: Optional[List[Dict[str, Any]]]

    # ── Analyse intermédiaire ────────────────────────────────────────────────
    # Output brut de analyst_node : analyse statistique/fonctionnelle détaillée
    # basée sur les données fetched. Utilisé comme input du synthesizer_node.
    analysis_output: Optional[str]

    # ── Réponse finale ───────────────────────────────────────────────────────
    # Narrative Markdown avec : résumé exécutif, insights clés, analyse détaillée,
    # recommandations business, limites et points d'attention.
    final_answer: Optional[str]

    # ── Contexte de schéma ───────────────────────────────────────────────────
    # Informations sur les tables et colonnes disponibles dans la DB.
    # Injecté dans le prompt du planner pour l'aider à écrire du SQL pertinent.
    # Généré par _auto_schema_context() avant le lancement du graphe.
    schema_context: Optional[str]

    # ── Identifiants ─────────────────────────────────────────────────────────
    agent_id: str
    session_id: str

    # ── Gestion d'erreurs ────────────────────────────────────────────────────
    last_error: Optional[str]


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

    # ── Compteur d'itérations ReAct ──────────────────────────────────────────
    # Incrémenté à chaque passage dans `agent_react_node` (graphe ReAct).
    # Sert de garde anti-boucle : quand iteration_count >= max_iterations,
    # le graphe sort de la boucle tools ↔ agent même si l'agent veut continuer.
    # Valeur typique : 0 initialement, limite à 8 iterations (4 cycles agent+tools).
    iteration_count: int


class FileAgentState(TypedDict):
    """
    État du graphe File Manager (file_graph.py).

    Agent ReAct pour la navigation et la gestion de fichiers/répertoires.
    Supporte : txt, md, csv, xlsx, docx, parquet, json, yaml, py, sql…
    Les opérations destructives (write, delete, move) requièrent confirmation utilisateur
    via le mécanisme confirmed=True/False des outils.
    """

    # ── Fil de messages LangChain ────────────────────────────────────────────
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # ── Question / demande de l'utilisateur ──────────────────────────────────
    user_request: str

    # ── Réponse finale ───────────────────────────────────────────────────────
    final_answer: Optional[str]

    # ── Compteur d'itérations ReAct (garde anti-boucle) ─────────────────────
    iteration_count: int

    # ── Identifiants ─────────────────────────────────────────────────────────
    agent_id: str
    session_id: str


class PowerBIAgentState(TypedDict):
    """
    État du graphe Power BI Analyst (powerbi_graph.py).

    Agent ReAct qui navigue dans Power BI via Playwright,
    capture des screenshots et produit des analyses business structurées.
    """

    # ── Fil de messages LangChain ────────────────────────────────────────────
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # ── Demande de l'utilisateur ──────────────────────────────────────────────
    user_request: str

    # ── Réponse finale ───────────────────────────────────────────────────────
    final_answer: Optional[str]

    # ── Compteur d'itérations ReAct (garde anti-boucle) ─────────────────────
    iteration_count: int

    # ── Identifiants ─────────────────────────────────────────────────────────
    agent_id: str
    session_id: str


class DataQualityState(TypedDict):
    """
    État du graphe Data Quality (data_quality_graph.py).

    Pipeline linéaire : schema → stats → [volumetric] → llm_analysis → synthesizer
    Entrée : message JSON structuré avec table, columns, sample_size, row_filter, time_column.
    """
    messages: Annotated[Sequence[BaseMessage], add_messages]
    # Paramètres parsés depuis le message JSON
    table: str
    columns: List[Dict[str, Any]]
    sample_size: int          # 0 = full scan
    row_filter: Optional[str]
    time_column: Optional[str]
    db_type: str              # "clickhouse" ou "oracle"
    # Résultats intermédiaires
    schema_info: Optional[Dict[str, Any]]   # {col: {raw_type, col_type, comment}}
    column_stats: Optional[Dict[str, Any]]  # {col: {stat: value, ...}}
    volumetric_stats: Optional[Dict[str, Any]]
    # Sortie
    llm_analysis: Optional[str]
    final_answer: Optional[str]
    # Infra
    agent_id: str
    session_id: str
    last_error: Optional[str]


class ReportState(TypedDict):
    """
    État du graphe Rédacteur de Rapports (report_graph.py).

    Ce graphe transforme une conversation ou une demande en un rapport
    d'analyse professionnel exportable en PDF :
      1. report_writer_node : le LLM génère un rapport Markdown structuré
         (couverture, résumé exécutif, analyse, insights, recommandations)
      2. pdf_node : weasyprint convertit le Markdown en PDF professionnel

    Cycle :
      START → report_writer_node → pdf_node → END

    Utilisation :
      - Agent standalone 📄 "Rapport PDF" : reçoit la demande + historique session
      - Délégation par l'orchestrateur : quand l'user demande un rapport de synthèse
    """

    messages: Annotated[Sequence[BaseMessage], add_messages]

    # ── Entrée ─────────────────────────────────────────────────────────────────
    # Demande de l'utilisateur (ex: "Génère un rapport PDF de cette analyse").
    user_request: str

    # Historique de la conversation sérialisé en texte.
    # Contient tous les échanges user/assistant de la session courante.
    # Utilisé par le LLM pour construire le rapport à partir des résultats.
    session_context: Optional[str]

    # ── Rapport généré ─────────────────────────────────────────────────────────
    # Contenu Markdown du rapport produit par report_writer_node.
    # Structure attendue : titre, résumé exécutif, analyse, résultats, recommandations.
    report_markdown: Optional[str]

    # ── PDF ────────────────────────────────────────────────────────────────────
    # Chemin absolu vers le PDF généré par pdf_node (ex: data/reports/rapport_UUID.pdf).
    pdf_path: Optional[str]

    # UUID du rapport — utilisé dans l'URL de téléchargement /api/report/{id}/download.
    report_id: Optional[str]

    # ── Réponse finale chat ────────────────────────────────────────────────────
    # Message court affiché dans le chat pour annoncer la disponibilité du PDF.
    final_answer: Optional[str]

    # ── Identifiants ───────────────────────────────────────────────────────────
    agent_id: str
    session_id: str
