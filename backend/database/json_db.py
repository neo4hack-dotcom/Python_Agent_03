"""
JSON-based local database for persistent storage of configurations and transient data.
"""
import json
import os
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
import threading

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

_lock = threading.Lock()


def _db_path(collection: str) -> Path:
    return DATA_DIR / f"{collection}.json"


def _read(collection: str) -> Dict[str, Any]:
    path = _db_path(collection)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _write(collection: str, data: Dict[str, Any]) -> None:
    path = _db_path(collection)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    tmp.replace(path)


class JsonDB:
    """Simple JSON-backed key-value store with collection support."""

    def get(self, collection: str, key: str) -> Optional[Any]:
        with _lock:
            data = _read(collection)
            return data.get(key)

    def get_all(self, collection: str) -> List[Any]:
        with _lock:
            data = _read(collection)
            return list(data.values())

    def set(self, collection: str, key: str, value: Any) -> None:
        with _lock:
            data = _read(collection)
            data[key] = value
            _write(collection, data)

    def delete(self, collection: str, key: str) -> bool:
        with _lock:
            data = _read(collection)
            if key in data:
                del data[key]
                _write(collection, data)
                return True
            return False

    def exists(self, collection: str, key: str) -> bool:
        with _lock:
            data = _read(collection)
            return key in data

    def upsert(self, collection: str, key: str, updates: Dict[str, Any]) -> Any:
        with _lock:
            data = _read(collection)
            existing = data.get(key, {})
            existing.update(updates)
            existing["updated_at"] = datetime.utcnow().isoformat()
            data[key] = existing
            _write(collection, data)
            return existing

    def append_to_list(self, collection: str, key: str, item: Any) -> None:
        with _lock:
            data = _read(collection)
            lst = data.get(key, [])
            if not isinstance(lst, list):
                lst = []
            lst.append(item)
            data[key] = lst
            _write(collection, data)

    def get_list(self, collection: str, key: str) -> List[Any]:
        with _lock:
            data = _read(collection)
            val = data.get(key, [])
            return val if isinstance(val, list) else []

    def clear_collection(self, collection: str) -> None:
        with _lock:
            _write(collection, {})


# Singleton
db = JsonDB()


# ── Collection names ──────────────────────────────────────────────────────────
COLL_AGENTS = "agents"
COLL_CONNECTIONS = "connections"
COLL_LLM_CONFIG = "llm_config"
COLL_SESSIONS = "sessions"
COLL_MESSAGES = "messages"
