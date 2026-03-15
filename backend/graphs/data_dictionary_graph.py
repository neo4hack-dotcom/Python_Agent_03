"""
Data Dictionary Agent — documentation métier automatique de tables ClickHouse/Oracle.

Pipeline linéaire :
  discover_tables_node → fetch_schemas_node → llm_doc_node → synthesizer_node

Entrée : message JSON structuré depuis DataDictionaryForm :
  {
    "__dd__": true,
    "tables": ["db.orders", "db.customers"],  // [] = toutes les tables
    "sample_rows": 5,                          // 1-20
    "language": "fr"                           // "fr" | "en"
  }
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, TypedDict

from backend.database import db, COLL_AGENTS, COLL_CONNECTIONS
from backend.graphs.llm_factory import build_llm
from backend.tools.sql_clickhouse import ClickHouseSQLTool
from backend.tools.sql_oracle import OracleSQLTool

logger = logging.getLogger(__name__)

# ── JSON extraction ──────────────────────────────────────────────────────────

def _extract_json(text: str) -> Optional[dict]:
    """Extracts the first JSON object found in text (handles markdown code blocks)."""
    # Strip markdown code blocks
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```", "", cleaned)
    cleaned = cleaned.strip()
    # Find outermost JSON object
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and start < end:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


# ── Helper : get tool and connection type ────────────────────────────────────

def _get_tool(agent_id: str):
    """Return (tool, db_type_str) or raise."""
    agent_cfg = db.get(COLL_AGENTS, agent_id)
    if not agent_cfg:
        raise ValueError(f"Agent {agent_id} not found")
    conn_id = agent_cfg.get("connection_id")
    if not conn_id:
        raise ValueError("Agent has no connection_id configured")
    conn_cfg = db.get(COLL_CONNECTIONS, conn_id)
    if not conn_cfg:
        raise ValueError(f"Connection {conn_id} not found")
    db_type = conn_cfg.get("type", "clickhouse")
    row_limit = agent_cfg.get("row_limit", 1000)
    if db_type == "oracle":
        return OracleSQLTool(conn_cfg, row_limit=row_limit), "oracle"
    return ClickHouseSQLTool(conn_cfg, row_limit=row_limit), "clickhouse"


# ── LLM prompts ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT_FR = """Tu es un senior data engineer expert en documentation de bases de données.
Tu reçois le schéma et un échantillon de données d'une table et tu dois produire une documentation métier structurée.

Règles STRICTES :
- Réponds UNIQUEMENT avec le JSON demandé, sans markdown, sans explication, sans texte avant ou après.
- Toutes les descriptions doivent être en FRANÇAIS.
- table_description : 1-3 phrases décrivant le rôle métier de la table, son domaine, ses usages.
- business_description de chaque colonne : courte (1-2 phrases), en termes métier (pas techniques).
- format : pattern ou format de la valeur (ex: "ISO 8601", "code devise ISO 4217", "entier >= 0", "booléen 0/1").
- possible_values : tableau des valeurs possibles UNIQUEMENT si cardinalité faible (< 15 valeurs) ET déductible du nom ou des données. Sinon : tableau vide [].
- Si une colonne est ambiguë, fais une hypothèse raisonnée.

Structure JSON attendue (respecte EXACTEMENT ce schéma) :
{
  "table_description": "...",
  "columns": [
    {
      "name": "nom_colonne",
      "type": "type_technique",
      "business_description": "...",
      "format": "...",
      "possible_values": []
    }
  ]
}"""

SYSTEM_PROMPT_EN = """You are a senior data engineer expert in database documentation.
You receive the schema and a data sample of a table and must produce structured business documentation.

STRICT rules:
- Reply ONLY with the requested JSON, no markdown, no explanation, no text before or after.
- All descriptions must be in ENGLISH.
- table_description: 1-3 sentences describing the business role of the table, its domain, its use cases.
- business_description of each column: short (1-2 sentences) in business terms (not technical).
- format: value pattern or format (e.g., "ISO 8601", "ISO 4217 currency code", "integer >= 0", "boolean 0/1").
- possible_values: array of possible values ONLY if low cardinality (< 15 values) AND deductible from name or data. Otherwise: empty array [].
- If a column is ambiguous, make a reasoned assumption.

