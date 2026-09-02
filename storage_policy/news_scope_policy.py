"""طبقة سياسة نطاق الأخبار — News Scope Policy Layer

القرارات الحاكمة (2026-09-02):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

١. الهوية الموحدة للأصل:
   - NQ100 = instrument_id (الهوية الداخلية الوحيدة)
   - USTEC = broker_symbol (الاسم الخارجي فقط)
   - يمنع استخدام USTEC كهوية داخلية

٢. ربط الرموز:
   - Feed واحد يجوز له تغطية أكثر من instrument
   - mapping صريح: feed/source → external symbol → instrument_id

٣. سياسة الرافد:
   - السياسة على مستوى feed + instrument (وليس feed وحده)
   - رافد واحد بأكثر من رمز مسموح

٤. حذف السياسة:
   - لا يُسمح للرافد بحذف سياسته بنفسه
   - الحذف عملية حوكمة مملوكة لطبقة السياسة/الحاكم
   - RESOLVED → UNRESOLVED عند الحذف
   - يجب إبطال القرار الفعال المرتبط

٥. لا يُستخدم قرارٌ قديم بعد الحذف:
   - UNRESOLVED = لا قرار صالح للاعتماد التشغيلي

٦. تدقيق كامل لأي: create / update / delete / resolve / unresolved

٧. STATUS GUARD:
   - status إجباري (لا null، لا empty، لا whitespace)
   - enum/allowlist فقط (OK هو الاسم المعتمد)
   - RECIEVED وأي قيمة غير معرفة = مرفوضة
"""
from __future__ import annotations

import enum
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# ═══════════════════════════════════════════════════════════════════
# الهوية الموحدة للأصول — Instrument Identity
# ═══════════════════════════════════════════════════════════════════

# NQ100 هو instrument_id الداخلي الوحيد.
# USTEC هو broker_symbol الخارجي فقط.
# هذا الثابت يمنع خلطهما.
DEFAULT_INSTRUMENT_ID = "NQ100"
DEFAULT_BROKER_SYMBOL = "USTEC"

# خريطة aliases: broker_symbol → instrument_id
# تُحمَّل من config وتُثبّت هنا.
_DEFAULT_ALIASES: dict[str, str] = {
    "USTEC": DEFAULT_INSTRUMENT_ID,
    "NQ100": DEFAULT_INSTRUMENT_ID,
    "NAS100": DEFAULT_INSTRUMENT_ID,
    "US100": DEFAULT_INSTRUMENT_ID,
    "USTECH": DEFAULT_INSTRUMENT_ID,
}


def resolve_instrument_id(
    symbol: str,
    aliases: dict[str, str] | None = None,
) -> str | None:
    """حوّل أي broker_symbol إلى instrument_id داخلي.

    يعيد None إذا لم يُعرف الرمز.
    لا يُعيد broker_symbol أبداً — فقط instrument_id أو None.
    """
    if not symbol or not isinstance(symbol, str):
        return None
    table = aliases or _DEFAULT_ALIASES
    # محاولة مباشرة (case-insensitive)
    upper = symbol.strip().upper()
    result = table.get(upper)
    if result:
        return result
    # إذا الرمز نفسه هو instrument_id معروف
    if upper in {DEFAULT_INSTRUMENT_ID}:
        return DEFAULT_INSTRUMENT_ID
    return None


def broker_symbols_for(
    instrument_id: str,
    aliases: dict[str, str] | None = None,
) -> list[str]:
    """أعد كل broker_symbols التي تشير إلى هذا instrument_id."""
    table = aliases or _DEFAULT_ALIASES
    return sorted(k for k, v in table.items() if v == instrument_id)


# ═══════════════════════════════════════════════════════════════════
# STATUS GUARD — حارس الحالة
# ═══════════════════════════════════════════════════════════════════

class NewsStatus(str, enum.Enum):
    """الحالات المسموحة لحقل status في الأخبار.

    OK = المصدر يعمل ويُنشر (الاسم المعتمد حالياً).
    OFFLINE = المصدر مقفّل أو متوقف.
    ERROR = المصدر في حالة خطأ.
    """
    OK = "OK"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"

    @classmethod
    def is_valid(cls, value: Any) -> bool:
        """فحص صارم: لا null، لا empty، لا whitespace، لا قيم حرة."""
        if value is None:
            return False
        if not isinstance(value, str):
            return False
        if not value.strip():
            return False
        # حساس لحالة الأحرف — "ok" ≠ "OK"
        try:
            cls(value)
            return True
        except ValueError:
            return False

    @classmethod
    def validate_or_raise(cls, value: Any) -> "NewsStatus":
        """أعد NewsStatus أو ارمِ ValueError."""
        if not cls.is_valid(value):
            raise ValueError(
                "INVALID_STATUS: %r — المسموح: %s"
                % (value, [s.value for s in cls])
            )
        return cls(value)


