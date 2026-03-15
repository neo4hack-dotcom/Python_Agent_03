"""
Modèles Pydantic pour le système de Toolkits.

Un Toolkit est un ensemble d'outils (tools) assignable à un agent.
Il permet de :
  - Activer / désactiver des outils spécifiques (ex: désactiver check_query)
  - Personnaliser les descriptions affichées au LLM pour chaque outil
  - Créer des templates réutilisables entre plusieurs agents

Structure :
  Toolkit
    └─ tools: List[ToolDefinition]
         ├─ name: str        (ex: "execute_query")
         ├─ description: str (override de la description par défaut)
         └─ enabled: bool    (si False, l'outil n'est pas lié au LLM)
"""
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import uuid


class ToolDefinition(BaseModel):
    """Définition d'un outil dans un toolkit."""

    name: str = Field(..., description="Nom de l'outil (ex: 'execute_query')")
    description: Optional[str] = Field(
        None,
        description="Description personnalisée affichée au LLM. None = description par défaut.",
    )
    enabled: bool = Field(
        True,
        description="Si False, l'outil n'est pas inclus dans bind_tools().",
    )


class ToolkitCreate(BaseModel):
    """Payload de création d'un toolkit."""

    name: str = Field(..., min_length=1, max_length=64)
    description: Optional[str] = Field(None, max_length=256)
    db_type: str = Field(
        "clickhouse",
        description="Type de DB cible : 'clickhouse', 'oracle', ou 'any'.",
    )
    tools: List[ToolDefinition] = Field(
        default_factory=list,
        description="Définitions des outils. Vide = tous les outils du db_type activés.",
    )


class ToolkitUpdate(BaseModel):
    """Payload de mise à jour d'un toolkit."""

    name: Optional[str] = Field(None, min_length=1, max_length=64)
    description: Optional[str] = None
    tools: Optional[List[ToolDefinition]] = None


class Toolkit(BaseModel):
    """Modèle complet d'un toolkit (stocké en base)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    db_type: str = "clickhouse"
    tools: List[ToolDefinition] = Field(default_factory=list)
    is_default: bool = Field(
        False,
        description="True pour les toolkits système fournis par défaut (non supprimables).",
    )
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
