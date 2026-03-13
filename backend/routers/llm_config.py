"""
LLM configuration endpoints — read and write the HTTP LLM settings.
"""
from fastapi import APIRouter, HTTPException
from backend.models.llm_config import LLMConfig
from backend.database import db, COLL_LLM_CONFIG
import httpx

router = APIRouter(prefix="/api/llm-config", tags=["LLM Config"])


@router.get("", response_model=LLMConfig)
def get_llm_config():
    raw = db.get(COLL_LLM_CONFIG, "default")
    if raw:
        return LLMConfig(**raw)
    return LLMConfig()


@router.put("", response_model=LLMConfig)
def update_llm_config(config: LLMConfig):
    db.set(COLL_LLM_CONFIG, "default", config.model_dump())
    return config


@router.post("/test")
async def test_llm_connection(config: LLMConfig):
    """Probe the LLM endpoint to verify connectivity."""
    base = config.base_url.rstrip("/")

    # Normalisation automatique pour Ollama : ajouter /v1 si absent
    if not base.endswith("/v1") and (
        ":11434" in base or config.provider.value == "ollama"
    ):
        base = base + "/v1"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {config.api_key}"},
                json={
                    "model": config.model,
                    "messages": [{"role": "user", "content": "Hello"}],
                    "max_tokens": 5,
                },
            )
            if resp.status_code == 200:
                return {"success": True, "status_code": resp.status_code, "endpoint": base}
            return {"success": False, "status_code": resp.status_code, "detail": resp.text[:500]}
    except httpx.ConnectError as e:
        return {"success": False, "error": f"Connexion refusée — vérifiez qu'Ollama/LM Studio est démarré. ({e})"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/models")
async def list_available_models(base_url: str, api_key: str = "ollama"):
    """Fetch available models from the configured endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                models = [m["id"] for m in data.get("data", [])]
                return {"success": True, "models": models}
            return {"success": False, "status_code": resp.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)}
