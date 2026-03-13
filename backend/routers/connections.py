"""
Database connection management endpoints.
"""
from typing import List
from fastapi import APIRouter, HTTPException
from datetime import datetime

from backend.models.connection import ConnectionConfig, ConnectionCreate, ConnectionType
from backend.database import db, COLL_CONNECTIONS
from backend.tools.sql_clickhouse import ClickHouseSQLTool
from backend.tools.sql_oracle import OracleSQLTool

router = APIRouter(prefix="/api/connections", tags=["Connections"])


def _build_tool(conn: dict):
    if conn["type"] == ConnectionType.CLICKHOUSE:
        return ClickHouseSQLTool(conn)
    elif conn["type"] == ConnectionType.ORACLE:
        return OracleSQLTool(conn)
    raise ValueError(f"Unsupported connection type: {conn['type']}")


@router.get("", response_model=List[ConnectionConfig])
def list_connections():
    conns = db.get_all(COLL_CONNECTIONS)
    # Mask passwords in response
    result = []
    for c in conns:
        c = dict(c)
        c["password"] = "***"
        result.append(ConnectionConfig(**c))
    return result


@router.post("", response_model=ConnectionConfig, status_code=201)
def create_connection(payload: ConnectionCreate):
    conn = ConnectionConfig(**payload.model_dump())
    db.set(COLL_CONNECTIONS, conn.id, conn.model_dump())
    resp = conn.model_dump()
    resp["password"] = "***"
    return ConnectionConfig(**resp)


@router.get("/{conn_id}", response_model=ConnectionConfig)
def get_connection(conn_id: str):
    raw = db.get(COLL_CONNECTIONS, conn_id)
    if not raw:
        raise HTTPException(404, "Connection not found")
    raw = dict(raw)
    raw["password"] = "***"
    return ConnectionConfig(**raw)


@router.put("/{conn_id}", response_model=ConnectionConfig)
def update_connection(conn_id: str, payload: ConnectionCreate):
    existing = db.get(COLL_CONNECTIONS, conn_id)
    if not existing:
        raise HTTPException(404, "Connection not found")
    updated = dict(existing)
    updated.update(payload.model_dump())
    updated["updated_at"] = datetime.utcnow().isoformat()
    db.set(COLL_CONNECTIONS, conn_id, updated)
    updated["password"] = "***"
    return ConnectionConfig(**updated)


@router.delete("/{conn_id}")
def delete_connection(conn_id: str):
    if not db.delete(COLL_CONNECTIONS, conn_id):
        raise HTTPException(404, "Connection not found")
    return {"message": "Deleted"}


@router.post("/{conn_id}/test")
def test_connection(conn_id: str):
    raw = db.get(COLL_CONNECTIONS, conn_id)
    if not raw:
        raise HTTPException(404, "Connection not found")
    try:
        tool = _build_tool(raw)
        return tool.test_connection()
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/{conn_id}/tables")
def list_tables(conn_id: str, database: str = None):
    raw = db.get(COLL_CONNECTIONS, conn_id)
    if not raw:
        raise HTTPException(404, "Connection not found")
    try:
        tool = _build_tool(raw)
        tables = tool.list_tables(database)
        return {"tables": tables}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/{conn_id}/schema/{table}")
def get_table_schema(conn_id: str, table: str, database: str = None):
    raw = db.get(COLL_CONNECTIONS, conn_id)
    if not raw:
        raise HTTPException(404, "Connection not found")
    try:
        tool = _build_tool(raw)
        return tool.get_schema(table, database)
    except Exception as e:
        raise HTTPException(500, str(e))
