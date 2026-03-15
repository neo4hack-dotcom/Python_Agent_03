"""
Data Quality Agent — profiling statistique + analyse LLM de qualité de données.

Pipeline linéaire :
  parse_node → schema_node → stats_node → [volumetric_node] → llm_node → synthesizer_node

Entrée : message JSON structuré émis par DataQualityForm :
  {
    "__dq__": true,
    "table": "ventes",
    "columns": ["montant", "date_vente", "statut"],
    "sample_size": 50000,          # 0 = full scan
    "row_filter": "region = 'FR'", # optionnel
    "time_column": "date_vente"    # optionnel
  }
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from typing_extensions import Annotated, TypedDict
from langgraph.graph.message import add_messages

from backend.database import db, COLL_AGENTS, COLL_CONNECTIONS
from backend.graphs.llm_factory import build_llm
from backend.tools.sql_clickhouse import ClickHouseSQLTool
from backend.tools.sql_oracle import OracleSQLTool

logger = logging.getLogger(__name__)

# ── Row-filter safeguard ─────────────────────────────────────────────────────
_FILTER_BLOCKED = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|CREATE|ALTER|EXEC|EXECUTE|UNION|SLEEP|BENCHMARK|LOAD|INTO|OUTFILE)\b",
    re.IGNORECASE,
)


def _validate_filter(f: str) -> Optional[str]:
    if _FILTER_BLOCKED.search(f):
        return "Filtre invalide : mot-clé SQL dangereux détecté."
    return None


# ── Column type detection ────────────────────────────────────────────────────
def _detect_col_type(raw_type: str) -> str:
    """Map a ClickHouse/Oracle type string to 'numeric', 'string', 'date', or 'other'."""
    t = raw_type.lower()
    # Strip Nullable / LowCardinality wrappers
    for wrapper in ("nullable(", "lowcardinality("):
        if t.startswith(wrapper):
            t = t[len(wrapper):-1]
    if any(t.startswith(p) for p in ("int", "uint", "float", "decimal", "number")):
        return "numeric"
    if any(t.startswith(p) for p in ("string", "fixedstring", "varchar", "char", "nvarchar", "nchar", "text", "clob")):
        return "string"
    if any(t.startswith(p) for p in ("date", "datetime", "timestamp")):
        return "date"
    return "other"


# ── SQL builders ─────────────────────────────────────────────────────────────
def _sample_wrapper(inner: str, sample_size: int, db_type: str, col: str) -> str:
    """Wrap a column reference in a sample subquery if sample_size > 0."""
    if sample_size == 0:
        return inner  # full scan
    # Replace "FROM {table}" with "FROM (SELECT {col} FROM {table} ... LIMIT N)"
    # This is handled at the outer query level — we pass the source table snippet instead
    return inner


def _build_source(table: str, row_filter: Optional[str], sample_size: int, db_type: str) -> str:
    """Build the FROM / WHERE / LIMIT part for sampling."""
    where = f" WHERE {row_filter}" if row_filter else ""
    if sample_size > 0:
        if db_type == "oracle":
            return f"(SELECT * FROM {table}{where} WHERE ROWNUM <= {sample_size})"
        else:
            return f"(SELECT * FROM {table}{where} LIMIT {sample_size})"
    return f"{table}{where}"


def _build_common_select(col: str) -> str:
    return f"""
    count() AS total,
    countIf({col} IS NULL) AS null_count,
    round(countIf({col} IS NULL) / count() * 100, 2) AS null_pct,
    uniqExact({col}) AS distinct_count,
    round(uniqExact({col}) / count() * 100, 2) AS distinct_pct,
    topK(10)({col}) AS top_values"""


def _stats_sql_clickhouse(col: str, col_type: str, source: str) -> str:
    common = _build_common_select(col)
    if col_type == "numeric":
        return f"""SELECT
    {common},
    min({col}) AS min_val,
    max({col}) AS max_val,
    round(avg({col}), 6) AS avg_val,
    round(stddevSamp({col}), 6) AS stddev_val,
    round(quantile(0.25)({col}), 6) AS p25,
    round(quantile(0.50)({col}), 6) AS p50,
    round(quantile(0.75)({col}), 6) AS p75,
    countIf({col} < 0) AS negative_count,
    countIf({col} = 0) AS zero_count
