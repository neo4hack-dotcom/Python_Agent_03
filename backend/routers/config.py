"""
Configuration export / import — sauvegarde et restauration globale des configs.

Export : GET /api/config/export
  → télécharge un fichier JSON contenant LLM config, connexions DB et agents.
  → les mots de passe des connexions sont inclus (usage local uniquement).

Import : POST /api/config/import
  → accepte le JSON exporté et restaure les collections.
  → mode "merge" (défaut) : fusionne avec l'existant (upsert par ID).
  → mode "replace" : vide les collections avant import.
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.database import db, COLL_AGENTS, COLL_CONNECTIONS, COLL_LLM_CONFIG

router = APIRouter(prefix="/api/config", tags=["Config"])

EXPORT_VERSION = "1.0"


# ── Schémas ───────────────────────────────────────────────────────────────────

class ConfigBundle(BaseModel):
    version: str = EXPORT_VERSION
    exported_at: str
    llm_config: Optional[Dict[str, Any]] = None
    connections: List[Dict[str, Any]] = []
    agents: List[Dict[str, Any]] = []


class ImportResult(BaseModel):
    success: bool
    imported: Dict[str, int]
    skipped: Dict[str, int]
    errors: List[str] = []


# ── Export ────────────────────────────────────────────────────────────────────

@router.get("/export")
def export_config(
    include_passwords: bool = Query(
        default=True,
        description="Inclure les mots de passe des connexions dans l'export"
    )
):
    """Exporte LLM config, connexions et agents dans un bundle JSON."""
    llm_raw = db.get(COLL_LLM_CONFIG, "default")
    connections_raw = db.get_all(COLL_CONNECTIONS)
    agents_raw = db.get_all(COLL_AGENTS)

    # Masquer les mots de passe si demandé
    connections = []
    for c in connections_raw:
        c = dict(c)
        if not include_passwords:
            c["password"] = ""
        connections.append(c)

    bundle = {
        "version": EXPORT_VERSION,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "llm_config": llm_raw,
        "connections": connections,
        "agents": list(agents_raw),
    }

    filename = f"agent-platform-config-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"
    return JSONResponse(
        content=bundle,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "application/json",
        },
    )


# ── Import ────────────────────────────────────────────────────────────────────

@router.post("/import", response_model=ImportResult)
def import_config(
    bundle: ConfigBundle,
    mode: Literal["merge", "replace"] = Query(
        default="merge",
        description="merge = conserve l'existant, replace = réinitialise avant import"
    ),
):
    """Importe un bundle de configuration exporté."""
    imported: Dict[str, int] = {"llm_config": 0, "connections": 0, "agents": 0}
    skipped: Dict[str, int] = {"llm_config": 0, "connections": 0, "agents": 0}
    errors: List[str] = []

    # ── LLM Config ────────────────────────────────────────────────────────────
    if bundle.llm_config:
        try:
            if mode == "replace":
                db.clear_collection(COLL_LLM_CONFIG)
            db.set(COLL_LLM_CONFIG, "default", bundle.llm_config)
            imported["llm_config"] = 1
        except Exception as e:
            errors.append(f"LLM config: {e}")
            skipped["llm_config"] = 1

    # ── Connexions ────────────────────────────────────────────────────────────
    if mode == "replace" and bundle.connections:
        db.clear_collection(COLL_CONNECTIONS)

    for conn in bundle.connections:
        try:
            conn_id = conn.get("id")
            if not conn_id:
                errors.append(f"Connexion sans ID ignorée : {conn.get('name', '?')}")
                skipped["connections"] += 1
                continue
            db.set(COLL_CONNECTIONS, conn_id, conn)
            imported["connections"] += 1
        except Exception as e:
            errors.append(f"Connexion '{conn.get('name', '?')}': {e}")
            skipped["connections"] += 1

    # ── Agents ────────────────────────────────────────────────────────────────
    if mode == "replace" and bundle.agents:
        db.clear_collection(COLL_AGENTS)

    for agent in bundle.agents:
        try:
            agent_id = agent.get("id")
            if not agent_id:
                errors.append(f"Agent sans ID ignoré : {agent.get('name', '?')}")
                skipped["agents"] += 1
                continue
            db.set(COLL_AGENTS, agent_id, agent)
            imported["agents"] += 1
        except Exception as e:
            errors.append(f"Agent '{agent.get('name', '?')}': {e}")
            skipped["agents"] += 1

    return ImportResult(
        success=len(errors) == 0,
        imported=imported,
        skipped=skipped,
        errors=errors,
    )
