"""سجلّ المُعامِلات — الأولوية ٠: لا رقم يحكم القرار بلا مصدر واعتماد.

المشكلة التي يحلّها:
    ستّة أرقام تحكم بوّابة `READY` اليوم وهي **اجتهاد المنفِّذ**، مكتوبة
    ثوابتَ في الكود. وسمُها «غير معتمدة» في ورقة لا يمنع أحدًا من البناء
    عليها بعد شهر — والورقة لا تُنفَّذ، الكود يُنفَّذ.

⇒ المُعامِل يصير صفًّا له `source` و`status`، والحاجز يقرأ الحالة آليًّا:

    status != APPROVED  ⇒  provisional  ⇒  لا يبلغ READY

⛔ هذا الملفّ **لا يعتمد رقمًا ولا يقترح قيمة**. يسجّل القيمة السارية
   في الكود اليوم مع `source=UNSET` و`status=UNAPPROVED` — أي يُعلن
   الجهل، لا يملؤه. الاعتماد قرار مالك يمرّ عبر `approve()` بتدقيق.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

SOURCE_UNSET = "UNSET"
SOURCE_OWNER = "OWNER"
SOURCE_BACKTEST = "BACKTEST"
SOURCE_VENDOR = "VENDOR"
ALL_SOURCES = frozenset({SOURCE_UNSET, SOURCE_OWNER, SOURCE_BACKTEST, SOURCE_VENDOR})

STATUS_APPROVED = "APPROVED"
STATUS_UNAPPROVED = "UNAPPROVED"
ALL_STATUSES = frozenset({STATUS_APPROVED, STATUS_UNAPPROVED})

SCOPE_GLOBAL = "global"
REASON_UNAPPROVED = "UNAPPROVED_PARAMETER"

#: المُعامِلات الستّة التي تحكم بوّابة `READY` اليوم — بقيمها السارية في
#: الكود، ومصدرها `UNSET` وحالتها `UNAPPROVED` حتّى يعتمدها المالك.
DECLARED: dict[str, dict[str, Any]] = {
    "MOVEMENT_FLOOR": {
        "value": 1e-6, "scope": SCOPE_GLOBAL,
        "where": "live_analysis._MOVEMENT_FLOOR",
        "governs": "strength.abnormality"},
    "ABNORMALITY_GAIN": {
        "value": 50.0, "scope": SCOPE_GLOBAL,
        "where": "live_analysis._analyze",
        "governs": "strength.abnormality"},
    "INTEGRITY_BLEND": {
        "value": 3.0, "scope": SCOPE_GLOBAL,
        "where": "live_analysis._analyze (coherence+concentration+persistence)/3",
        "governs": "strength.integrity"},
    "CONFIDENCE_BLEND": {
        "value": 0.40, "scope": SCOPE_GLOBAL,
        "where": "live_analysis + section_live (0.40/0.30/0.30)",
        "governs": "confidence"},
    "DEPTH_BLEND": {
        "value": 0.35, "scope": SCOPE_GLOBAL,
        "where": "live_analysis + section_live (0.35/0.30/0.20/0.15)",
        "governs": "current_depth"},
    "STALE_AFTER_S": {
        "value": 5.0, "scope": SCOPE_GLOBAL,
        "where": "section_contract.STALE_AFTER_S · live_analysis · section_live",
        "governs": "state.freshness"},
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS parameters (
    name           TEXT NOT NULL,
    scope          TEXT NOT NULL,
    value          REAL NOT NULL,
    source         TEXT NOT NULL,
    status         TEXT NOT NULL,
    version        INTEGER NOT NULL,
    effective_from REAL NOT NULL,
    approved_by    TEXT NOT NULL,
    approved_at    REAL NOT NULL,
    governs        TEXT NOT NULL,
    declared_at    TEXT NOT NULL,
    PRIMARY KEY(name, scope)
);
CREATE TABLE IF NOT EXISTS parameters_audit (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    scope      TEXT NOT NULL,
    old_json   TEXT NOT NULL,
    new_json   TEXT NOT NULL,
    version    INTEGER NOT NULL,
    changed_at REAL NOT NULL,
    changed_by TEXT NOT NULL,
    command_id TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_parameters_command
ON parameters_audit(command_id, name, scope);
"""