FROM {source}"""
    elif col_type == "string":
        return f"""SELECT
    {common},
    countIf({col} = '') AS empty_count,
    round(countIf({col} = '') / count() * 100, 2) AS empty_pct,
    min(length({col})) AS min_length,
    max(length({col})) AS max_length,
    round(avg(length({col})), 2) AS avg_length,
    countIf(length({col}) > 1000) AS very_long_count,
    countIf(trimBoth({col}) != {col}) AS whitespace_padded_count,
    countIf(upper({col}) = {col} AND {col} != lower({col})) AS all_caps_count,
    countIf(match({col}, '^[0-9]+(\\\\.[0-9]+)?$')) AS numeric_string_count,
    countIf(match({col}, '^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\\\.[a-zA-Z]{{2,}}$')) AS email_like_count,
    countIf({col} IN ('N/A','NA','NULL','null','None','none','-','0','n/a','undefined','unknown','#N/A','9999','-1','999')) AS sentinel_count
FROM {source}"""
    elif col_type == "date":
        return f"""SELECT
    {common},
    min({col}) AS min_date,
    max({col}) AS max_date,
    countIf({col} > now()) AS future_count,
    countIf({col} < toDateTime('1970-01-02 00:00:00')) AS epoch_sentinel_count,
    countIf(toDayOfWeek({col}) IN (6, 7)) AS weekend_count,
    countIf(toYear({col}) < 1900) AS pre_1900_count
FROM {source}"""
    else:
        # Generic stats for unknown types
        return f"""SELECT
    {common}
FROM {source}"""


def _stats_sql_oracle(col: str, col_type: str, source: str) -> str:
    """Oracle equivalent stats queries (subset)."""
    if col_type == "numeric":
        return f"""SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) AS null_count,
    ROUND(SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS null_pct,
    COUNT(DISTINCT {col}) AS distinct_count,
    ROUND(COUNT(DISTINCT {col}) / COUNT(*) * 100, 2) AS distinct_pct,
    MIN({col}) AS min_val,
    MAX({col}) AS max_val,
    ROUND(AVG({col}), 6) AS avg_val,
    ROUND(STDDEV({col}), 6) AS stddev_val,
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {col}) AS p25,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY {col}) AS p50,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {col}) AS p75,
    SUM(CASE WHEN {col} < 0 THEN 1 ELSE 0 END) AS negative_count,
    SUM(CASE WHEN {col} = 0 THEN 1 ELSE 0 END) AS zero_count
