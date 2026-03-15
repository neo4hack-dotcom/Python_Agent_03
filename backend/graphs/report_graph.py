"""
Graphe LangGraph Rédacteur de Rapports (report_graph.py)
=========================================================
Génère un rapport d'analyse professionnel en PDF à partir :
  - d'une demande utilisateur
  - de l'historique d'une session de conversation

Architecture :
  START → report_writer_node → pdf_node → END

Nœuds :
  report_writer_node :
    Le LLM reçoit l'historique de la session et la demande.
    Il produit un rapport Markdown structuré :
      - Titre et résumé exécutif
      - Contexte & objectifs
      - Méthodologie
      - Analyse des données (avec tableaux SQL si présents)
      - Résultats clés & métriques
      - Recommandations actionnables
      - Conclusion

  pdf_node :
    Appelle pdf_generator.generate_pdf() qui utilise weasyprint
    pour convertir le Markdown en PDF professionnel (page de couverture,
    headers, tables stylisées, blocs SQL, footer numéroté).
    Stocke le PDF dans data/reports/, retourne l'URL de téléchargement.
"""
import logging
import re
import uuid
from typing import Any, Dict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from .state import ReportState
from .llm_factory import build_llm

logger = logging.getLogger(__name__)

# ── System prompt du rédacteur ─────────────────────────────────────────────────

REPORT_WRITER_SYSTEM = """Tu es un consultant senior spécialisé en rédaction de rapports d'analyse professionnels.

À partir de l'historique d'une conversation entre un utilisateur et des agents IA d'analyse de données,
tu dois produire un rapport d'analyse complet, structuré et prêt pour présentation à un comité de direction.

## Structure OBLIGATOIRE du rapport (respecter cet ordre) :

```
# [Titre du rapport — clair et descriptif]

## Résumé Exécutif
[2-3 paragraphes synthétisant les principaux résultats et recommandations]

---

## Contexte & Objectifs
[Description du problème analysé, périmètre, enjeux business]

---

## Méthodologie
[Approche d'analyse, sources de données utilisées, outils et requêtes SQL si pertinent]

---

## Analyse des Données

### [Axe d'analyse 1]
[Résultats avec tableaux de données Markdown si disponibles]

### [Axe d'analyse 2]
[...]

---

## Résultats Clés

- **Indicateur 1** : valeur / tendance
- **Indicateur 2** : valeur / tendance
- [...]

---

## Recommandations

1. **[Recommandation 1]** : description détaillée et plan d'action
2. **[Recommandation 2]** : [...]

---

## Conclusion
[Synthèse en 1-2 paragraphes, prochaines étapes proposées]

---

*Rapport généré par Python Agent Platform*
```

## Règles de rédaction :
- Langue : français professionnel
- Ton : analytique, factuel, orienté décision
- Inclure TOUS les tableaux de données mentionnés dans la conversation (en Markdown)
- Inclure les requêtes SQL importantes dans des blocs ```sql```
- Les métriques clés doivent être mises en gras
- Citer les données précises (pas de généralités)
- Longueur cible : 600-1200 mots (hors tableaux)
- PAS de balises HTML dans le markdown
"""


def _extract_title(markdown: str) -> str:
    """Extrait le titre H1 du markdown, ou retourne un titre générique."""
    match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return "Rapport d'Analyse"


def _extract_subtitle(user_request: str) -> str:
    """Génère un sous-titre à partir de la demande utilisateur."""
    cleaned = user_request.strip()
    if len(cleaned) > 80:
        cleaned = cleaned[:77] + "…"
    return cleaned


# ── Nœuds LangGraph ────────────────────────────────────────────────────────────

