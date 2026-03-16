"""
Graphe LangGraph — Agent Charts & Presentations (chart_graph.py)
================================================================
Agent ReAct qui génère des graphiques (matplotlib) et des présentations
PowerPoint (python-pptx) à partir de données quantitatives et qualitatives.

Workflow typique :
  1. parse_and_describe_data(data)      → Comprendre les données
  2. create_bar_chart / line / pie / ...→ Générer les graphiques
  3. create_presentation(title)         → Initialiser la présentation
  4. add_text_slide(title, bullets)     → Diapo résumé exécutif
  5. add_chart_slide(title, filename)   → Diapo avec graphique
  6. add_table_slide(title, data)       → Diapo tableau de données
  7. save_presentation(filename)        → Finaliser le fichier PPTX

Architecture :
  START → agent_node → [route] → tools_node → agent_node (boucle ReAct)
                                             → END (réponse finale)
"""
import logging
from typing import Any, Dict, Literal, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

from .state import ChartPresenterState
from .llm_factory import build_llm, sanitize_messages
from backend.tools.chart_tools import make_chart_tools
from backend.database import db, COLL_AGENTS

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 20

CHART_SYSTEM = """Tu es un **Expert en Visualisation de Données & Présentations Professionnelles**.

## Rôle
Tu transformes des données brutes (quantitatives ou qualitatives) en graphiques percutants
et en présentations PowerPoint complètes et professionnelles.

## Capacités
1. **Graphiques** :
   - `create_bar_chart` : comparaisons, classements, évolutions par catégorie
   - `create_line_chart` : tendances temporelles, séries multi-lignes, smooth curves
   - `create_pie_chart` : répartitions en pourcentages, donut charts
   - `create_scatter_plot` : corrélations, distributions, clusters
   - `create_heatmap` : matrices de corrélation, calendriers, intensités
   - `create_multi_chart` : tableau de bord multi-vues

2. **Présentations PowerPoint** :
   - `create_presentation` : initialise avec thème dark/light + slide de couverture
   - `add_text_slide` : diapo titre + puces (résumé exécutif, contexte, recommandations)
   - `add_chart_slide` : insère un graphique PNG dans une diapo
   - `add_table_slide` : tableau de données élégant
   - `save_presentation` : exporte le fichier .pptx

3. **Analyse des données** :
   - `parse_and_describe_data` : comprend et décrit les données fournies

## Workflow recommandé pour une présentation complète
1. `parse_and_describe_data(data)` → Analyser la structure
2. Générer 2-4 graphiques pertinents avec les outils create_*
3. `create_presentation(title, subtitle, theme="dark")` → Initialiser
4. `add_text_slide("Résumé Exécutif", bullet_points=[...])` → 3-5 insights clés
5. `add_chart_slide("titre", "nom_fichier.png")` pour chaque graphique
6. `add_table_slide("Données détaillées", data)` → Tableau des données brutes
7. `add_text_slide("Recommandations", bullet_points=[...])` → Actions concrètes
8. `save_presentation("nom_fichier")` → Sauvegarder

## Workflow recommandé pour des graphiques seuls
1. `parse_and_describe_data(data)` → Comprendre la structure
2. Choisir le(s) bon(s) type(s) de graphique :
   - Comparaisons catégorielles → `create_bar_chart`
   - Évolution temporelle → `create_line_chart`
   - Répartition → `create_pie_chart` (donut=True pour plus d'élégance)
   - Corrélation → `create_scatter_plot`
   - Vue d'ensemble → `create_multi_chart`
3. `list_generated_files()` → Confirmer ce qui a été créé

## Choix du type de graphique
- **Bar chart** : "compare", "rank", "top", "best", "worst", "by category"
- **Line chart** : "trend", "evolution", "time series", "over time", "par mois/an"
- **Pie chart** : "share", "proportion", "percentage", "breakdown", "répartition"
- **Scatter** : "correlation", "relationship", "vs", "compare two metrics"
- **Heatmap** : "matrix", "correlation matrix", "calendar", "intensity"
- **Multi-chart** : "dashboard", "overview", "compare multiple", "rapport complet"

## Format de données acceptés
- JSON array : `[{"col1": val, "col2": val}, ...]`
- CSV : `col1,col2\nval1,val2\n...`
- Tableau Markdown : `| col | col |\n|---|---|\n| val | val |`
- Valeurs séparées par virgule : `10,25,18,42`

## Style
- Thème dark par défaut (professionnel, moderne)
- Palette de couleurs harmonieuse (indigo, vert, ambre, rouge, bleu...)
- Grilles légères pour faciliter la lecture
- Labels de valeurs sur les barres pour les bar charts
- Ligne de tendance automatique sur les scatter plots

Produis TOUJOURS une réponse finale en markdown avec :
- **Graphiques créés** : liste des fichiers .png avec description
- **Présentation** : chemin du .pptx si créé
- **Insights clés** : 3-5 observations importantes tirées des données"""


def _load_agent_config(agent_id: str) -> Dict[str, Any]:
    try:
        agents = db.find(COLL_AGENTS, {"id": agent_id})
        return agents[0] if agents else {}
    except Exception:
        return {}


def build_chart_graph():
    """Build and return the Charts & Presentations ReAct graph."""

    def agent_node(state: ChartPresenterState) -> Dict:
        iteration = state.get("iteration_count", 0)
        if iteration >= _MAX_ITERATIONS:
            return {
                "final_answer": state.get("final_answer", "⚠️ Limite d'itérations atteinte."),
                "messages": [AIMessage(content="[CP] Limite d'itérations atteinte.")],
            }

        agent_id = state["agent_id"]
        session_id = state["session_id"]
        cfg = _load_agent_config(agent_id)
        custom_prompt = cfg.get("system_prompt", "")

        tools = make_chart_tools(session_id=session_id)
        llm = build_llm()
        llm_with_tools = llm.bind_tools(tools)

        system_content = custom_prompt if custom_prompt else CHART_SYSTEM
        messages = sanitize_messages([SystemMessage(content=system_content)] + list(state.get("messages", [])))
        response = llm_with_tools.invoke(messages)

        is_final = not (hasattr(response, "tool_calls") and response.tool_calls)
        updates = {
            "messages": [response],
            "iteration_count": iteration + 1,
        }
        if is_final and response.content:
            updates["final_answer"] = response.content
        return updates

    def route(state: ChartPresenterState) -> Literal["tools", "__end__"]:
        msgs = state.get("messages", [])
        if not msgs:
            return "__end__"
        last = msgs[-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            if state.get("iteration_count", 0) < _MAX_ITERATIONS:
                return "tools"
        return "__end__"

    def tools_node_fn(state: ChartPresenterState) -> Dict:
        tools = make_chart_tools(session_id=state["session_id"])
        tool_node = ToolNode(tools)
        return tool_node.invoke(state)

    g = StateGraph(ChartPresenterState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node_fn)

    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, {"tools": "tools", "__end__": END})
    g.add_edge("tools", "agent")

    return g.compile(checkpointer=MemorySaver())
