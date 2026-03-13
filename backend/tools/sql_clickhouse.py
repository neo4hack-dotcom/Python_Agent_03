"""
ClickHouse SQL Tool with security guardrails, schema injection, error feedback,
and smart output formatting for LangGraph agents.
"""
import re
import json
import logging
from typing import Any, Dict, List, Optional, Tuple, Type
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Blocked SQL operations ────────────────────────────────────────────────────
_BLOCKED_PATTERNS = re.compile(
    r"\b(DROP|TRUNCATE|DELETE|INSERT|UPDATE|CREATE|ALTER|RENAME|ATTACH|DETACH|OPTIMIZE|KILL)\b",
    re.IGNORECASE,
)
_SELECT_PATTERN = re.compile(r"^\s*(SELECT|WITH|EXPLAIN)\b", re.IGNORECASE)


def _sanitize_query(sql: str, row_limit: int) -> Tuple[str, Optional[str]]:
    """
    Validate and sanitize a SQL query.
    Returns (sanitized_sql, error_message).
    error_message is None if safe.
    """
    stripped = sql.strip().rstrip(";")

    if not _SELECT_PATTERN.match(stripped):
        return sql, "SECURITY: Only SELECT / WITH / EXPLAIN queries are allowed."

    blocked = _BLOCKED_PATTERNS.search(stripped)
    if blocked:
        return sql, f"SECURITY: Blocked keyword '{blocked.group()}' detected."

    # Inject LIMIT if not present
    limit_re = re.search(r"\bLIMIT\s+\d+", stripped, re.IGNORECASE)
    if not limit_re:
        stripped = f"{stripped}\nLIMIT {row_limit}"
    else:
        # Ensure existing LIMIT doesn't exceed hard cap
        current_limit = int(re.search(r"\bLIMIT\s+(\d+)", stripped, re.IGNORECASE).group(1))
        if current_limit > row_limit:
            stripped = re.sub(
                r"\bLIMIT\s+\d+", f"LIMIT {row_limit}", stripped, flags=re.IGNORECASE
            )

    return stripped, None


def _rows_to_markdown(columns: List[str], rows: List[tuple], max_rows: int = 50) -> str:
    if not rows:
        return "_No rows returned._"

    truncated = len(rows) > max_rows
    display_rows = rows[:max_rows]

    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = "\n".join(
        "| " + " | ".join(str(v) for v in row) + " |" for row in display_rows
    )
    table = f"{header}\n{separator}\n{body}"

    if truncated:
        table += f"\n\n> ⚠️ Showing first {max_rows} of {len(rows)} rows."

    return table


