"""Tiny SQLite key/value cache with TTL, used for search results and fetched pages so that
re-scanning the same text is fast and does not hammer the (free, rate-limited) search engines."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

from . import config

_DB = config.DATA_DIR / "cache.sqlite3"
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS kv (ns TEXT, k TEXT, v TEXT, ts REAL, PRIMARY KEY (ns, k))")
    return c


def get(ns: str, key: str, ttl: float):
    with _lock, _conn() as c:
        row = c.execute("SELECT v, ts FROM kv WHERE ns=? AND k=?", (ns, key)).fetchone()
    if not row or time.time() - row[1] > ttl:
        return None
    return json.loads(row[0])


def put(ns: str, key: str, value) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO kv (ns, k, v, ts) VALUES (?,?,?,?)", (ns, key, json.dumps(value), time.time()))


def purge(max_age: float = 30 * 86400) -> None:
    with _lock, _conn() as c:
        c.execute("DELETE FROM kv WHERE ts < ?", (time.time() - max_age,))
