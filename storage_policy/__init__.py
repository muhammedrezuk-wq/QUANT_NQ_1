"""Shared, bounded SQLite retention policy for storage atoms."""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_DB_TIMEOUT_S = 5.0
_BUSY_TIMEOUT_MS = 3000


def database_bytes(path: str) -> int:
    return sum(Path(path + suffix).stat().st_size
               for suffix in ("", "-wal", "-shm")
               if Path(path + suffix).is_file())


def enforce_limits(path: str, table: str, *, max_rows: int = 0,
                   max_db_bytes: int = 0, shrink: bool = False) -> dict[str, Any]:
    """إنفاذ سقوف المخزن.

    `shrink=False` (المسار الساخن — كل flush): حذف الأقدم + checkpoint سلبي
    فقط. الحذف وحده يوقف نموّ الملف (الصفحات المحرَّرة يعيد SQLite استعمالها)،
    أمّا `VACUUM` وسط سيل الكتابة فكان يقفل الكاتب دقائق («database is
    locked») ويخسر السباق، و`TRUNCATE` checkpoint يفشل ما دام للوحة قارئ
    دائم — فانفلت الـWAL بالجيغابايتات (مقيس 2026-08-19: ~9GB/ساعة).
    `shrink=True` (الدورة اليومية الهادئة فقط): يضيف قصّ الـWAL وVACUUM
    لإرجاع المساحة فعليًّا للقرص.
    """
    if not _IDENT.fullmatch(table):
        raise ValueError("INVALID_STORAGE_TABLE")
    if not Path(path).is_file():
        return {"breached": False, "pruned": 0, "rows": 0, "db_bytes": 0}
    connection = sqlite3.connect(path, timeout=_DB_TIMEOUT_S)
    connection.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
    # حدّ حجم ملف الـWAL عند اكتمال أي checkpoint — بلا رقم جديد: نفس سقف
    # المخزن المصرَّح به هو الحدّ.
    if max_db_bytes > 0:
        connection.execute("PRAGMA journal_size_limit=%d" % max_db_bytes)
    pruned = 0
    breached = False
    try:
        rows = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        if max_rows > 0 and rows > max_rows:
            remove = rows - max_rows
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE id IN "
                f"(SELECT id FROM {table} ORDER BY id LIMIT ?)", (remove,))
            deleted = cursor.rowcount or 0
            pruned += deleted
            connection.commit()
            rows -= deleted
            breached = True
        def _used_bytes() -> int:
            # بلا VACUUM حجم الملف لا ينكمش أبدًا — القياس الصادق للمسار الساخن
            # هو المحتوى الفعلي: (الصفحات الكلية − الحرّة) × حجم الصفحة، وإلا
            # ظلّت الحلقة تحذف حتى تفرغ الجدول وحجم الملف ثابت.
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
            freelist = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
            return max(0, page_count - freelist) * page_size

        size = database_bytes(path) if shrink else _used_bytes()
        rounds = 0
        while max_db_bytes > 0 and size > max_db_bytes and rows > 0 and rounds < 20:
            remove = max(1, rows // 5)
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE id IN "
                f"(SELECT id FROM {table} ORDER BY id LIMIT ?)", (remove,))
            deleted = cursor.rowcount or 0
            connection.commit()
            if deleted <= 0:
                break
            pruned += deleted
            rows -= deleted
            breached = True
            if shrink:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("VACUUM")
                size = database_bytes(path)
            else:
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
                size = _used_bytes()
            rounds += 1
        if shrink and breached:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {"breached": breached or (max_db_bytes > 0 and size > max_db_bytes),
                "pruned": pruned, "rows": rows, "db_bytes": size,
                "max_rows": max_rows, "max_db_bytes": max_db_bytes}
    finally:
        connection.close()