class ClickHouseSQLTool:
    """
    Wraps a ClickHouse connection and exposes:
    - execute(sql) → structured result dict
    - get_schema(table) → column descriptions
    - explain(sql) → EXPLAIN output
    """

    name = "clickhouse_sql"
    description = (
        "Execute a read-only SQL SELECT query on a ClickHouse database. "
        "Always returns structured results with column names, rows, and row count. "
        "Automatically enforces a row limit. Never allows DDL/DML operations."
    )

    def __init__(self, connection_config: Dict[str, Any], row_limit: int = 1000):
        self.cfg = connection_config
        self.row_limit = row_limit
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import clickhouse_connect
                self._client = clickhouse_connect.get_client(
                    host=self.cfg["host"],
                    port=int(self.cfg.get("port", 8123)),
                    username=self.cfg.get("username", "default"),
                    password=self.cfg.get("password", ""),
                    database=self.cfg.get("database", "default"),
                    connect_timeout=30,
                    **self.cfg.get("extra_params", {}),
                )
            except Exception as e:
                raise ConnectionError(f"ClickHouse connection failed: {e}") from e
        return self._client

    def test_connection(self) -> Dict[str, Any]:
        try:
            client = self._get_client()
            result = client.query("SELECT version()")
            version = result.result_rows[0][0] if result.result_rows else "unknown"
            return {"success": True, "version": version}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_schema(self, table: str, database: Optional[str] = None) -> Dict[str, Any]:
        """Return column info for a table including primary/partition keys."""
        try:
            client = self._get_client()
            db = database or self.cfg.get("database", "default")

            # Column info
            cols_result = client.query(
                f"SELECT name, type, comment FROM system.columns "
                f"WHERE database = '{db}' AND table = '{table}' "
                f"ORDER BY position"
            )
            columns = [
                {"name": row[0], "type": row[1], "comment": row[2]}
                for row in cols_result.result_rows
            ]

            # Table metadata
            meta_result = client.query(
                f"SELECT engine, partition_key, sorting_key, primary_key "
                f"FROM system.tables "
                f"WHERE database = '{db}' AND name = '{table}'"
            )
            meta = {}
            if meta_result.result_rows:
                row = meta_result.result_rows[0]
                meta = {
                    "engine": row[0],
                    "partition_key": row[1],
                    "sorting_key": row[2],
                    "primary_key": row[3],
                }

            return {"table": table, "database": db, "columns": columns, "metadata": meta}
        except Exception as e:
            return {"error": str(e)}

    def list_tables(self, database: Optional[str] = None) -> List[str]:
        try:
            client = self._get_client()
            db = database or self.cfg.get("database", "default")
            result = client.query(
                f"SELECT name FROM system.tables WHERE database = '{db}' ORDER BY name"
            )
            return [row[0] for row in result.result_rows]
        except Exception as e:
            return [f"ERROR: {e}"]

    def list_dictionaries(self) -> List[Dict[str, str]]:
        try:
            client = self._get_client()
            result = client.query(
                "SELECT name, key_names, attribute_names FROM system.dictionaries"
            )
            return [
                {"name": row[0], "key_names": row[1], "attribute_names": row[2]}
                for row in result.result_rows
            ]
        except Exception as e:
            return [{"error": str(e)}]

    def explain(self, sql: str) -> Dict[str, Any]:
        """Run EXPLAIN on a query to validate syntax and estimate cost."""
        try:
            client = self._get_client()
            _, err = _sanitize_query(sql, self.row_limit)
            if err:
                return {"error": err}
            explain_sql = f"EXPLAIN {sql}"
            result = client.query(explain_sql)
            return {
                "success": True,
                "plan": "\n".join(str(row[0]) for row in result.result_rows),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def execute(self, sql: str, dry_run: bool = False) -> Dict[str, Any]:
        """
        Execute a SQL query with full guardrails.

        Returns a dict with:
        - success: bool
        - columns: list[str]
        - rows: list[list]
        - row_count: int
        - markdown_table: str
        - sql_executed: str
        - error: str (only on failure)
        - warning: str (if truncated)
        """
        sanitized, err = _sanitize_query(sql, self.row_limit)
        if err:
            return {"success": False, "error": err, "sql_executed": sql}

        if dry_run:
            return self.explain(sanitized)

        try:
            client = self._get_client()
            result = client.query(sanitized)

            columns = list(result.column_names)
            rows = [list(row) for row in result.result_rows]
            total = len(rows)

            warning = None
            if total >= self.row_limit:
                warning = f"Result was capped at {self.row_limit} rows (hard limit). Use more specific filters."

            # Convert to JSON-safe types
            def _safe(v):
                if hasattr(v, "isoformat"):
                    return v.isoformat()
                return v

            safe_rows = [[_safe(v) for v in row] for row in rows]

            return {
                "success": True,
                "columns": columns,
                "rows": safe_rows,
                "row_count": total,
                "markdown_table": _rows_to_markdown(columns, rows),
                "sql_executed": sanitized,
                "warning": warning,
            }

        except Exception as e:
            error_msg = str(e)
            logger.error("ClickHouse query error: %s | SQL: %s", error_msg, sanitized)
            return {
                "success": False,
                "error": error_msg,
                "sql_executed": sanitized,
                "hint": "Check the error message carefully and correct the SQL accordingly.",
            }

    def get_slow_queries(self, limit: int = 20) -> Dict[str, Any]:
        """Audit: fetch slowest recent queries from system.query_log."""
        sql = f"""
        SELECT
            query_id,
            query,
            query_duration_ms,
            read_rows,
            read_bytes,
            result_rows,
            exception
        FROM system.query_log
        WHERE type = 'QueryFinish'
            AND event_time >= now() - INTERVAL 1 HOUR
        ORDER BY query_duration_ms DESC
        LIMIT {limit}
        """
        return self.execute(sql)