Expected JSON structure (respect EXACTLY this schema):
{
  "table_description": "...",
  "columns": [
    {
      "name": "column_name",
      "type": "technical_type",
      "business_description": "...",
      "format": "...",
      "possible_values": []
    }
  ]
}"""


def _build_user_prompt(table: str, schema_cols: List[Dict], sample: Optional[Dict], language: str) -> str:
    # Schema markdown table
    schema_lines = ["| name | type | comment |", "|------|------|---------|"]
    for col in schema_cols:
        name = col.get("name", "")
        ctype = col.get("type", "")
        comment = (col.get("comment") or "").strip()
        schema_lines.append(f"| {name} | {ctype} | {comment} |")
    schema_md = "\n".join(schema_lines)

    # Sample markdown table
    sample_md = ""
    if sample and sample.get("columns") and sample.get("rows"):
        cols = sample["columns"]
        rows = sample["rows"][:10]
        sample_lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for row in rows:
            sample_lines.append("| " + " | ".join(str(v) if v is not None else "" for v in row) + " |")
        sample_md = f"\n\nSample data ({len(rows)} rows):\n" + "\n".join(sample_lines)

    if language == "fr":
        return f"Table : **{table}**\n\nSchéma :\n{schema_md}{sample_md}"
    return f"Table: **{table}**\n\nSchema:\n{schema_md}{sample_md}"


# ── State ────────────────────────────────────────────────────────────────────

class DataDictionaryState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    # Input params
    tables: List[str]      # empty = auto-discover all tables
    sample_rows: int       # 1-20
    language: str          # "fr" or "en"
    db_type: str
    # Intermediate
    discovered_tables: List[str]
    table_schemas: Optional[Dict[str, Any]]   # {table: {columns: [...], sample: {...}}}
    # Output
    dictionary: Optional[List[Dict[str, Any]]]
    final_answer: Optional[str]
    # Infra
    agent_id: str
    session_id: str
    last_error: Optional[str]


# ── Graph nodes ──────────────────────────────────────────────────────────────

def discover_tables_node(state: DataDictionaryState) -> Dict:
    """Use provided tables list or auto-discover via SHOW TABLES."""
    try:
        tool, db_type = _get_tool(state["agent_id"])
        if state.get("tables"):
            return {
                "discovered_tables": state["tables"],
                "db_type": db_type,
                "messages": [AIMessage(content=f"[DD] {len(state['tables'])} table(s) fournies par l'utilisateur")],
            }
        # Auto-discover
        tables = tool.list_tables()
        if tables and tables[0].startswith("ERROR:"):
            return {
                "last_error": tables[0],
                "discovered_tables": [],
                "db_type": db_type,
            }
        # Filter out system tables if ClickHouse
        if db_type == "clickhouse":
            tables = [t for t in tables if not t.startswith("system.") and not t.startswith(".")]
        return {
            "discovered_tables": tables,
            "db_type": db_type,
            "messages": [AIMessage(content=f"[DD] {len(tables)} table(s) découverte(s) dans la base")],
        }
    except Exception as e:
        return {
            "last_error": str(e),
            "discovered_tables": [],
            "db_type": state.get("db_type", "clickhouse"),
            "messages": [AIMessage(content=f"[DD] Erreur découverte tables : {e}")],
        }


def fetch_schemas_node(state: DataDictionaryState) -> Dict:
    """Fetch DESCRIBE + sample for each discovered table."""
    try:
        tool, db_type = _get_tool(state["agent_id"])
    except Exception as e:
        return {"last_error": str(e), "table_schemas": {}}

    tables = state.get("discovered_tables") or []
    sample_rows = max(1, min(20, state.get("sample_rows", 5)))
    schemas: Dict[str, Any] = {}

    for i, table in enumerate(tables):
        # Split "database.table" for ClickHouse get_schema
        db_name = None
        tbl_name = table
        if "." in table:
            parts = table.split(".", 1)
            db_name, tbl_name = parts[0], parts[1]

        entry: Dict[str, Any] = {"columns": [], "sample": None, "error": None}

        # Get schema
        try:
            if db_type == "oracle":
                schema_result = tool.get_schema(tbl_name.upper())
            else:
                schema_result = tool.get_schema(tbl_name, database=db_name)
            if "error" in schema_result:
                entry["error"] = schema_result["error"]
            else:
                entry["columns"] = schema_result.get("columns", [])
        except Exception as e:
            entry["error"] = str(e)

        # Get sample (even if schema failed, try anyway)
        try:
            if db_type == "oracle":
                sample_sql = f"SELECT * FROM {table} WHERE ROWNUM <= {sample_rows}"
            else:
                sample_sql = f"SELECT * FROM {table} LIMIT {sample_rows}"
            # Temporarily bump row_limit to allow small samples
            old_limit = tool.row_limit
            tool.row_limit = max(tool.row_limit, 100)
            res = tool.execute(sample_sql)
            tool.row_limit = old_limit
            if res.get("success"):
                entry["sample"] = {
                    "columns": res.get("columns", []),
                    "rows": res.get("rows", []),
                }
        except Exception:
            pass  # sample failure is non-blocking

        schemas[table] = entry

    return {
        "table_schemas": schemas,
        "messages": [AIMessage(content=f"[DD] Schémas récupérés pour {len(schemas)} table(s)")],
    }


def llm_doc_node(state: DataDictionaryState) -> Dict:
    """Call LLM for each table to generate structured business documentation."""
    llm = build_llm(state["agent_id"])
    language = state.get("language", "fr")
    system_prompt = SYSTEM_PROMPT_FR if language == "fr" else SYSTEM_PROMPT_EN
    table_schemas = state.get("table_schemas") or {}
    tables = state.get("discovered_tables") or []

    dictionary: List[Dict[str, Any]] = []
    msgs_out = []

    for i, table in enumerate(tables):
        schema_entry = table_schemas.get(table, {})
        schema_cols = schema_entry.get("columns", [])
        sample = schema_entry.get("sample")
        schema_error = schema_entry.get("error")

        progress_msg = f"[DD] Documentation table {i + 1}/{len(tables)} : {table}…"
        msgs_out.append(AIMessage(content=progress_msg))

        if schema_error and not schema_cols:
            # Table couldn't be described — mark as error, continue
            entry = {
                "table": table,
                "table_description": f"⚠️ Erreur : impossible de récupérer le schéma ({schema_error})",
                "columns": [],
                "error": schema_error,
            }
            dictionary.append(entry)
            continue

        # Build LLM prompt
        user_msg = _build_user_prompt(table, schema_cols, sample, language)
        try:
            response = llm.invoke([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ])
            raw_text = response.content if hasattr(response, "content") else str(response)
            parsed = _extract_json(raw_text)
            if parsed:
                entry = {
                    "table": table,
                    "table_description": parsed.get("table_description", ""),
                    "columns": parsed.get("columns", []),
                }
                # Fill in missing columns (LLM might skip some) — use schema as fallback
                documented_names = {c.get("name") for c in entry["columns"]}
                for col in schema_cols:
                    if col.get("name") not in documented_names:
                        entry["columns"].append({
                            "name": col.get("name", ""),
                            "type": col.get("type", ""),
                            "business_description": "",
                            "format": "",
                            "possible_values": [],
                        })
                dictionary.append(entry)
            else:
                # Fallback: create entry from schema only
                dictionary.append({
                    "table": table,
                    "table_description": "",
                    "columns": [
                        {
                            "name": c.get("name", ""),
                            "type": c.get("type", ""),
                            "business_description": c.get("comment", ""),
                            "format": "",
                            "possible_values": [],
                        }
                        for c in schema_cols
                    ],
                    "llm_error": "JSON parsing failed",
                })
        except Exception as e:
            logger.error("LLM doc failed for table %s: %s", table, e)
            dictionary.append({
                "table": table,
                "table_description": "",
                "columns": [
                    {
                        "name": c.get("name", ""),
                        "type": c.get("type", ""),
                        "business_description": "",
                        "format": "",
                        "possible_values": [],
                    }
                    for c in schema_cols
                ],
                "error": str(e),
            })

    return {
        "dictionary": dictionary,
        "messages": msgs_out + [AIMessage(content=f"[DD] Documentation générée pour {len(dictionary)} table(s)")],
    }


def synthesizer_node(state: DataDictionaryState) -> Dict:
    """Build the final markdown summary answer."""
    dictionary = state.get("dictionary") or []
    language = state.get("language", "fr")
    tables_count = len(dictionary)
    errors = sum(1 for e in dictionary if e.get("error"))
    ok_count = tables_count - errors

    if language == "fr":
        summary_lines = [
            f"## Dictionnaire de Données — {tables_count} table(s)",
            f"_Documentées : {ok_count} | Erreurs : {errors} | Langue : Français_\n",
        ]
        for entry in dictionary:
            table = entry.get("table", "")
            desc = entry.get("table_description", "")
            cols = entry.get("columns", [])
            err = entry.get("error", "")
            if err and not cols:
                summary_lines.append(f"### ⚠️ `{table}`\n{desc}\n")
                continue
            summary_lines.append(f"### 📋 `{table}`")
            if desc:
                summary_lines.append(f"{desc}\n")
            summary_lines.append(f"**{len(cols)} colonne(s)** — voir le panneau Dictionnaire ci-dessous.\n")
        final = "\n".join(summary_lines)
    else:
        summary_lines = [
            f"## Data Dictionary — {tables_count} table(s)",
            f"_Documented: {ok_count} | Errors: {errors} | Language: English_\n",
        ]
        for entry in dictionary:
            table = entry.get("table", "")
            desc = entry.get("table_description", "")
            cols = entry.get("columns", [])
            err = entry.get("error", "")
            if err and not cols:
                summary_lines.append(f"### ⚠️ `{table}`\n{desc}\n")
                continue
            summary_lines.append(f"### 📋 `{table}`")
            if desc:
                summary_lines.append(f"{desc}\n")
            summary_lines.append(f"**{len(cols)} column(s)** — see the Dictionary panel below.\n")
        final = "\n".join(summary_lines)

    return {
        "final_answer": final,
        "messages": [AIMessage(content=final[:300] + "…")],
    }


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_data_dictionary_graph():
    from langgraph.checkpoint.memory import MemorySaver

    g = StateGraph(DataDictionaryState)
    g.add_node("discover_tables", discover_tables_node)
    g.add_node("fetch_schemas", fetch_schemas_node)
    g.add_node("llm_doc", llm_doc_node)
    g.add_node("synthesizer", synthesizer_node)

    g.add_edge(START, "discover_tables")
    g.add_edge("discover_tables", "fetch_schemas")
    g.add_edge("fetch_schemas", "llm_doc")
    g.add_edge("llm_doc", "synthesizer")
    g.add_edge("synthesizer", END)

    return g.compile(checkpointer=MemorySaver())
