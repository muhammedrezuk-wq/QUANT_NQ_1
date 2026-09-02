"""Durable pair memory -- v5.3.0 (2026-08-25).

Measured root: pair memory lived only in the clean-stop snapshot, so an
unclean death erased it; the surviving broker leg then graded as a stranger
(CONFLICT / SNAPSHOT_DISAGREES_WITH_BROKER) and froze the whole path. The
owner's continuity line stays exactly as ruled 2026-08-16 -- the store is
CONTINUITY, the broker picture is TRUTH -- this file only makes the
continuity survive any kind of death: every pair mutation is written
through immediately, and on boot the durable record outranks the (possibly
staler) clean-stop snapshot.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = "var/store/pair_memory_578.db"

_SCHEMA = ("CREATE TABLE IF NOT EXISTS pair_state ("
           " id INTEGER PRIMARY KEY CHECK (id = 1),"
           " sealed_json TEXT NOT NULL,"
           " saved_at REAL NOT NULL)")


def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(_SCHEMA)
    return conn


def save(path: str, sealed: dict[str, Any]) -> str:
    """يكتب الحالة المختومة كاملة — ذرّيًّا، ويعيد نص الخطأ إن فشل."""
    try:
        conn = _connect(path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO pair_state(id, sealed_json, saved_at)"
                " VALUES (1, ?, ?)",
                (json.dumps(sealed, ensure_ascii=False), time.time()))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — التخزين لا يُسقط التنفيذ
        return type(exc).__name__
    return ""


def load(path: str) -> dict[str, Any] | None:
    """يعيد الحالة المختومة المحفوظة أو None — الفشل يعامَل كغياب معلَن."""
    try:
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT sealed_json FROM pair_state WHERE id = 1").fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        parsed = json.loads(row[0])
        return parsed if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001
        return None
