"""
Router CRUD pour les Toolkits.

Endpoints :
  GET    /api/toolkits              — liste tous les toolkits (seed defaults si vide)
  POST   /api/toolkits              — créer un toolkit
  POST   /api/toolkits/generate     — générer un toolkit via LLM (description naturelle)
  GET    /api/toolkits/templates    — retourner les définitions par défaut par db_type
  GET    /api/toolkits/{id}         — obtenir un toolkit spécifique
  PUT    /api/toolkits/{id}         — mettre à jour un toolkit
  DELETE /api/toolkits/{id}         — supprimer un toolkit (sauf defaults)
"""
import json
import re
import uuid
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.database import db, COLL_TOOLKITS, COLL_CONNECTIONS
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

# ── Génération par LLM ────────────────────────────────────────────────────────

class ToolkitGenerateRequest(BaseModel):
    description: str
    db_type: str = "clickhouse"
    connection_id: Optional[str] = None  # si fourni, le schéma réel est injecté


def _extract_json(text: str) -> dict:
    """Extrait le premier bloc JSON valide d'une réponse LLM."""
    # Cherche un bloc ```json ... ``` ou { ... }
    for pattern in (r"```json\s*([\s\S]+?)```", r"```\s*([\s\S]+?)```", r"(\{[\s\S]+\})"):
        m = re.search(pattern, text)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    raise ValueError("No valid JSON found in LLM response")


@router.post("/generate")
def generate_toolkit(payload: ToolkitGenerateRequest):
    """
    Génère une configuration de toolkit via le LLM local.

    Le LLM reçoit :
    - La description du cas d'usage en langage naturel
    - Les noms et descriptions par défaut des 4 outils disponibles
    - (Optionnel) La liste des tables réelles si connection_id est fourni

    Retourne un JSON prêt à être affiché dans le formulaire ToolkitModal.
    """
    from backend.graphs.llm_factory import build_llm, sanitize_messages, sanitize_response
    from backend.tools.langchain_clickhouse_tools import CLICKHOUSE_TOOL_DEFINITIONS
    from backend.tools.langchain_oracle_tools import ORACLE_TOOL_DEFINITIONS
    from langchain_core.messages import HumanMessage, SystemMessage

    # Outil definitions selon db_type
    tool_defs = ORACLE_TOOL_DEFINITIONS if payload.db_type == "oracle" else CLICKHOUSE_TOOL_DEFINITIONS
    tools_block = "\n".join(
        f"- **{t['name']}** : {t['description']}" for t in tool_defs
    )

    # Schéma réel optionnel
    schema_block = ""
    if payload.connection_id:
        try:
            conn_cfg = db.get(COLL_CONNECTIONS, payload.connection_id)
            if conn_cfg:
                conn_type = conn_cfg.get("type", "clickhouse")
                if conn_type == "clickhouse":
                    from backend.tools.sql_clickhouse import ClickHouseSQLTool
                    tool = ClickHouseSQLTool(conn_cfg, row_limit=10)
                    tables = tool.list_tables()
                    valid_tables = [t for t in tables if not t.startswith("ERROR:")]
                    if valid_tables:
                        schema_block = (
                            f"\n\n## Schéma réel de la base ({conn_cfg.get('database', '')})\n"
                            f"Tables disponibles : {', '.join(valid_tables[:30])}"
                        )
        except Exception as e:
            logger.warning("Could not fetch schema for generate: %s", e)

    system_prompt = f"""Tu es un expert en configuration d'agents SQL LangGraph.
Tu dois générer une configuration de toolkit JSON pour personnaliser les outils d'un agent.

## Outils disponibles pour {payload.db_type.upper()} :
{tools_block}
{schema_block}

## Instructions :
1. Génère un nom court et descriptif pour le toolkit
2. Écris une description courte (1-2 phrases) du toolkit
3. Pour CHAQUE outil, écris une description PERSONNALISÉE et PRÉCISE adaptée au cas d'usage décrit
   - Inclus les noms de tables pertinents si connus
   - Inclus les colonnes clés à utiliser
   - Inclus les règles métier spécifiques
   - La description doit guider le LLM vers les bonnes pratiques pour CE cas d'usage
4. Tu peux désactiver un outil (enabled: false) si il n'est pas utile pour ce cas d'usage

## Format de réponse (JSON uniquement, aucun texte avant ou après) :
```json
{{
  "name": "Nom du toolkit",
  "description": "Description courte du toolkit",
  "db_type": "{payload.db_type}",
  "tools": [
    {{
      "name": "list_tables",
      "description": "Description personnalisée pour ce cas d'usage...",
      "enabled": true
    }},
    {{
      "name": "get_schema",
      "description": "Description personnalisée...",
      "enabled": true
    }},
    {{
      "name": "execute_query",
      "description": "Description personnalisée avec règles SQL spécifiques...",
      "enabled": true
    }},
    {{
      "name": "check_query",
      "description": "Description personnalisée...",
      "enabled": true
    }}
  ]
}}
```"""

    try:
        llm = build_llm(temperature=0.3)
        response = sanitize_response(llm.invoke(sanitize_messages([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Cas d'usage : {payload.description}"),
        ])))
        generated = _extract_json(response.content or "")

        # Validation / normalisation
        valid_names = {t["name"] for t in tool_defs}
        tools_out = []
        for t in generated.get("tools", []):
            if t.get("name") in valid_names:
                tools_out.append({
                    "name": t["name"],
                    "description": t.get("description") or None,
                    "enabled": bool(t.get("enabled", True)),
                })
        # S'assurer que tous les outils sont présents
        present = {t["name"] for t in tools_out}
        for t in tool_defs:
            if t["name"] not in present:
                tools_out.append({"name": t["name"], "description": None, "enabled": True})

        return {
            "name": generated.get("name", "Toolkit généré"),
            "description": generated.get("description", ""),
            "db_type": payload.db_type,
            "tools": tools_out,
        }

    except Exception as e:
        logger.error("Toolkit generation error: %s", e)
        raise HTTPException(500, f"LLM generation failed: {e}")


# ── Endpoints CRUD ─────────────────────────────────────────────────────────────

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
