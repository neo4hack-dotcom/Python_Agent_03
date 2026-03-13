"""
Build a LangChain LLM client from the persisted LLM configuration.
Supports any OpenAI-compatible HTTP endpoint (Ollama, LM Studio, etc.).
"""
import logging
from typing import Optional
from langchain_openai import ChatOpenAI
from backend.database import db, COLL_LLM_CONFIG
from backend.models.llm_config import LLMConfig

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = LLMConfig()


def get_llm_config() -> LLMConfig:
    raw = db.get(COLL_LLM_CONFIG, "default")
    if raw:
        try:
            return LLMConfig(**raw)
        except Exception as e:
            logger.warning("Invalid LLM config in DB: %s — using defaults.", e)
    return _DEFAULT_CONFIG


def build_llm(streaming: bool = False, temperature: Optional[float] = None) -> ChatOpenAI:
    """
    Build a ChatOpenAI client pointing to the configured local LLM endpoint.
    """
    cfg = get_llm_config()
    return ChatOpenAI(
        model=cfg.model,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        temperature=temperature if temperature is not None else cfg.temperature,
        max_tokens=cfg.max_tokens,
        timeout=cfg.timeout,
        streaming=streaming and cfg.streaming,
    )
