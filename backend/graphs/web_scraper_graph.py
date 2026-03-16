"""
Graphe LangGraph — Agent Web Scraper (web_scraper_graph.py)
===========================================================
Agent ReAct qui navigue sur internet via Playwright, extrait des données
structurées depuis des pages web et les analyse.

Workflow typique :
  1. navigate_to_url(url)           → Ouvrir la page
  2. get_page_text()                → Lire le contenu
  3. extract_elements(selector)     → Extraire des éléments ciblés
  4. extract_tables()               → Récupérer les tableaux
  5. take_screenshot("capture")     → Documenter visuellement
  6. save_scraped_data(content, fn) → Persister les données
  7. Analyser et synthétiser        → Réponse structurée avec insights

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

from .state import WebScraperState
from .llm_factory import build_llm, sanitize_messages
from backend.tools.web_scraper_tools import make_web_scraper_tools
from backend.database import db, COLL_AGENTS

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 25

WEB_SCRAPER_SYSTEM = """Tu es un **Expert Web Scraper & Data Extraction Analyst**.

## Rôle
Tu navigues sur des pages web via un navigateur automatisé (Playwright) pour extraire,
structurer et analyser des données. Tu fournis des insights actionnables à partir des
informations collectées.

## Capacités
1. **Navigation web** : accéder à des URLs, cliquer, remplir des formulaires, scroller.
2. **Extraction de données** : texte, liens, tableaux HTML, attributs d'éléments (CSS selector).
3. **Screenshots** : documenter visuellement les pages visitées.
4. **Persistance** : sauvegarder les données scrapées en JSON/CSV/TXT.
5. **Analyse** : interpréter et synthétiser les données extraites.

## Workflow recommandé
1. `navigate_to_url(url)` → Naviguer vers la page cible
2. `get_page_text()` → Lire le contenu global pour comprendre la structure
3. `extract_elements(selector, attribute)` → Cibler les données précises
4. `extract_tables()` → Récupérer les tableaux si présents
5. `take_screenshot("label")` → Documenter
6. `save_scraped_data(content, filename, format)` → Sauvegarder si demandé
7. Synthétiser les données en réponse structurée

## Bonnes pratiques
- Commence TOUJOURS par `navigate_to_url` avant toute autre action
- Utilise `get_page_text()` pour comprendre la structure avant d'utiliser des sélecteurs CSS
- Les sélecteurs CSS courants : `h1`, `h2`, `.classe`, `#id`, `table td`, `a[href]`
- Pour des listes de prix : cherche `span`, `.price`, `[data-price]`
- Pour des articles : cherche `article`, `.post`, `.item`, `li`
- Prends un screenshot pour documenter chaque page importante visitée

## Limitations
- Uniquement les URLs configurées dans `allowed_urls` de l'agent (si renseigné)
- Pas d'authentification OAuth/SAML automatique (sessions manuelles possibles)
- Contenu dynamique JavaScript : utilise `wait_for_element()` si nécessaire

## Format de réponse
Fournis toujours :
- **Résumé** : ce qui a été extrait et depuis quelle(s) URL(s)
- **Données** : les données structurées (tables, listes, valeurs clés)
- **Insights** : observations et recommandations basées sur les données
- **Fichiers** : si des données ont été sauvegardées, mentionne le chemin"""


def _load_agent_config(agent_id: str) -> Dict[str, Any]:
    """Load agent configuration from DB."""
    try:
        agents = db.find(COLL_AGENTS, {"id": agent_id})
        return agents[0] if agents else {}
    except Exception:
        return {}


def build_web_scraper_graph():
    """Build and return the Web Scraper ReAct graph."""

    def agent_node(state: WebScraperState) -> Dict:
        iteration = state.get("iteration_count", 0)
        if iteration >= _MAX_ITERATIONS:
            return {
                "final_answer": state.get("final_answer", "⚠️ Limite d'itérations atteinte."),
                "messages": [AIMessage(content="[WS] Limite d'itérations atteinte.")],
            }

        agent_id = state["agent_id"]
        session_id = state["session_id"]
        cfg = _load_agent_config(agent_id)
        extra = cfg.get("extra_config", {})

        allowed_urls = extra.get("allowed_urls", [])
        headless = extra.get("headless", True)
        screenshots_dir = extra.get("screenshots_dir", "data/web_scraper_screenshots")
        custom_prompt = cfg.get("system_prompt", "")

        tools = make_web_scraper_tools(
            agent_id=agent_id,
            session_id=session_id,
            allowed_urls=allowed_urls,
            headless=headless,
            screenshots_dir=screenshots_dir,
        )

        llm = build_llm()
        llm_with_tools = llm.bind_tools(tools)

        system_content = custom_prompt if custom_prompt else WEB_SCRAPER_SYSTEM
        if allowed_urls:
            system_content += f"\n\n## URLs autorisées pour cette session\n" + "\n".join(f"- {u}" for u in allowed_urls)

        messages = sanitize_messages([SystemMessage(content=system_content)] + list(state.get("messages", [])))
        response = llm_with_tools.invoke(messages)

        # Detect final answer — no more tool calls
        is_final = not (hasattr(response, "tool_calls") and response.tool_calls)
        updates = {
            "messages": [response],
            "iteration_count": iteration + 1,
        }
        if is_final and response.content:
            updates["final_answer"] = response.content
        return updates

    def route(state: WebScraperState) -> Literal["tools", "__end__"]:
        msgs = state.get("messages", [])
        if not msgs:
            return "__end__"
        last = msgs[-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            if state.get("iteration_count", 0) < _MAX_ITERATIONS:
                return "tools"
        return "__end__"

    # Build tools node dynamically using a wrapper that loads config at call time
    def tools_wrapper(state: WebScraperState) -> Dict:
        agent_id = state["agent_id"]
        session_id = state["session_id"]
        cfg = _load_agent_config(agent_id)
        extra = cfg.get("extra_config", {})
        tools = make_web_scraper_tools(
            agent_id=agent_id,
            session_id=session_id,
            allowed_urls=extra.get("allowed_urls", []),
            headless=extra.get("headless", True),
            screenshots_dir=extra.get("screenshots_dir", "data/web_scraper_screenshots"),
        )
        tool_node = ToolNode(tools)
        return tool_node.invoke(state)

    g = StateGraph(WebScraperState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_wrapper)

    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, {"tools": "tools", "__end__": END})
    g.add_edge("tools", "agent")

    return g.compile(checkpointer=MemorySaver())