def report_writer_node(state: ReportState) -> Dict[str, Any]:
    """
    Nœud 1 — Rédaction du rapport Markdown par le LLM.

    Reçoit :
      - state["user_request"] : la demande de l'utilisateur
      - state["session_context"] : l'historique de la session en texte

    Retourne :
      - report_markdown : le rapport complet en Markdown structuré
      - messages : [AIMessage] avec le rapport
    """
    llm = build_llm()
    context = state.get("session_context") or ""
    user_request = state["user_request"]

    human_content = f"""Demande : {user_request}

---

## Historique de la session d'analyse

{context if context else "Aucun historique disponible — génère un rapport sur la base de la demande."}

---

Génère maintenant le rapport d'analyse complet en Markdown, en suivant strictement la structure demandée.
"""

    response = llm.invoke([
        SystemMessage(content=REPORT_WRITER_SYSTEM),
        HumanMessage(content=human_content),
    ])

    report_md = response.content.strip()
    return {
        "report_markdown": report_md,
        "messages": [AIMessage(content="✍️ Rapport rédigé, génération du PDF en cours…")],
    }


def pdf_node(state: ReportState) -> Dict[str, Any]:
    """
    Nœud 2 — Conversion du Markdown en PDF professionnel.

    Appelle pdf_generator.generate_pdf() avec :
      - Le contenu Markdown généré par report_writer_node
      - Le titre extrait du markdown
      - Le nom de l'agent, l'ID de session pour la page de couverture

    Stocke le PDF dans data/reports/rapport_{uuid}.pdf.
    Retourne l'ID du rapport et l'URL de téléchargement.
    """
    from backend.tools.pdf_generator import generate_pdf
    from backend.database import db, COLL_AGENTS

    report_md = state.get("report_markdown", "")
    if not report_md:
        return {
            "final_answer": "❌ Impossible de générer le PDF : aucun contenu de rapport.",
            "messages": [AIMessage(content="Erreur: rapport vide.")],
        }

    # Métadonnées pour la page de couverture
    title = _extract_title(report_md)
    subtitle = _extract_subtitle(state["user_request"])
    agent_cfg = db.get(COLL_AGENTS, state["agent_id"]) or {}
    agent_name = agent_cfg.get("name", "Agent Analyste")
    report_id = str(uuid.uuid4())

    try:
        pdf_path = generate_pdf(
            markdown_content=report_md,
            title=title,
            subtitle=subtitle,
            agent_name=agent_name,
            session_id=state["session_id"],
            output_filename=f"rapport_{report_id}.pdf",
        )
        download_url = f"/api/report/{report_id}/download"
        final_msg = (
            f"✅ **Rapport PDF généré avec succès !**\n\n"
            f"📄 **{title}**\n\n"
            f"Cliquez sur le lien ci-dessous pour télécharger le rapport :\n"
            f"[⬇️ Télécharger le rapport PDF]({download_url})\n\n"
            f"_Rapport de {len(report_md.split())} mots généré le "
            f"{__import__('datetime').datetime.now().strftime('%d/%m/%Y à %H:%M')}_"
        )
        return {
            "pdf_path": pdf_path,
            "report_id": report_id,
            "final_answer": final_msg,
            "messages": [AIMessage(content=final_msg)],
        }
    except Exception as e:
        logger.error("PDF generation error: %s", e)
        err_msg = f"❌ Erreur lors de la génération du PDF : {e}"
        return {
            "final_answer": err_msg,
            "messages": [AIMessage(content=err_msg)],
        }


# ── Construction du graphe ──────────────────────────────────────────────────────

def build_report_graph():
    """
    Assemble et compile le graphe LangGraph rédacteur de rapports.

    Flow : START → report_writer → pdf → END

    Returns:
        Graphe LangGraph compilé avec MemorySaver.
    """
    builder = StateGraph(ReportState)
    builder.add_node("report_writer", report_writer_node)
    builder.add_node("pdf", pdf_node)
    builder.add_edge(START, "report_writer")
    builder.add_edge("report_writer", "pdf")
    builder.add_edge("pdf", END)
    return builder.compile(checkpointer=MemorySaver())
