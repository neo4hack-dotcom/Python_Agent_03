"""
Oracle SQL Tool with security guardrails, matching the ClickHouse tool interface.
"""
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_BLOCKED_PATTERNS = re.compile(
    r"\b(DROP|TRUNCATE|DELETE|INSERT|UPDATE|CREATE|ALTER|RENAME|MERGE|CALL|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)
_SELECT_PATTERN = re.compile(r"^\s*(SELECT|WITH|EXPLAIN\s+PLAN)\b", re.IGNORECASE)


def _sanitize_query(sql: str, row_limit: int) -> Tuple[str, Optional[str]]:
    stripped = sql.strip().rstrip(";")

    if not _SELECT_PATTERN.match(stripped):
        return sql, "SECURITY: Only SELECT / WITH queries are allowed."

    blocked = _BLOCKED_PATTERNS.search(stripped)
    if blocked:
        return sql, f"SECURITY: Blocked keyword '{blocked.group()}' detected."

    # Oracle uses FETCH FIRST n ROWS ONLY or ROWNUM
    if not re.search(r"\bFETCH\s+FIRST\b|\bROWNUM\b|\bROW_NUMBER\b", stripped, re.IGNORECASE):
        stripped = (
            f"SELECT * FROM ({stripped}) WHERE ROWNUM <= {row_limit}"
        )

    return stripped, None


def _rows_to_markdown(columns: List[str], rows: List[list], max_rows: int = 50) -> str:
    if not rows:
        return "_No rows returned._"
    truncated = len(rows) > max_rows
    display = rows[:max_rows]
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = "\n".join("| " + " | ".join(str(v) for v in row) + " |" for row in display)
    table = f"{header}\n{sep}\n{body}"
    if truncated:
        table += f"\n\n> ⚠️ Showing first {max_rows} of {len(rows)} rows."
    return table


class OracleSQLTool:
    """
    Wraps an Oracle connection and exposes execute(), get_schema(), test_connection().
    """

    name = "oracle_sql"
    description = (
        "Execute a read-only SQL SELECT query on an Oracle database. "
        "Returns structured results with column names and rows. "
        "Automatically enforces a row limit. DDL/DML is blocked."
    )

    def __init__(self, connection_config: Dict[str, Any], row_limit: int = 1000):
        self.cfg = connection_config
        self.row_limit = row_limit
        self._pool = None

    def _get_connection(self):
        try:
            import oracledb
            dsn = self.cfg.get("dsn") or oracledb.makedsn(
                self.cfg["host"],
                int(self.cfg.get("port", 1521)),
                service_name=self.cfg.get("database", ""),
            )
            conn = oracledb.connect(
                user=self.cfg.get("username"),
                password=self.cfg.get("password"),
                dsn=dsn,
            )
            return conn
        except Exception as e:
            raise ConnectionError(f"Oracle connection failed: {e}") from e

    def test_connection(self) -> Dict[str, Any]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT banner FROM v$version WHERE ROWNUM=1")
            row = cursor.fetchone()
            conn.close()
            return {"success": True, "version": row[0] if row else "unknown"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_schema(self, table: str, schema: Optional[str] = None) -> Dict[str, Any]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            owner = (schema or self.cfg.get("username", "")).upper()
            table_upper = table.upper()

            cursor.execute(
                """
                SELECT column_name, data_type, data_length, nullable, data_default
                FROM all_tab_columns
                WHERE owner = :owner AND table_name = :tbl
                ORDER BY column_id
                """,
                {"owner": owner, "tbl": table_upper},
            )
            columns = [
                {
                    "name": row[0],
                    "type": row[1],
                    "length": row[2],
                    "nullable": row[3],
                    "default": row[4],
                }
                for row in cursor.fetchall()
            ]
            conn.close()
            return {"table": table, "schema": owner, "columns": columns}
        except Exception as e:
            return {"error": str(e)}

    def list_tables(self, schema: Optional[str] = None) -> List[str]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            owner = (schema or self.cfg.get("username", "")).upper()
            cursor.execute(
                "SELECT table_name FROM all_tables WHERE owner = :owner ORDER BY table_name",
                {"owner": owner},
            )
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()
            return tables
        except Exception as e:
            return [f"ERROR: {e}"]

    def execute(self, sql: str) -> Dict[str, Any]:
        sanitized, err = _sanitize_query(sql, self.row_limit)
        if err:
            return {"success": False, "error": err, "sql_executed": sql}

        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(sanitized)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            conn.close()

            total = len(rows)
            warning = None
            if total >= self.row_limit:
                warning = f"Result capped at {self.row_limit} rows. Use more specific filters."

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
                "markdown_table": _rows_to_markdown(columns, list(rows)),
                "sql_executed": sanitized,
                "warning": warning,
            }

        except Exception as e:
            error_msg = str(e)
            logger.error("Oracle query error: %s | SQL: %s", error_msg, sanitized)
            return {
                "success": False,
                "error": error_msg,
                "sql_executed": sanitized,
                "hint": "Check the error message and correct the SQL. Common issues: wrong table/column names, type mismatches.",
            }
