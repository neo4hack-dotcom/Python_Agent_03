"""
Fabrique LLM (llm_factory.py)
==============================
Construit un client LangChain `ChatOpenAI` à partir de la configuration LLM
persistée en base de données locale (json_db.py).

Pourquoi ChatOpenAI pour des modèles locaux ?
---------------------------------------------
Ollama, LM Studio, LiteLLM et la majorité des serveurs LLM locaux exposent
une API compatible OpenAI (même format JSON, mêmes routes /v1/chat/completions).
`ChatOpenAI` accepte un `base_url` personnalisé, ce qui permet de pointer vers
n'importe quel de ces serveurs sans changer le code des agents.

Flux de configuration :
  UI → POST /api/llm-config → json_db (collection "llm_config", id="default")
     → get_llm_config() charge la config au moment de l'invocation du graphe
     → build_llm() instancie le client avec ces paramètres

Ce chargement « à la demande » (lazy) garantit que les agents utilisent toujours
la config la plus récente sans avoir besoin de redémarrer le serveur.
"""
import logging
from typing import Optional
import httpx
from langchain_openai import ChatOpenAI
from backend.database import db, COLL_LLM_CONFIG
from backend.models.llm_config import LLMConfig

logger = logging.getLogger(__name__)

# ── Config par défaut ────────────────────────────────────────────────────────
# Instance LLMConfig avec les valeurs par défaut (voir models/llm_config.py) :
# base_url="http://localhost:11434/v1", model="llama3", temperature=0.1, etc.
# Utilisée uniquement si aucune config n'est enregistrée en base.
_DEFAULT_CONFIG = LLMConfig()


def get_llm_config() -> LLMConfig:
    """
    Charge la configuration LLM active depuis la base de données.

    Recherche le document d'id "default" dans la collection COLL_LLM_CONFIG.
    En cas d'absence ou de données corrompues, retourne la config par défaut.

    Returns:
        LLMConfig : modèle Pydantic avec tous les paramètres du LLM.
    """
    raw = db.get(COLL_LLM_CONFIG, "default")
    if raw:
        try:
            # Valider et parser le dict brut via Pydantic
            return LLMConfig(**raw)
        except Exception as e:
            # Config invalide (champ manquant, mauvais type…) → fallback
            logger.warning("Invalid LLM config in DB: %s — using defaults.", e)
    return _DEFAULT_CONFIG


def build_llm(streaming: bool = False, temperature: Optional[float] = None) -> ChatOpenAI:
    """
    Construit et retourne un client LangChain pointant vers le LLM configuré.

    Ce client est utilisé par TOUS les nœuds des graphes LangGraph
    (planner, worker, analyst, synthesizer, corrector…). Un appel à build_llm()
    recharge toujours la config depuis la DB, ce qui permet de changer de modèle
    sans redémarrer le serveur.

    Args:
        streaming (bool):
            Si True ET si la config autorise le streaming (cfg.streaming=True),
            active le streaming token-par-token. Utilisé par l'endpoint SSE
            /api/agents/{id}/stream. Désactivé pour les appels internes entre
            nœuds LangGraph (car les résultats intermédiaires ne sont pas streamés).

        temperature (Optional[float]):
            Surcharge la température de la config. Utile pour forcer
            temperature=0 sur des nœuds de planification (déterminisme maximal)
            ou temperature=0.7 sur des nœuds de synthèse (plus de créativité).
            Si None, utilise cfg.temperature.

    Returns:
        ChatOpenAI : client LangChain prêt à invoquer via .invoke() ou .stream().

    Example:
        # Appel standard dans un nœud LangGraph
        llm = build_llm()
        response = llm.invoke([SystemMessage(content="..."), HumanMessage(content="...")])

        # Appel en streaming pour l'endpoint SSE
        llm = build_llm(streaming=True)
        for chunk in llm.stream(messages):
            yield chunk.content
    """
    cfg = get_llm_config()

    # Build a custom httpx client so we can control SSL verification.
    # verify=False is required for self-signed certificates (LM Studio HTTPS,
    # corporate proxies, local PKI not trusted by the system store, etc.)
    http_client = httpx.Client(verify=cfg.verify_ssl)
    http_async_client = httpx.AsyncClient(verify=cfg.verify_ssl)

    return ChatOpenAI(
        model=cfg.model,                                          # ex: "llama3", "mistral", "gpt-4o"
        base_url=cfg.base_url,                                    # ex: "http://localhost:11434/v1"
        api_key=cfg.api_key,                                      # "ollama" pour Ollama (valeur factice)
        temperature=temperature if temperature is not None else cfg.temperature,
        max_tokens=cfg.max_tokens,                                # limite la longueur des réponses
        timeout=cfg.timeout,                                      # timeout HTTP en secondes
        streaming=streaming and cfg.streaming,                    # AND logique : les deux doivent être True
        http_client=http_client,
        http_async_client=http_async_client,
    )


def sanitize_messages(messages: list) -> list:
    """
    Sanitize a list of LangChain messages before sending to an LLM.

    Local LLMs (Ollama, LM Studio) and many OpenAI-compatible APIs reject
    messages where content is None (422 error). This happens when:
      - An AIMessage has tool_calls but no text (content=None)
      - A ToolMessage result is None
      - Any other message with missing content

    Uses object.__setattr__ to bypass Pydantic's validation/frozen constraints —
    the most reliable approach regardless of Pydantic v1/v2 and LangChain version.
    """
    for msg in messages:
        if hasattr(msg, "content") and msg.content is None:
            try:
                object.__setattr__(msg, "content", "")
            except Exception:
                pass
    return messages


def sanitize_response(response) -> None:
    """
    Sanitize an LLM response in-place so it is safe to store in LangGraph state.
    Call this immediately after llm.invoke() to ensure content is never None
    before the message is added to state.messages.
    """
    if hasattr(response, "content") and response.content is None:
        try:
            object.__setattr__(response, "content", "")
        except Exception:
            pass