class ParameterRegistry:
    """مخزن المُعامِلات — نفس قاعدة المعايرة، جدول مستقلّ (§٥٢)."""

    _lock = threading.Lock()
    #: المسارات التي ضُمن مخطّطها في هذه العملية — الضمان يجري مرّة واحدة.
    #: (كان `_ensure_schema` يكتب `executescript` + 6 صفوف مع **كلّ** بناء
    #: نسخة، وذرّات القرار تبني نسخًا على المسار الساخن — كتابة قاعدة لكل
    #: دورة قرار. مقيس ٢٠٢٦-٠٨-٢٥.)
    _schema_ready: set[str] = set()

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.environ.get("QUANT_ANALYSIS_SETTINGS_DB")
        root = Path(__file__).resolve().parent.parent
        candidate = (Path(configured) if configured
                     else root / "var" / "store" / "analysis_settings.db")
        self.path = candidate if candidate.is_absolute() else root / candidate
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = str(self.path)
        if key not in ParameterRegistry._schema_ready:
            self._ensure_schema()
            ParameterRegistry._schema_ready.add(key)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)
            for name, spec in DECLARED.items():
                # ⛔ لا يُكتب إلّا الإعلان الأوّل: إعادة التشغيل لا تُرجع
                #    مُعامِلًا اعتمده المالك إلى `UNAPPROVED`.
                conn.execute(
                    """INSERT OR IGNORE INTO parameters(name,scope,value,source,
                       status,version,effective_from,approved_by,approved_at,
                       governs,declared_at)
                       VALUES(?,?,?,?,?,0,0.0,'','',?,?)""",
                    (name, spec["scope"], float(spec["value"]), SOURCE_UNSET,
                     STATUS_UNAPPROVED, spec["governs"], spec["where"]))
            conn.commit()

    def get(self, name: str, scope: str = SCOPE_GLOBAL) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM parameters WHERE name=? AND scope=?",
                (name, scope)).fetchone()
        return dict(row) if row is not None else None

    def all(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM parameters ORDER BY name, scope")]

    def unapproved(self) -> list[str]:
        with self._connect() as conn:
            return [str(row["name"]) for row in conn.execute(
                "SELECT name FROM parameters WHERE status!=? ORDER BY name",
                (STATUS_APPROVED,))]

    def approve(self, name: str, *, value: float, source: str, approved_by: str,
                command_id: str, approved_at: float,
                scope: str = SCOPE_GLOBAL) -> dict[str, Any]:
        """اعتماد مُعامِل — قرار مالك، لا اجتهاد كود.

        ⛔ `source=UNSET` مرفوض: رقمٌ بلا مصدر لا يُعتمد مهما كان صحيحًا.
        """
        if source not in ALL_SOURCES or source == SOURCE_UNSET:
            raise ValueError("PARAMETER_SOURCE_REQUIRED")
        if not approved_by or not command_id:
            raise ValueError("PARAMETER_APPROVAL_IDENTITY_REQUIRED")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError("PARAMETER_VALUE_INVALID") from None
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                """SELECT new_json FROM parameters_audit
                   WHERE command_id=? AND name=? AND scope=?""",
                (command_id, name, scope)).fetchone()
            if duplicate is not None:
                conn.rollback()
                return json.loads(str(duplicate["new_json"]))
            row = conn.execute(
                "SELECT * FROM parameters WHERE name=? AND scope=?",
                (name, scope)).fetchone()
            if row is None:
                conn.rollback()
                raise ValueError("PARAMETER_NOT_DECLARED")
            old = dict(row)
            new = dict(old)
            new.update({"value": number, "source": source,
                        "status": STATUS_APPROVED,
                        "version": int(old["version"]) + 1,
                        "effective_from": approved_at,
                        "approved_by": approved_by, "approved_at": approved_at})
            conn.execute(
                """UPDATE parameters SET value=?,source=?,status=?,version=?,
                   effective_from=?,approved_by=?,approved_at=?
                   WHERE name=? AND scope=?""",
                (new["value"], new["source"], new["status"], new["version"],
                 new["effective_from"], new["approved_by"], new["approved_at"],
                 name, scope))
            conn.execute(
                """INSERT INTO parameters_audit(name,scope,old_json,new_json,
                   version,changed_at,changed_by,command_id)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (name, scope,
                 json.dumps(old, ensure_ascii=False, sort_keys=True),
                 json.dumps(new, ensure_ascii=False, sort_keys=True),
                 new["version"], approved_at, approved_by, command_id))
            conn.commit()
        refresh_gate()
        return new


# ── الحاجز الميكانيكيّ ───────────────────────────────────────────────────
_gate_lock = threading.Lock()
_gate_cache: list[str] | None = None
#: بصمة الحداثة التي بُنيت عليها الذاكرة: (mtime القاعدة، لحظة آخر فحص).
#: ⛔ الذاكرة التي تُبذر مرّةً وتعيش عمر العملية كانت جذر «ما بتتحمل إلا
#:    على البارد» (مقيس ٢٠٢٦-٠٨-٢٥): اعتماد يُكتب من عمليّة الحوكمة لا
#:    يصل عمليّة النواة أبدًا. الآن تُفحص بصمة الملفّ كل `_GATE_RECHECK_S`
#:    على الأكثر — فتصل الاعتمادات عبر العمليّات خلال ثوانٍ بلا إقلاع بارد.
_gate_mtime: float = -1.0
_gate_path: str | None = None
_gate_checked_monotonic: float = -1.0
_GATE_RECHECK_S = 2.0


def _registry_mtime() -> float:
    try:
        return os.path.getmtime(ParameterRegistry().path)
    except OSError:
        return -1.0


def refresh_gate() -> None:
    """يُبطل الذاكرة المؤقّتة — يُستدعى بعد كلّ اعتماد."""
    global _gate_cache, _gate_path
    with _gate_lock:
        _gate_cache = None
        _gate_path = None
        _approved_cache.clear()


def unapproved_parameters() -> list[str]:
    """أسماء المُعامِلات غير المعتمدة — محفوظة وتُعاد قراءتها عند تغيّر القاعدة.

    ⛔ لا تُقرأ القاعدة في كلّ نشرة: `stamp_section` مسار ساخن — الفحص
       الدوريّ بصمةُ ملفّ (stat) لا استعلام، ومرّة كل ثانيتين كحدّ أقصى.
    """
    global _gate_cache, _gate_mtime, _gate_path, _gate_checked_monotonic
    import time as _time
    now = _time.monotonic()
    # م-27 (ورقة ٤١، أمر المالك 2026-08-28): مفتاح الحداثة صار (المسار، mtime)
    # لا mtime وحده — سجلّان مختلفان (فوركس/كريبتو) بختم ثانيةٍ واحد كانا
    # يقدّمان اعتمادات أحدهما للآخر عبر عملية واحدة تخدم السوقين.
    try:
        reg_path = str(ParameterRegistry().path)
    except Exception:  # noqa: BLE001
        reg_path = ""
    with _gate_lock:
        if _gate_cache is not None and _gate_path == reg_path:
            if now - _gate_checked_monotonic < _GATE_RECHECK_S:
                return list(_gate_cache)
    mtime = _registry_mtime()
    with _gate_lock:
        _gate_checked_monotonic = now
        if (_gate_cache is not None and _gate_path == reg_path
                and mtime == _gate_mtime):
            return list(_gate_cache)
    try:
        names = ParameterRegistry().unapproved()
    except sqlite3.Error:
        # تعذّرت القراءة ⇒ نفترض عدم الاعتماد. المجهول لا يفتح البوّابة.
        names = sorted(DECLARED)
    with _gate_lock:
        _gate_cache = list(names)
        _gate_mtime = mtime
        _gate_path = reg_path
    return list(names)


def approved_value(name: str, fallback: float,
                   scope: str = SCOPE_GLOBAL) -> float:
    """القيمة المعتمدة من المالك لمُعامِل مُعلَن — أو القيمة السارية بالكود.

    وُلدت لسدّ نمط «مفتاح بلا سلك» (سجلّ الختم، البند 47): مُعامِل يعتمده
    المالك بالسجلّ وصفر قارئ له بالكود. تُقرأ عبر نفس بصمة حداثة البوّابة
    (لا استعلام على المسار الساخن إلا عند تغيّر القاعدة فعلًا)."""
    global _approved_cache
    unapproved_parameters()  # يُحدّث بصمة الحداثة ويُبطل ما يلزم
    stamp = (_gate_path, _gate_mtime)  # م-27: الختم بالمسار أيضًا
    # م-38 (ورقة ٤١، أمر المالك «صلّح الباقي» 2026-08-28): الذاكرة كانت تُسقط
    # قيمة المانيفست من مفتاحها — أول مستدعيٍّ (ذرّة/اختبار بمانيفست 1) يسمّم
    # كل من بعده بقميته مهما اختلف مانيفسته (مقيس: 463 بcap=2 يعمل بـ1 بعد
    # أي اختبار سابق). القيمة المخزّنة صارت تحمل الـfallback وتقارن به.
    fallback_key = float(fallback)
    with _gate_lock:
        cached = _approved_cache.get((name, scope))
        if (cached is not None and cached[0] == stamp
                and cached[2] == fallback_key):
            return cached[1]
    try:
        row = ParameterRegistry().get(name, scope)
    except Exception:  # noqa: BLE001 — قاعدة مقفولة/غائبة لحظيًّا
        return float(fallback)
    value = (float(row["value"])
             if row is not None and str(row.get("status")) == STATUS_APPROVED
             else float(fallback))
    with _gate_lock:
        _approved_cache[(name, scope)] = (stamp, value, fallback_key)
    return value


_approved_cache: dict[tuple[str, str], tuple[float, float]] = {}


def readiness_blocked() -> bool:
    """هل يوجد مُعامِل غير معتمد يحكم بوّابة `READY`؟"""
    return bool(unapproved_parameters())
