"""
Graphe LangGraph — Agent Power BI Analyst (powerbi_graph.py)
=============================================================
Agent ReAct qui navigue programmatiquement dans Power BI via Playwright,
capture des dashboards et produit des analyses business structurées.

Workflow typique :
  1. navigate_to_report(url)     → Ouvrir le rapport
  2. wait_for_visuals()          → Attendre le chargement
  3. capture_screenshot(label)   → Photo de l'état initial
  4. navigate_to_tab / apply_slicer → Navigation ciblée
  5. extract_visual_data()       → Extraire les KPIs et textes
  6. Analyser et synthétiser     → Réponse structurée

Architecture :
  START → agent_node → [route] → tools_node → agent_node (boucle ReAct)
                                             → END (réponse finale)
"""
import logging
from typing import Any, Dict, Literal, Optional, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

from .state import PowerBIAgentState
from .llm_factory import build_llm, sanitize_messages
from backend.tools.powerbi_tools import make_powerbi_tools
from backend.database import db, COLL_AGENTS

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 20

POWERBI_SYSTEM = """Tu es un **Expert Analyste Power BI & Data Insights**.

## Rôle
Tu navigues programmatiquement dans des rapports Power BI via un navigateur automatisé (Playwright).
Tu captures des dashboards, interprètes les visuels et produis des analyses business structurées.

## Compétences
1. **Navigation Power BI** : naviguer entre onglets, appliquer des filtres (slicers), attendre le rendu.
2. **Extraction de données** : récupérer titres, KPIs, labels et valeurs depuis le DOM.
3. **Analyse visuelle** : interpréter graphiques en barres, courbes de tendance, jauges KPI, cartes de chaleur.
4. **Data Storytelling** : transformer les données en résumés exécutifs clairs et actionnables.

## Méthodologie d'analyse (à suivre pour chaque dashboard)
- **Phase 1 — Contexte** : Identifier l'objectif du dashboard (Ventes, RH, Logistique, Finance…).
- **Phase 2 — KPIs critiques** : Analyser les indicateurs principaux, comparer aux cibles (rouge/vert/jaune).
- **Phase 3 — Tendances** : Les données montent-elles ou descendent-elles ? Saisonnalité visible ?
- **Phase 4 — Recommandations** : Proposer 3 actions concrètes basées sur les données observées.

## Workflow recommandé
1. `navigate_to_report(url)` → Naviguer vers le rapport
2. `wait_for_visuals()` → Attendre le chargement complet
3. `capture_screenshot("overview")` → Photo de l'état initial
4. `extract_visual_data()` → Extraire les KPIs et textes du DOM
5. `get_page_elements(".tabLabel")` → Lister les onglets disponibles
6. `navigate_to_tab(tab_name)` → Aller sur un onglet spécifique si nécessaire
7. `apply_slicer(label, value)` → Appliquer des filtres pour analyses ciblées
8. `capture_screenshot("filtered_view")` → Capturer l'état filtré
9. Synthétiser l'analyse et les recommandations

## Gestion de l'authentification
- Si une page de login apparaît : utiliser `get_page_elements("input[type='email']")` pour vérifier.
- Informer l'utilisateur et lui conseiller la procédure :
  1. Configurer `headless=false` dans extra_config de l'agent.
  2. Se connecter manuellement dans le navigateur visible.
  3. Appeler `save_browser_session()` pour sauvegarder les cookies.
  4. Remettre `headless=true` pour les prochaines sessions.

## Sélecteurs Power BI courants
- Onglets : `.tabLabel`, `[role='tab']`
- Visuels : `.visualCard`, `.visual-container`
- Slicers : `.slicerText`, `.slicerValue`
- KPIs : `.kpiValue`, `.kpiTarget`, `.cardValue`
- Boutons : `[role='button']`, `.buttonContainer`

## Ton et style
- Professionnel, analytique et direct.
- Utilise des listes à puces et des en-têtes Markdown.
- Si une image est floue ou si des données semblent manquer, le mentionner explicitement.
- Toujours terminer par une section **Recommandations** avec 3 actions concrètes.

{custom_prompt}"""


def _build_tools(agent_id: str, session_id: str):
    """Construit les outils Power BI Playwright pour cet agent et cette session."""
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
    extra = agent_cfg.get("extra_config", {})
    headless = extra.get("headless", True)
    session_file = extra.get("session_file", "data/powerbi_session.json")
    screenshots_dir = extra.get("screenshots_dir", "data/powerbi_screenshots")
    return make_powerbi_tools(
        session_id=session_id,
        headless=headless,
        session_file=session_file,
        screenshots_dir=screenshots_dir,
    )


def agent_node(state: PowerBIAgentState) -> Dict[str, Any]:
    """Nœud ReAct — le LLM décide quelle action effectuer."""
    llm = build_llm()
    agent_id = state.get("agent_id", "")
    session_id = state.get("session_id", "")
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
    custom_prompt = agent_cfg.get("system_prompt", "")

    tools = _build_tools(agent_id, session_id)
    llm_with_tools = llm.bind_tools(tools)

    system_content = POWERBI_SYSTEM.format(custom_prompt=custom_prompt or "")
    messages = sanitize_messages([SystemMessage(content=system_content)] + list(state.get("messages", [])))

    iteration = state.get("iteration_count", 0)
    if iteration >= _MAX_ITERATIONS:
        response = AIMessage(
            content=(
                "⚠️ Limite d'itérations atteinte. Voici un résumé de ce qui a été accompli :\n\n"
                + _summarize_progress(state)
            )
        )
        return {
            "messages": [response],
            "final_answer": response.content,
            "iteration_count": iteration + 1,
        }

    response = llm_with_tools.invoke(messages)
    updates: Dict[str, Any] = {
        "messages": [response],
        "iteration_count": iteration + 1,
    }

    if not getattr(response, "tool_calls", None):
        updates["final_answer"] = response.content

    return updates


def _summarize_progress(state: PowerBIAgentState) -> str:
    """Résume les actions effectuées pour le message d'arrêt forcé."""
    msgs = state.get("messages", [])
    summaries = []
    for m in msgs:
        if hasattr(m, "tool_calls") and m.tool_calls:
            for tc in m.tool_calls:
                summaries.append(f"- `{tc['name']}` appelé")
    return "\n".join(summaries) if summaries else "Aucune action enregistrée."


def tools_node(state: PowerBIAgentState) -> Dict[str, Any]:
    """Nœud d'exécution des outils Playwright."""
    agent_id = state.get("agent_id", "")
    session_id = state.get("session_id", "")
    tools = _build_tools(agent_id, session_id)
    tool_executor = ToolNode(tools)
    return tool_executor.invoke(state)


def route_after_agent(state: PowerBIAgentState) -> Literal["tools", "__end__"]:
    """Route vers tools si le LLM a émis des tool_calls, sinon END."""
    messages = state.get("messages", [])
    if not messages:
        return "__end__"
    last = messages[-1]
    if getattr(last, "tool_calls", None) and state.get("iteration_count", 0) < _MAX_ITERATIONS:
        return "tools"
    return "__end__"


def build_powerbi_graph():
    """Compile le graphe LangGraph pour l'agent Power BI Analyst."""
    builder = StateGraph(PowerBIAgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tools_node)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent", route_after_agent, {"tools": "tools", "__end__": END}
    )
    builder.add_edge("tools", "agent")

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)