FROM {source}"""
    elif col_type == "string":
        return f"""SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) AS null_count,
    ROUND(SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS null_pct,
    COUNT(DISTINCT {col}) AS distinct_count,
    ROUND(COUNT(DISTINCT {col}) / COUNT(*) * 100, 2) AS distinct_pct,
    SUM(CASE WHEN {col} = '' THEN 1 ELSE 0 END) AS empty_count,
    ROUND(SUM(CASE WHEN {col} = '' THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS empty_pct,
    MIN(LENGTH({col})) AS min_length,
    MAX(LENGTH({col})) AS max_length,
    ROUND(AVG(LENGTH({col})), 2) AS avg_length
FROM {source}"""
    elif col_type == "date":
        return f"""SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) AS null_count,
    ROUND(SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS null_pct,
    COUNT(DISTINCT {col}) AS distinct_count,
    MIN({col}) AS min_date,
    MAX({col}) AS max_date,
    SUM(CASE WHEN {col} > SYSDATE THEN 1 ELSE 0 END) AS future_count,
    SUM(CASE WHEN EXTRACT(YEAR FROM {col}) < 1900 THEN 1 ELSE 0 END) AS pre_1900_count
FROM {source}"""
    else:
        return f"""SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) AS null_count,
    ROUND(SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS null_pct,
    COUNT(DISTINCT {col}) AS distinct_count
FROM {source}"""


def _outlier_sql_clickhouse(col: str, avg: float, stddev: float, lower: float, upper: float, source: str) -> str:
    sd = stddev if stddev and stddev != 0 else 1
    return f"""SELECT
    countIf({col} < {lower} OR {col} > {upper}) AS iqr_outlier_count,
    round(countIf({col} < {lower} OR {col} > {upper}) / count() * 100, 2) AS iqr_outlier_pct,
    countIf(abs(({col} - {avg}) / {sd}) > 3) AS zscore_outlier_count,
    round(countIf(abs(({col} - {avg}) / {sd}) > 3) / count() * 100, 2) AS zscore_outlier_pct
FROM {source}"""


def _volumetric_sql_clickhouse(time_col: str, source: str, granularity: str) -> str:
    if granularity == "hour":
        period_fn = f"toStartOfHour({time_col})"
    else:
        period_fn = f"toStartOfDay({time_col})"
    return f"""SELECT
    {period_fn} AS period,
    count() AS volume
FROM {source}
GROUP BY period
ORDER BY period"""


# ── Python-side derived metrics ───────────────────────────────────────────────
def _derive_numeric_metrics(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Compute coeff_variation, skewness_approx, IQR fences from base stats."""
    derived = dict(stats)
    avg = _float(stats.get("avg_val"))
    stddev = _float(stats.get("stddev_val"))
    p25 = _float(stats.get("p25"))
    p50 = _float(stats.get("p50"))
    p75 = _float(stats.get("p75"))

    if avg is not None and stddev is not None and avg != 0:
        derived["coeff_variation"] = round(stddev / abs(avg), 4) if avg != 0 else None
    else:
        derived["coeff_variation"] = None

    if avg is not None and stddev is not None and stddev != 0 and p50 is not None:
        derived["skewness_approx"] = round(3 * (avg - p50) / stddev, 4)
    else:
        derived["skewness_approx"] = None

    if p25 is not None and p75 is not None:
        iqr = p75 - p25
        derived["iqr"] = round(iqr, 6)
        derived["lower_fence"] = round(p25 - 1.5 * iqr, 6)
        derived["upper_fence"] = round(p75 + 1.5 * iqr, 6)
    else:
        derived["iqr"] = derived["lower_fence"] = derived["upper_fence"] = None

    return derived


def _derive_volumetric_metrics(rows: List[List]) -> Dict[str, Any]:
    """Compute volumetric stats from period/volume rows."""
    if not rows:
        return {}
    volumes = [r[1] for r in rows if r[1] is not None]
    if not volumes:
        return {}
    n = len(volumes)
    avg_vol = sum(volumes) / n
    variance = sum((v - avg_vol) ** 2 for v in volumes) / n if n > 1 else 0
    stddev_vol = math.sqrt(variance)
    sorted_vols = sorted(volumes)
    p25 = sorted_vols[int(n * 0.25)]
    p75 = sorted_vols[int(n * 0.75)]
    iqr = p75 - p25
    low_threshold = p25 - 1.5 * iqr

    anomaly_periods = []
    recent_periods = []
    for i, (period, vol) in enumerate(rows[-10:], len(rows) - 10):
        recent_periods.append({"period": str(period), "volume": vol})
    for period, vol in rows:
        if vol < low_threshold:
            anomaly_periods.append({"period": str(period), "volume": vol})

    return {
        "total_periods": n,
        "avg_volume": round(avg_vol, 2),
        "stddev_volume": round(stddev_vol, 2),
        "min_volume": min(volumes),
        "max_volume": max(volumes),
        "p25_volume": p25,
        "p75_volume": p75,
        "low_threshold": round(low_threshold, 2),
        "anomaly_periods_count": len(anomaly_periods),
        "anomaly_periods": anomaly_periods[:20],
        "recent_periods": recent_periods,
    }


def _float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── State ────────────────────────────────────────────────────────────────────
class DataQualityState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    # Parsed input
    table: str
    columns: List[str]
    sample_size: int
    row_filter: Optional[str]
    time_column: Optional[str]
    db_type: str
    # Intermediate
    schema_info: Optional[Dict[str, Any]]
    column_stats: Optional[Dict[str, Any]]
    volumetric_stats: Optional[Dict[str, Any]]
    # Output
    llm_analysis: Optional[str]
    final_answer: Optional[str]
    # Infra
    agent_id: str
    session_id: str
    last_error: Optional[str]


# ── LLM System Prompt ────────────────────────────────────────────────────────
DQ_SYSTEM_PROMPT = """Tu es un expert Data Quality et Data Engineering. Tu reçois les statistiques de profiling de colonnes d'une table de base de données et tu analyses la qualité des données.

Pour chaque colonne fournie, évalue les dimensions suivantes :
1. **Nulls / vides / sentinelles** : taux de null, valeurs vides, valeurs sentinelles suspectes (N/A, -1, 9999, 0 pour des montants…)
2. **Formats incohérents** : emails invalides, longueurs anormales, mélange de casse, colonnes texte contenant des nombres
3. **Valeurs aberrantes métier** : outliers IQR et z-score, valeurs négatives sur des colonnes qui devraient être positives
4. **Cardinalité suspecte** : trop peu de valeurs distinctes (quasi-constante — distinct_pct < 1%), ou trop élevée (clé cachée — distinct_pct > 90% sur une colonne censée être catégorielle)
5. **Distributions anormales** : skewness |>2|, coeff_variation >1 (forte variabilité), p50 très différent du mean (distribution non-normale)
6. **Anomalies temporelles** : dates futures, dates epoch (1970), dates pré-1900, week-end suspects pour des données métier
7. **Cohérence volumétrique** : si l'analyse temporelle est fournie, périodes avec volume anormalement bas

Calcule un **score de qualité global** de 0 à 100 :
- Commence à 100, retire des points selon la sévérité :
  - 🔴 Critique (null_pct > 20%, outliers > 10%, sentinelles > 5%) : -15 pts chacun
  - 🟡 Warning (null_pct 5-20%, outliers 2-10%, cardinalité suspecte) : -5 pts chacun
  - 🟢 OK : aucun retrait

Structure ta réponse en **Markdown** :

## Résumé Exécutif
Score global : X/100
Problèmes critiques : N | Warnings : N | OK : N

## Analyse par Colonne
### `nom_colonne` — 🔴/🟡/🟢 [Critique/Warning/OK]
Chiffres clés + constats + risques métier

## Top Recommandations
Liste priorisée avec impact business

## Analyse Volumétrique *(si applicable)*
Constats sur les anomalies de volume temporel

Sois précis et chiffré. Cite les valeurs statistiques clés qui justifient chaque constat. Évite le jargon excessif — le rapport doit être lisible par un Product Owner."""


# ── Helper : get SQL tool from agent config ──────────────────────────────────
def _get_tool_and_type(agent_id: str):
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
    row_limit = agent_cfg.get("row_limit", 500000)
    if db_type == "oracle":
        return OracleSQLTool(conn_cfg, row_limit=row_limit), "oracle"
    return ClickHouseSQLTool(conn_cfg, row_limit=row_limit), "clickhouse"


def _execute_no_limit(tool, sql: str) -> Dict[str, Any]:
    """Execute without the automatic LIMIT injection (used for aggregate queries)."""
    # Aggregate queries must not be wrapped in LIMIT subquery
    # We temporarily set a very high row_limit to avoid truncation on aggregates
    old_limit = tool.row_limit
    tool.row_limit = 2_000_000
    result = tool.execute(sql)
    tool.row_limit = old_limit
    return result


# ── Graph nodes ──────────────────────────────────────────────────────────────
def schema_node(state: DataQualityState) -> Dict:
    """Fetch column types for all requested columns from DB schema."""
    try:
        tool, db_type = _get_tool_and_type(state["agent_id"])
        schema = tool.get_schema(state["table"], columns=state["columns"])
        if "error" in schema:
            return {"last_error": schema["error"], "schema_info": {}, "db_type": db_type}
        col_info = {}
        for c in schema.get("columns", []):
            name = c["name"]
            raw_type = c.get("type", "")
            col_info[name] = {
                "raw_type": raw_type,
                "col_type": _detect_col_type(raw_type),
                "comment": c.get("comment", ""),
            }
        return {
            "schema_info": col_info,
            "db_type": db_type,
            "messages": [AIMessage(content=f"[DQ] Schéma récupéré : {len(col_info)} colonnes")],
        }
    except Exception as e:
        return {
            "last_error": str(e),
            "schema_info": {},
            "db_type": state.get("db_type", "clickhouse"),
            "messages": [AIMessage(content=f"[DQ] Erreur schéma : {e}")],
        }


def stats_node(state: DataQualityState) -> Dict:
    """Run per-column statistical SQL queries."""
    try:
        tool, db_type = _get_tool_and_type(state["agent_id"])
    except Exception as e:
        return {"last_error": str(e), "column_stats": {}}

    schema_info = state.get("schema_info") or {}
    sample_size = state.get("sample_size", 0)
    row_filter = state.get("row_filter")
    table = state["table"]
    db_type = state.get("db_type", "clickhouse")

    # Validate filter
    if row_filter:
        err = _validate_filter(row_filter)
        if err:
            return {"last_error": err, "column_stats": {}}

    source = _build_source(table, row_filter, sample_size, db_type)
    all_stats = {}

    for col in state.get("columns", []):
        col_meta = schema_info.get(col, {})
        col_type = col_meta.get("col_type", "other")

        # Build stats SQL
        if db_type == "oracle":
            sql = _stats_sql_oracle(col, col_type, source)
        else:
            sql = _stats_sql_clickhouse(col, col_type, source)

        result = _execute_no_limit(tool, sql)
        if not result.get("success"):
            all_stats[col] = {"error": result.get("error", "query failed"), "col_type": col_type}
            continue

        # Parse single-row result into dict
        cols_names = result.get("columns", [])
        rows = result.get("rows", [])
        if not rows:
            all_stats[col] = {"total": 0, "col_type": col_type}
            continue

        row = rows[0]
        stats = dict(zip(cols_names, row))
        stats["col_type"] = col_type

        # Derived numeric metrics + outlier query (ClickHouse only for full derivation)
        if col_type == "numeric" and db_type == "clickhouse":
            stats = _derive_numeric_metrics(stats)
            lower = stats.get("lower_fence")
            upper = stats.get("upper_fence")
            avg = _float(stats.get("avg_val"))
            stddev = _float(stats.get("stddev_val"))
            if lower is not None and upper is not None and avg is not None and stddev is not None:
                outlier_sql = _outlier_sql_clickhouse(col, avg, stddev, lower, upper, source)
                outlier_res = _execute_no_limit(tool, outlier_sql)
                if outlier_res.get("success") and outlier_res.get("rows"):
                    outlier_row = dict(zip(outlier_res["columns"], outlier_res["rows"][0]))
                    stats.update(outlier_row)
        elif col_type == "numeric":
            stats = _derive_numeric_metrics(stats)

        all_stats[col] = stats

    return {
        "column_stats": all_stats,
        "messages": [AIMessage(content=f"[DQ] Statistiques calculées pour {len(all_stats)} colonnes")],
    }


def volumetric_node(state: DataQualityState) -> Dict:
    """Optional: compute time-series volume stats if time_column is set."""
    time_col = state.get("time_column")
    if not time_col:
        return {}

    try:
        tool, db_type = _get_tool_and_type(state["agent_id"])
    except Exception as e:
        return {"last_error": str(e)}

    sample_size = state.get("sample_size", 0)
    row_filter = state.get("row_filter")
    table = state["table"]
    source = _build_source(table, row_filter, sample_size, db_type)

    # Determine date range to choose granularity
    range_sql = f"SELECT min({time_col}) AS min_ts, max({time_col}) AS max_ts FROM {source}"
    range_res = _execute_no_limit(tool, range_sql)
    granularity = "day"
    if range_res.get("success") and range_res.get("rows"):
        try:
            min_ts, max_ts = range_res["rows"][0]
            if min_ts and max_ts:
                from datetime import datetime as dt
                if isinstance(min_ts, str):
                    min_ts = dt.fromisoformat(min_ts)
                    max_ts = dt.fromisoformat(max_ts)
                delta_days = (max_ts - min_ts).days if hasattr(max_ts, "days") else 0
                if delta_days <= 7:
                    granularity = "hour"
        except Exception:
            pass

    if db_type == "clickhouse":
        vol_sql = _volumetric_sql_clickhouse(time_col, source, granularity)
    else:
        period_fn = f"TRUNC({time_col}, 'HH24')" if granularity == "hour" else f"TRUNC({time_col}, 'DD')"
        vol_sql = f"""SELECT {period_fn} AS period, COUNT(*) AS volume
FROM {source}
GROUP BY {period_fn}
ORDER BY {period_fn}"""

    vol_res = _execute_no_limit(tool, vol_sql)
    if not vol_res.get("success"):
        return {"volumetric_stats": {"error": vol_res.get("error")}}

    rows = vol_res.get("rows", [])
    vol_stats = _derive_volumetric_metrics(rows)
    vol_stats["granularity"] = granularity
    vol_stats["time_column"] = time_col

    return {
        "volumetric_stats": vol_stats,
        "messages": [AIMessage(content=f"[DQ] Analyse volumétrique : {vol_stats.get('total_periods', 0)} périodes, {vol_stats.get('anomaly_periods_count', 0)} anomalie(s)")],
    }


def llm_analysis_node(state: DataQualityState) -> Dict:
    """Call LLM to analyse collected stats and generate DQ recommendations."""
    try:
        llm = build_llm()

        column_stats = state.get("column_stats") or {}
        vol_stats = state.get("volumetric_stats")
        schema_info = state.get("schema_info") or {}

        # Build a compact JSON payload for the LLM
        payload = {
            "table": state["table"],
            "sample_size": state.get("sample_size", 0),
            "row_filter": state.get("row_filter"),
            "columns": [],
        }
        for col, stats in column_stats.items():
            entry = {
                "name": col,
                "type": stats.get("col_type", "?"),
                "raw_type": schema_info.get(col, {}).get("raw_type", ""),
                "stats": {k: (round(v, 4) if isinstance(v, float) else v)
                           for k, v in stats.items()
                           if k not in ("col_type", "top_values") and v is not None},
            }
            # Include top_values but truncate
            tv = stats.get("top_values")
            if tv:
                entry["top_values"] = [str(x) for x in (tv[:10] if isinstance(tv, list) else [tv])]
            payload["columns"].append(entry)

        if vol_stats and not vol_stats.get("error"):
            payload["volumetric_analysis"] = vol_stats

        user_msg = f"""Voici les statistiques de profiling de la table **{state['table']}** :

```json
{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}
```

Analyse la qualité des données et produis un rapport structuré selon les instructions."""

        messages = [
            SystemMessage(content=DQ_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ]
        response = llm.invoke(messages)
        analysis = response.content if hasattr(response, "content") else str(response)

        return {
            "llm_analysis": analysis,
            "messages": [AIMessage(content="[DQ] Analyse LLM terminée")],
        }
    except Exception as e:
        logger.error("llm_analysis_node error: %s", e, exc_info=True)
        return {
            "llm_analysis": f"⚠️ Erreur lors de l'analyse LLM : {e}",
            "last_error": str(e),
            "messages": [AIMessage(content=f"[DQ] Erreur analyse LLM : {e}")],
        }


def synthesizer_node(state: DataQualityState) -> Dict:
    """Format and expose the final answer."""
    analysis = state.get("llm_analysis", "")
    col_count = len(state.get("columns", []))
    sample = state.get("sample_size", 0)
    sample_label = "scan complet" if sample == 0 else f"{sample:,} lignes"
    vol = state.get("volumetric_stats")

    header = (
        f"## Analyse Data Quality — `{state['table']}`\n"
        f"_Colonnes analysées : {col_count} | Échantillon : {sample_label}"
        + (f" | Analyse temporelle : `{state.get('time_column')}`" if vol and not vol.get("error") else "")
        + "_\n\n"
    )

    final = header + (analysis or "_Aucune analyse disponible._")

    return {
        "final_answer": final,
        "messages": [AIMessage(content=final[:200] + "…")],
    }


# ── Graph routing ─────────────────────────────────────────────────────────────
def _should_run_volumetric(state: DataQualityState) -> str:
    return "volumetric" if state.get("time_column") else "llm"


# ── Graph builder ─────────────────────────────────────────────────────────────
def build_data_quality_graph():
    from langgraph.checkpoint.memory import MemorySaver

    g = StateGraph(DataQualityState)
    g.add_node("schema", schema_node)
    g.add_node("stats", stats_node)
    g.add_node("volumetric", volumetric_node)
    g.add_node("llm_analysis", llm_analysis_node)
    g.add_node("synthesizer", synthesizer_node)

    g.add_edge(START, "schema")
    g.add_edge("schema", "stats")
    g.add_conditional_edges("stats", _should_run_volumetric, {
        "volumetric": "volumetric",
        "llm": "llm_analysis",
    })
    g.add_edge("volumetric", "llm_analysis")
    g.add_edge("llm_analysis", "synthesizer")
    g.add_edge("synthesizer", END)

    return g.compile(checkpointer=MemorySaver())
