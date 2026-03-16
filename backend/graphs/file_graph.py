"""
Graphe LangGraph — Agent File Manager (file_graph.py)
======================================================
Agent ReAct capable de naviguer dans des répertoires et de gérer des fichiers.

Opérations disponibles :
  - Lecture : list_directory, get_file_info, search_files, read_file, read_csv_summary
  - Création : create_file, create_directory
  - Modification/déplacement : write_file, move_file  (confirmation obligatoire)
  - Suppression : delete_file, delete_directory        (confirmation obligatoire)

Formats supportés : .txt, .md, .py, .sql, .json, .yaml, .csv, .tsv,
                    .xlsx, .xls, .docx, .parquet, et tous formats texte.

Sécurité :
  - Les opérations destructives (write, delete, move) retournent d'abord une description
    + demande de confirmation. L'agent doit attendre "oui" / "confirme" de l'utilisateur
    avant de rappeler l'outil avec confirmed=True.
  - Un base_path optionnel peut restreindre toutes les opérations à un répertoire racine.

Architecture :
  START → agent_node → [route] → tools_node → agent_node (boucle ReAct)
                                            → END (réponse finale ou max iterations)
"""
import logging
from typing import Any, Dict, Literal, Optional, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

from .state import FileAgentState
from .llm_factory import build_llm, sanitize_messages, sanitize_response
from backend.tools.file_tools import make_file_tools
from backend.database import db, COLL_AGENTS

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 15  # garde-fou anti-boucle infinie

FILE_MANAGER_SYSTEM = """Tu es un agent expert en gestion de fichiers et répertoires.
Tu peux naviguer dans des dossiers, lire, créer et modifier des fichiers de nombreux formats.

## Formats supportés
- Texte : .txt, .md, .py, .js, .sql, .json, .yaml, .html, .csv, .log…
- Tableurs : .xlsx (Excel), .csv, .tsv
- Documents : .docx (Word)
- Données : .parquet

## Workflow recommandé
1. Utilise `list_directory` pour explorer la structure de répertoires
2. Utilise `get_file_info` pour obtenir les métadonnées d'un fichier
3. Utilise `search_files` pour trouver des fichiers par pattern glob
4. Utilise `read_file` pour lire le contenu (ou `read_csv_summary` pour les CSV)
5. Utilise `create_file` pour créer un NOUVEAU fichier
6. Utilise `write_file`, `delete_file`, `delete_directory`, `move_file` pour les modifications

## Règles de sécurité OBLIGATOIRES
- Pour toute opération destructive ou de modification (write_file, delete_file, delete_directory, move_file) :
  1. Appelle TOUJOURS d'abord l'outil avec confirmed=False pour afficher l'aperçu
  2. Demande explicitement la confirmation à l'utilisateur dans ta réponse
  3. N'exécute l'opération (confirmed=True) QUE si l'utilisateur répond avec un message contenant
     "oui", "confirme", "ok", "go", "yes", "confirm", "valide" ou similaire
  4. Si l'utilisateur refuse ou hésite, n'exécute PAS l'opération

## Style de réponse
- Réponds en français sauf si l'utilisateur parle anglais
- Pour les lectures : affiche clairement le contenu trouvé
- Pour les modifications : confirme l'action accomplie avec les détails (chemin, taille)
- Si un fichier est introuvable, propose des alternatives (search_files avec un pattern similaire)

{custom_prompt}"""


def _build_tools(agent_id: str):
    """Construit les outils file manager pour l'agent."""
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
    base_path = agent_cfg.get("extra_config", {}).get("base_path") or None
    return make_file_tools(base_path=base_path)


def agent_node(state: FileAgentState) -> Dict[str, Any]:
    """Nœud ReAct — le LLM décide quelle action effectuer."""
    llm = build_llm()
    agent_id = state.get("agent_id", "")
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
    custom_prompt = agent_cfg.get("system_prompt", "")

    tools = _build_tools(agent_id)
    llm_with_tools = llm.bind_tools(tools)

    system_content = FILE_MANAGER_SYSTEM.format(custom_prompt=custom_prompt or "")
    messages = sanitize_messages([SystemMessage(content=system_content)] + list(state.get("messages", [])))

    iteration = state.get("iteration_count", 0)
    if iteration >= _MAX_ITERATIONS:
        # Force la sortie de la boucle ReAct
        response = AIMessage(
            content=(
                "⚠️ Limite d'itérations atteinte. Voici un résumé de ce qui a été accompli "
                "jusqu'ici :\n\n" + _summarize_progress(state)
            )
        )
        return {
            "messages": [response],
            "final_answer": response.content,
            "iteration_count": iteration + 1,
        }

    response = sanitize_response(llm_with_tools.invoke(messages))
    updates: Dict[str, Any] = {
        "messages": [response],
        "iteration_count": iteration + 1,
    }

    # Si pas d'appel d'outil → réponse finale
    if not getattr(response, "tool_calls", None):
        updates["final_answer"] = response.content or ""

    return updates


def _summarize_progress(state: FileAgentState) -> str:
    """Résume les actions déjà effectuées pour le message d'arrêt forcé."""
    msgs = state.get("messages", [])
    summaries = []
    for m in msgs:
        if hasattr(m, "tool_calls") and m.tool_calls:
            for tc in m.tool_calls:
                summaries.append(f"- `{tc['name']}` appelé")
    return "\n".join(summaries) if summaries else "Aucune action enregistrée."


def tools_node(state: FileAgentState) -> Dict[str, Any]:
    """Nœud d'exécution des outils."""
    agent_id = state.get("agent_id", "")
    tools = _build_tools(agent_id)
    tool_executor = ToolNode(tools)
    result = tool_executor.invoke(state)
    return result


def route_after_agent(state: FileAgentState) -> Literal["tools", "__end__"]:
    """Route vers tools si le LLM a émis des tool_calls, sinon END."""
    messages = state.get("messages", [])
    if not messages:
        return "__end__"
    last = messages[-1]
    if getattr(last, "tool_calls", None) and state.get("iteration_count", 0) < _MAX_ITERATIONS:
        return "tools"
    return "__end__"


def build_file_graph():
    """Compile le graphe LangGraph pour l'agent File Manager."""
    builder = StateGraph(FileAgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tools_node)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "__end__": END})
    builder.add_edge("tools", "agent")

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)