# ═══════════════════════════════════════════════════════════════════
# Audit Trail — سجل التدقيق
# ═══════════════════════════════════════════════════════════════════

_AUDIT_ACTIONS = frozenset({
    "CREATE",       # سياسة جديدة
    "UPDATE",       # تعديل سياسة موجودة
    "DELETE",       # حذف سياسة (حوكمة)
    "RESOLVE",      # unresolved → resolved
    "UNRESOLVE",    # resolved → unresolved (عند الحذف)
})

# SQL schema
_SCHEMA = """
CREATE TABLE IF NOT EXISTS scope_policy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    source_status TEXT NOT NULL DEFAULT 'OK',
    broker_symbols TEXT NOT NULL DEFAULT '',
    resolved_at REAL,
    resolved_by TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_scope_policy_active
    ON scope_policy(feed_id, instrument_id) WHERE is_active = 1;

CREATE TABLE IF NOT EXISTS scope_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    feed_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    detail TEXT,
    actor TEXT NOT NULL DEFAULT 'system',
    at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS instrument_registry (
    instrument_id TEXT PRIMARY KEY,
    broker_symbols TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
"""


@dataclass
class ScopePolicy:
    """سياسة نطاق لـ feed + instrument."""
    feed_id: str
    instrument_id: str
    source_status: str = "OK"
    broker_symbols: list[str] = field(default_factory=list)
    is_active: bool = True
    resolved_at: float | None = None
    resolved_by: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "feed_id": self.feed_id,
            "instrument_id": self.instrument_id,
            "source_status": self.source_status,
            "broker_symbols": list(self.broker_symbols),
            "is_active": self.is_active,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class NewsScopePolicyStore:
    """مخزن سياسة النطاق — SQLite.

    الحذف هنا عملية حوكمة:
    - لا يُسمح للرFeed بحذف سياسته
    - الحذف = is_active → 0 + audit UNRESOLVE
    - القرار الفعال يُبطَل (resolved_at → None)
    """

    _BUSY_TIMEOUT_MS = 3000
    _CONNECT_TIMEOUT_S = 5.0

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=self._CONNECT_TIMEOUT_S)
        conn.execute("PRAGMA busy_timeout=%d" % self._BUSY_TIMEOUT_MS)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        """أنشئ الجداول إن لم تكن موجودة."""
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
            # سجل instrument الافتراضي
            existing = conn.execute(
                "SELECT instrument_id FROM instrument_registry WHERE instrument_id=?",
                (DEFAULT_INSTRUMENT_ID,)).fetchone()
            if existing is None:
                now = time.time()
                conn.execute(
                    "INSERT INTO instrument_registry (instrument_id, broker_symbols, created_at) "
                    "VALUES (?, ?, ?)",
                    (DEFAULT_INSTRUMENT_ID, "USTEC,NQ100,NAS100,US100,USTECH", now))
                conn.commit()
            self._initialized = True
        finally:
            conn.close()

    # ─── Create / Update ───────────────────────────────────────────

    def upsert_policy(
        self,
        feed_id: str,
        instrument_id: str,
        *,
        source_status: str = "OK",
        broker_symbols: list[str] | None = None,
        actor: str = "system",
    ) -> ScopePolicy:
        """أنشئ أو حدّث سياسة.

        - source_status يُفحَص صارماً (NewsStatus guard)
        - instrument_id يجب أن يكون identity داخلية (لا broker_symbol)
        """
        self._ensure_initialized()
        # Status guard
        NewsStatus.validate_or_raise(source_status)
        # instrument_id guard — لا broker_symbol
        if instrument_id != resolve_instrument_id(instrument_id):
            raise ValueError(
                "NOT_INSTRUMENT_ID: %r ليس هوية داخلية — استخدم instrument_id"
                % instrument_id)

        now = time.time()
        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT id, feed_id, instrument_id, source_status, broker_symbols, "
                "resolved_at, resolved_by, created_at, updated_at "
                "FROM scope_policy WHERE feed_id=? AND instrument_id=? AND is_active=1",
                (feed_id, instrument_id)).fetchone()

            symbols_str = ",".join(broker_symbols or [])
            if existing:
                # UPDATE
                conn.execute(
                    "UPDATE scope_policy SET source_status=?, broker_symbols=?, "
                    "updated_at=? WHERE id=?",
                    (source_status, symbols_str, now, existing["id"]))
                policy_id = existing["id"]
                action = "UPDATE"
                created_at = existing["created_at"]
            else:
                # CREATE
                cursor = conn.execute(
                    "INSERT INTO scope_policy "
                    "(feed_id, instrument_id, source_status, broker_symbols, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (feed_id, instrument_id, source_status, symbols_str, now, now))
                policy_id = cursor.lastrowid
                action = "CREATE"
                created_at = now

            conn.commit()
            self._audit(conn, action, feed_id, instrument_id,
                        {"source_status": source_status,
                         "broker_symbols": symbols_str},
                        actor, now)
            conn.commit()

            return ScopePolicy(
                feed_id=feed_id, instrument_id=instrument_id,
                source_status=source_status,
                broker_symbols=broker_symbols or [],
                resolved_at=existing["resolved_at"] if existing else None,
                resolved_by=existing["resolved_by"] if existing else None,
                created_at=created_at, updated_at=now)
        finally:
            conn.close()

    # ─── Resolve ───────────────────────────────────────────────────

    def resolve_policy(
        self,
        feed_id: str,
        instrument_id: str,
        *,
        resolved_by: str = "system",
        actor: str = "system",
    ) -> ScopePolicy | None:
        """حوّل سياسة من UNRESOLVED إلى RESOLVED.

        يعيد None إذا لم توجد سياسة فعّالة.
        """
        self._ensure_initialized()
        now = time.time()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, source_status, broker_symbols, created_at, updated_at "
                "FROM scope_policy WHERE feed_id=? AND instrument_id=? AND is_active=1",
                (feed_id, instrument_id)).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE scope_policy SET resolved_at=?, resolved_by=?, updated_at=? "
                "WHERE id=?",
                (now, resolved_by, now, row["id"]))
            conn.commit()
            self._audit(conn, "RESOLVE", feed_id, instrument_id,
                        {"resolved_by": resolved_by}, actor, now)
            conn.commit()
            symbols = [s for s in (row["broker_symbols"] or "").split(",") if s]
            return ScopePolicy(
                feed_id=feed_id, instrument_id=instrument_id,
                source_status=row["source_status"],
                broker_symbols=symbols,
                is_active=True,
                resolved_at=now, resolved_by=resolved_by,
                created_at=row["created_at"], updated_at=now)
        finally:
            conn.close()

    # ─── Delete (Governance) ───────────────────────────────────────

    def delete_policy(
        self,
        feed_id: str,
        instrument_id: str,
        *,
        actor: str = "governance",
    ) -> bool:
        """حذف سياسة — عملية حوكمة.

        ١. is_active → 0 (لا حذف فعليّ للصف)
        ٢. resolved_at → None (إبطال القرار الفعال)
        ٣. audit UNRESOLVE
        ٤. يعيد True إذا حُذفت سياسة فعّالة، False إذا لم توجد
        """
        self._ensure_initialized()
        now = time.time()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM scope_policy "
                "WHERE feed_id=? AND instrument_id=? AND is_active=1",
                (feed_id, instrument_id)).fetchone()
            if row is None:
                return False
            # إبطال القرار الفعال + تعطيل السياسة
            conn.execute(
                "UPDATE scope_policy SET is_active=0, resolved_at=NULL, "
                "resolved_by=NULL, updated_at=? WHERE id=?",
                (now, row["id"]))
            conn.commit()
            self._audit(conn, "DELETE", feed_id, instrument_id,
                        {"effective_decision_invalidated": True,
                         "transition": "RESOLVED→UNRESOLVED"},
                        actor, now)
            self._audit(conn, "UNRESOLVE", feed_id, instrument_id,
                        {"reason": "policy_deleted_by_governance"},
                        actor, now)
            conn.commit()
            return True
        finally:
            conn.close()

    # ─── Query ─────────────────────────────────────────────────────

    def get_policy(
        self, feed_id: str, instrument_id: str,
    ) -> ScopePolicy | None:
        """أعد السياسة الفعّالة أو None.

        None = UNRESOLVED (لا قرار صالح).
        """
        self._ensure_initialized()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT feed_id, instrument_id, source_status, broker_symbols, "
                "resolved_at, resolved_by, created_at, updated_at "
                "FROM scope_policy WHERE feed_id=? AND instrument_id=? AND is_active=1",
                (feed_id, instrument_id)).fetchone()
            if row is None:
                return None
            symbols = [s for s in (row["broker_symbols"] or "").split(",") if s]
            return ScopePolicy(
                feed_id=row["feed_id"], instrument_id=row["instrument_id"],
                source_status=row["source_status"],
                broker_symbols=symbols,
                is_active=True,
                resolved_at=row["resolved_at"],
                resolved_by=row["resolved_by"],
                created_at=row["created_at"], updated_at=row["updated_at"])
        finally:
            conn.close()

    def is_resolved(self, feed_id: str, instrument_id: str) -> bool:
        """هل يوجد قرار صالح؟ UNRESOLVED = False."""
        policy = self.get_policy(feed_id, instrument_id)
        return policy is not None

    def can_operate(self, feed_id: str, instrument_id: str) -> bool:
        """هل يجوز التشغيل؟

        شروط التشغيل:
        ١. سياسة فعّالة موجودة (is_active=1)
        ٢. source_status = OK
        ٣. policy ليست None (UNRESOLVED ممنوع)
        """
        policy = self.get_policy(feed_id, instrument_id)
        if policy is None:
            return False
        if policy.source_status != NewsStatus.OK.value:
            return False
        return True

    def policies_for_feed(self, feed_id: str) -> list[ScopePolicy]:
        """كل السياسات الفعّالة لرافد معيّن (قد يكون أكثر من واحدة)."""
        self._ensure_initialized()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT feed_id, instrument_id, source_status, broker_symbols, "
                "resolved_at, resolved_by, created_at, updated_at "
                "FROM scope_policy WHERE feed_id=? AND is_active=1",
                (feed_id,)).fetchall()
            result = []
            for row in rows:
                symbols = [s for s in (row["broker_symbols"] or "").split(",") if s]
                result.append(ScopePolicy(
                    feed_id=row["feed_id"], instrument_id=row["instrument_id"],
                    source_status=row["source_status"],
                    broker_symbols=symbols,
                    is_active=True,
                    resolved_at=row["resolved_at"],
                    resolved_by=row["resolved_by"],
                    created_at=row["created_at"], updated_at=row["updated_at"]))
            return result
        finally:
            conn.close()

    def all_active_policies(self) -> list[ScopePolicy]:
        """كل السياسات الفعّالة."""
        self._ensure_initialized()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT feed_id, instrument_id, source_status, broker_symbols, "
                "resolved_at, resolved_by, created_at, updated_at "
                "FROM scope_policy WHERE is_active=1 "
                "ORDER BY feed_id, instrument_id").fetchall()
            result = []
            for row in rows:
                symbols = [s for s in (row["broker_symbols"] or "").split(",") if s]
                result.append(ScopePolicy(
                    feed_id=row["feed_id"], instrument_id=row["instrument_id"],
                    source_status=row["source_status"],
                    broker_symbols=symbols,
                    is_active=True,
                    resolved_at=row["resolved_at"],
                    resolved_by=row["resolved_by"],
                    created_at=row["created_at"], updated_at=row["updated_at"]))
            return result
        finally:
            conn.close()

    # ─── Audit ─────────────────────────────────────────────────────

    def audit_trail(
        self,
        feed_id: str | None = None,
        instrument_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """سجل التدقيق — آخر `limit` حدثاً."""
        self._ensure_initialized()
        conn = self._connect()
        try:
            where_parts = []
            params: list[Any] = []
            if feed_id:
                where_parts.append("feed_id=?")
                params.append(feed_id)
            if instrument_id:
                where_parts.append("instrument_id=?")
                params.append(instrument_id)
            where = " WHERE " + " AND ".join(where_parts) if where_parts else ""
            params.append(limit)
            rows = conn.execute(
                "SELECT action, feed_id, instrument_id, detail, actor, at "
                "FROM scope_audit%s ORDER BY id DESC LIMIT ?" % where,
                params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _audit(
        self, conn: sqlite3.Connection, action: str,
        feed_id: str, instrument_id: str,
        detail: dict[str, Any] | None,
        actor: str, at: float,
    ) -> None:
        conn.execute(
            "INSERT INTO scope_audit (action, feed_id, instrument_id, detail, actor, at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (action, feed_id, instrument_id,
             json.dumps(detail or {}), actor, at))

    # ─── helpers ───────────────────────────────────────────────────

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()
