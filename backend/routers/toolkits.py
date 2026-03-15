"""
Router CRUD pour les Toolkits.

Endpoints :
  GET    /api/toolkits              — liste tous les toolkits (seed defaults si vide)
  POST   /api/toolkits              — créer un toolkit
  GET    /api/toolkits/templates    — retourner les définitions par défaut par db_type
  GET    /api/toolkits/{id}         — obtenir un toolkit spécifique
  PUT    /api/toolkits/{id}         — mettre à jour un toolkit
  DELETE /api/toolkits/{id}         — supprimer un toolkit (sauf defaults)
"""
import uuid
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from backend.database import db, COLL_TOOLKITS
from backend.models.toolkit import Toolkit, ToolkitCreate, ToolkitUpdate, ToolDefinition

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/toolkits", tags=["Toolkits"])

# ── Définitions par défaut des outils ─────────────────────────────────────────

_CLICKHOUSE_DEFAULT_TOOLS = [
    ToolDefinition(name="list_tables", description=None, enabled=True),
    ToolDefinition(name="get_schema", description=None, enabled=True),
    ToolDefinition(name="execute_query", description=None, enabled=True),
    ToolDefinition(name="check_query", description=None, enabled=True),
]

_ORACLE_DEFAULT_TOOLS = [
    ToolDefinition(name="list_tables", description=None, enabled=True),
    ToolDefinition(name="get_schema", description=None, enabled=True),
    ToolDefinition(name="execute_query", description=None, enabled=True),
    ToolDefinition(name="check_query", description=None, enabled=True),
]

# Toolkits système (seeded au premier appel à GET /toolkits)
_DEFAULT_TOOLKITS = [
    Toolkit(
        id="default-clickhouse",
        name="ClickHouse Standard",
        description="Tous les outils ClickHouse activés : exploration, schéma, requêtes et validation.",
        db_type="clickhouse",
        tools=_CLICKHOUSE_DEFAULT_TOOLS,
        is_default=True,
    ),
    Toolkit(
        id="default-oracle",
        name="Oracle Standard",
        description="Tous les outils Oracle activés : exploration, schéma, requêtes et validation.",
        db_type="oracle",
        tools=_ORACLE_DEFAULT_TOOLS,
        is_default=True,
    ),
    Toolkit(
        id="default-clickhouse-readonly",
        name="ClickHouse Read-Only (sans check)",
        description="Outils ClickHouse sans la validation EXPLAIN — pour les bases où EXPLAIN est restreint.",
        db_type="clickhouse",
        tools=[
            ToolDefinition(name="list_tables", enabled=True),
            ToolDefinition(name="get_schema", enabled=True),
            ToolDefinition(name="execute_query", enabled=True),
            ToolDefinition(name="check_query", enabled=False),
        ],
        is_default=True,
    ),
]


def _seed_defaults():
    """Insère les toolkits par défaut s'ils n'existent pas encore."""
    for toolkit in _DEFAULT_TOOLKITS:
        if not db.exists(COLL_TOOLKITS, toolkit.id):
            db.set(COLL_TOOLKITS, toolkit.id, toolkit.model_dump())


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", response_model=List[Toolkit])
def list_toolkits():
    """Retourne tous les toolkits (seed les defaults si la collection est vide)."""
    _seed_defaults()
    return db.get_all(COLL_TOOLKITS)


@router.get("/templates")
def get_templates():
    """
    Retourne les définitions d'outils par défaut pour chaque type de DB.
    Utile pour pré-remplir le formulaire de création d'un toolkit.
    """
    from backend.tools.langchain_clickhouse_tools import CLICKHOUSE_TOOL_DEFINITIONS
    from backend.tools.langchain_oracle_tools import ORACLE_TOOL_DEFINITIONS

    return {
        "clickhouse": CLICKHOUSE_TOOL_DEFINITIONS,
        "oracle": ORACLE_TOOL_DEFINITIONS,
    }


@router.post("", response_model=Toolkit)
def create_toolkit(payload: ToolkitCreate):
    """Crée un nouveau toolkit personnalisé."""
    _seed_defaults()

    # Si aucun outil n'est fourni, utiliser les defaults du db_type
    tools = payload.tools
    if not tools:
        if payload.db_type == "oracle":
            tools = _ORACLE_DEFAULT_TOOLS
        else:
            tools = _CLICKHOUSE_DEFAULT_TOOLS

    toolkit = Toolkit(
        id=str(uuid.uuid4()),
        name=payload.name,
        description=payload.description,
        db_type=payload.db_type,
        tools=tools,
        is_default=False,
        created_at=datetime.utcnow().isoformat(),
        updated_at=datetime.utcnow().isoformat(),
    )
    db.set(COLL_TOOLKITS, toolkit.id, toolkit.model_dump())
    return toolkit


@router.get("/{toolkit_id}", response_model=Toolkit)
def get_toolkit(toolkit_id: str):
    """Retourne un toolkit par son ID."""
    _seed_defaults()
    toolkit = db.get(COLL_TOOLKITS, toolkit_id)
    if not toolkit:
        raise HTTPException(404, f"Toolkit '{toolkit_id}' not found")
    return toolkit


@router.put("/{toolkit_id}", response_model=Toolkit)
def update_toolkit(toolkit_id: str, payload: ToolkitUpdate):
    """Met à jour un toolkit existant. Les toolkits système (is_default=True) peuvent être modifiés."""
    _seed_defaults()
    existing = db.get(COLL_TOOLKITS, toolkit_id)
    if not existing:
        raise HTTPException(404, f"Toolkit '{toolkit_id}' not found")

    updates: dict = {"updated_at": datetime.utcnow().isoformat()}
    if payload.name is not None:
        updates["name"] = payload.name
    if payload.description is not None:
        updates["description"] = payload.description
    if payload.tools is not None:
        updates["tools"] = [t.model_dump() for t in payload.tools]

    updated = db.upsert(COLL_TOOLKITS, toolkit_id, updates)
    return updated


@router.delete("/{toolkit_id}")
def delete_toolkit(toolkit_id: str):
    """Supprime un toolkit. Les toolkits système ne peuvent pas être supprimés."""
    _seed_defaults()
    existing = db.get(COLL_TOOLKITS, toolkit_id)
    if not existing:
        raise HTTPException(404, f"Toolkit '{toolkit_id}' not found")
    if existing.get("is_default"):
        raise HTTPException(403, "Cannot delete a default system toolkit")
    db.delete(COLL_TOOLKITS, toolkit_id)
    return {"message": f"Toolkit '{existing.get('name')}' deleted"}
