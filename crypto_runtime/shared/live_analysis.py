"""عقد التحليل الحي المستقل المشترك للذرات 151–165.

لا يصنع هذا الملف قرار تداول. هو يربط كل محلل بالتكة الصالحة، يعزل حالته
بـ(الحساب، الأصل، المحلل)، ويطبّق العمق والعيار قبل إتاحة الوزن للدمج.
"""
from __future__ import annotations

import inspect
import json
import math
import os
import sqlite3
import threading
from collections import deque

from shared.analysis_speed import (limits_factor, speed_factor, speed_value,
                                   window as speed_window)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import clock
from shared.financial_scope import account_broker
from shared.cycle_identity import cycle_key

EVENT_TICK = "market.tick.validated"
EVENT_SETTINGS = "analysis.settings.command"
EVENT_SETTING_CHANGED = "analysis.setting.changed"
EVENT_SECOND = "SYS_SECOND"
# §٣٠ — ملكيّة الحساب: 619 هو المصدر الوحيد لاسم الوسيط.
EVENT_ACCOUNT = "platform.account.state"
MODE_LIVE = "live_tick"
#: وسم مسار الشموع — التوأم البطيء للنواة نفسها (ختم ٢٠٢٦-٠٨-٢١).
MODE_CANDLE = "candle"
#: حدث الشمعة المغلقة — مدخل المسار البطيء.
EVENT_CANDLE = "market_data.candle_closed"
STATE_ANALYZING = "ANALYZING"
STATE_NOT_READY = "NOT_READY"
STATE_READY = "DECISION_READY"
STATE_STALE = "STALE"
STATE_INVALID = "INVALID"
STATE_ERROR = "ERROR"

# أرضية الظاهرة (§٤): حركة نسبية أصغر من هذا القدر لا تُعدّ ظاهرة مهما
# كانت نادرة — وإلّا صار السوق الهادئ مولِّدًا آليًّا لقوّة عالية.
_MOVEMENT_FLOOR = 1e-6

# Owner ruling 2026-08-20 (NQ stamp, item 22): all fifteen analyzers start
# EQUAL inside each time camp (100/15 each, sum exactly 100 per path scope);
# the owner tunes individual shares from the dashboard afterwards. The old
# non-equal defaults (trend 15, momentum 10, ...) were code-invented, not
# owner-approved. Total available weight stays 100 with no implicit
# redistribution.
_EQUAL_ANALYZER_WEIGHT = 100.0 / 15.0
DEFAULT_WEIGHTS: dict[str, float] = {
    "trend": _EQUAL_ANALYZER_WEIGHT, "momentum": _EQUAL_ANALYZER_WEIGHT,
    "volatility": _EQUAL_ANALYZER_WEIGHT, "volume": _EQUAL_ANALYZER_WEIGHT,
    "spread": _EQUAL_ANALYZER_WEIGHT, "candle": _EQUAL_ANALYZER_WEIGHT,
    "gap": _EQUAL_ANALYZER_WEIGHT, "session": _EQUAL_ANALYZER_WEIGHT,
    "time": _EQUAL_ANALYZER_WEIGHT, "velocity": _EQUAL_ANALYZER_WEIGHT,
    "acceleration": _EQUAL_ANALYZER_WEIGHT, "volume_quality": _EQUAL_ANALYZER_WEIGHT,
    "noise": _EQUAL_ANALYZER_WEIGHT, "correlation": _EQUAL_ANALYZER_WEIGHT,
    "relative_strength": _EQUAL_ANALYZER_WEIGHT,
}
# §١٢ — الأقسام تُعاير كما تُعاير المحلّلات: `account+broker+symbol+section`.
# نطاقها مسموح في مخزن الإعدادات نفسه — مصدر معايرة واحد لا اثنان (§٥٢).
SECTION_IDS: frozenset[str] = frozenset({"150", "200", "250", "300", "350", "400"})
# Owner ruling 2026-08-20 (NQ stamp, item 22, Q4): the six sections that enter
# the aggregation default to the EQUAL share 100/6 each -- weight 0.0 for a
# section was a code-invented gap, not an owner decision.
_EQUAL_SECTION_WEIGHT = 100.0 / 6.0
SECTION_DEFAULT_WEIGHTS: dict[str, float] = {
    "150": _EQUAL_SECTION_WEIGHT, "200": _EQUAL_SECTION_WEIGHT,
    "250": _EQUAL_SECTION_WEIGHT, "300": _EQUAL_SECTION_WEIGHT,
    "350": _EQUAL_SECTION_WEIGHT, "400": _EQUAL_SECTION_WEIGHT,
}
# NQ-22 Q1: calibration carries a time-camp dimension. The live tick kernel
# is the fast camp; the slow camp is calibrated through the same store.
PATH_FAST = "fast"
PATH_SLOW = "slow"
ANALYSIS_PATHS: tuple[str, str] = (PATH_FAST, PATH_SLOW)

# ختم المالك المباشر ٢٠٢٦-٠٨-٢١ («كلهم NQ»): ملحق اللوحات §١٢ عدّد عتبات
# الجاهزية، والمحرّك كان يملك اثنتين فقط (العمق والثقة) وما عداهما محفور في
# الكود لا يصله المالك. صارت ثلاث عتبات إضافية عيارات معايَرة كاملة لكل
# (حساب · وسيط · رمز · محلّل · مسار):
#   strength_threshold      حاجز قوّة **جديد** لم يكن له وجود. افتراضه 0.0
#                           أي «لا حجب» — فالسلوك القائم لا يتغيّر بحرف حتى
#                           يرفعه المالك بنفسه من اللوحة.
#   stale_after_s           كانت 5.0 محفورة في الصنف نفسه.
#   direction_neutral_band  المنطقة الميّتة، كانت 5.0 محفورة في `_analyze`.
# الافتراضات هنا = القيم النافذة اليوم حرفيًّا، فالهجرة بلا أثر سلوكيّ.
DIAL_DEFAULTS: dict[str, float] = {
    "required_depth": 60.0,
    "confidence_threshold": 60.0,
    "strength_threshold": 0.0,
    "stale_after_s": 5.0,
    "direction_neutral_band": 5.0,
}
#: العيارات القابلة للضبط من اللوحة — العتبات ثمّ الوزن (وزنه يُعاد توزيعه).
TUNABLE_SETTINGS: tuple[str, ...] = (*DIAL_DEFAULTS, "weight")

# معاملات مختلفة عمدًا كي لا تكون المحللات نسخًا متطابقة من نتيجة واحدة.
PROFILE: dict[str, tuple[float, float, float]] = {
    "trend": (1.00, 0.15, 1.00), "momentum": (1.20, 0.35, 1.05),
    "volatility": (0.55, -0.10, 1.20), "volume": (0.75, 0.05, 0.95),
    "spread": (0.50, -0.20, 1.10), "candle": (0.90, 0.20, 1.00),
    "gap": (1.10, -0.30, 1.15), "session": (0.65, 0.10, 0.90),
    "time": (0.60, -0.05, 0.85), "velocity": (1.35, 0.40, 1.10),
    "acceleration": (1.50, 0.65, 1.20), "volume_quality": (0.70, 0.00, 0.95),
    "noise": (0.45, -0.60, 1.25), "correlation": (0.80, 0.25, 1.00),
    "relative_strength": (1.05, 0.30, 1.00),
}


def _finite(value: Any, fallback: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if math.isfinite(result) else fallback


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _now() -> float:
    """الساعة الرسمية المشتركة؛ لا توجد ساعة جدارية خاصة بالمحلل."""
    return float(clock.now())


class AnalysisSettingsStore:
    """إعدادات دائمة وتدقيق إضافي لا يطمس السجل السابق."""

    _schema_lock = threading.Lock()

    # Single source for the settings DDL: the fresh-database branch and the
    # path migration (which must rebuild the table) create the same shape.
    _SETTINGS_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS analysis_settings (
            account_id TEXT NOT NULL,
            broker TEXT NOT NULL,
            symbol TEXT NOT NULL,
            analyzer_id TEXT NOT NULL,
            path TEXT NOT NULL DEFAULT 'fast' CHECK(path IN ('fast','slow')),
            required_depth REAL NOT NULL CHECK(required_depth BETWEEN 0 AND 100),
            confidence_threshold REAL NOT NULL CHECK(confidence_threshold BETWEEN 0 AND 100),
            strength_threshold REAL NOT NULL DEFAULT 0
                CHECK(strength_threshold BETWEEN 0 AND 100),
            stale_after_s REAL NOT NULL DEFAULT 5
                CHECK(stale_after_s BETWEEN 0 AND 100),
            direction_neutral_band REAL NOT NULL DEFAULT 5
                CHECK(direction_neutral_band BETWEEN 0 AND 100),
            weight REAL NOT NULL CHECK(weight BETWEEN 0 AND 100),
            revision INTEGER NOT NULL,
            updated_at REAL NOT NULL,
            updated_by TEXT NOT NULL,
            PRIMARY KEY(account_id, broker, symbol, analyzer_id, path)
        )"""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.environ.get("QUANT_ANALYSIS_SETTINGS_DB")
        # ⛔ عطل مقيس (بند ٤ بورقة ٩٩ — 2026-08-19): `parent` وحدها تحلّ إلى
        # `shared/` فكان الافتراضي يكتب في `shared/var/store/analysis_settings.db`
        # بينما خادم الحوكمة (وكل مخازن المشروع) على `var/store/` بجذر المشروع —
        # فالحفظ «ينجح» ويُكتب بملفّ ظلّ لا يقرؤه أحد، واللوحة تعرض الافتراضيّات
        # للأبد (أوامر 901 رقم 36-38 موثّقة مكتوبة بملفّ الظلّ). الجذر = parent.parent.
        _root = Path(__file__).resolve().parent.parent
        candidate = Path(configured) if configured else _root / "var" / "store" / "analysis_settings.db"
        self.path = candidate if candidate.is_absolute() else _root / candidate
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _ensure_schema(self) -> None:
        with self._schema_lock, self._connect() as conn:
            self._retire_scopeless_schema(conn)
            self._add_path_dimension(conn)
            self._add_threshold_dials(conn)
            conn.executescript(self._SETTINGS_TABLE_SQL + """;
                CREATE TABLE IF NOT EXISTS analysis_settings_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    analyzer_id TEXT NOT NULL,
                    path TEXT NOT NULL DEFAULT 'fast' CHECK(path IN ('fast','slow')),
                    old_json TEXT NOT NULL,
                    new_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    changed_at REAL NOT NULL,
                    changed_by TEXT NOT NULL,
                    command_id TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_settings_command
                ON analysis_settings_audit(command_id, account_id, broker, symbol, analyzer_id, path);
            """)

    #: أعمدة العتبات الثلاث الجديدة وقيمها الافتراضية = النافذة اليوم حرفيًّا.
    _DIAL_COLUMNS: tuple[tuple[str, float], ...] = (
        ("strength_threshold", 0.0),
        ("stale_after_s", 5.0),
        ("direction_neutral_band", 5.0),
    )

    @classmethod
    def _add_threshold_dials(cls, conn: sqlite3.Connection) -> None:
        """ختم المالك ٢٠٢٦-٠٨-٢١: العتبات الثلاث تنزل عمودًا عمودًا.

        هجرة آمنة بلا نسخ جدول: العمود له `DEFAULT` فيملأ كل صفّ قائم بقيمته
        النافذة اليوم — أي صفر أثر سلوكيّ على معايرة موجودة. والفحص يسبق
        الإضافة فتتحمّل الدالة إعادة التشغيل على قاعدة مهاجَرة أصلًا.
        """
        existing = {row[1] for row in conn.execute(
            "PRAGMA table_info(analysis_settings)")}
        if not existing:
            return
        for column, default in cls._DIAL_COLUMNS:
            if column in existing:
                continue
            conn.execute(
                f"ALTER TABLE analysis_settings ADD COLUMN {column} REAL "
                f"NOT NULL DEFAULT {default} "
                f"CHECK({column} BETWEEN 0 AND 100)")
        conn.commit()

    @classmethod
    def _add_path_dimension(cls, conn: sqlite3.Connection) -> None:
        """NQ-22 Q1 (2026-08-20): the unique key grows a `path` column
        ('fast'/'slow'); every pre-existing row IS the fast camp.

        Mechanical necessity, documented: SQLite cannot extend a composite
        PRIMARY KEY in place, so the settings table migrates through
        ALTER TABLE ... RENAME + copy(path='fast') + drop. The audit table
        has a surrogate PK, so a plain ALTER TABLE ADD COLUMN suffices; its
        unique command index is dropped here and recreated with `path` by
        `_ensure_schema`.
        """
        settings_cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(analysis_settings)")}
        if settings_cols and "path" not in settings_cols:
            conn.execute("ALTER TABLE analysis_settings RENAME TO analysis_settings_prepath")
            conn.execute(cls._SETTINGS_TABLE_SQL)
            conn.execute("""INSERT INTO analysis_settings(account_id,broker,symbol,
                analyzer_id,path,required_depth,confidence_threshold,weight,
                revision,updated_at,updated_by)
                SELECT account_id,broker,symbol,analyzer_id,'fast',required_depth,
                confidence_threshold,weight,revision,updated_at,updated_by
                FROM analysis_settings_prepath""")
            conn.execute("DROP TABLE analysis_settings_prepath")
        audit_cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(analysis_settings_audit)")}
        if audit_cols and "path" not in audit_cols:
            conn.execute("ALTER TABLE analysis_settings_audit ADD COLUMN "
                         "path TEXT NOT NULL DEFAULT 'fast' "
                         "CHECK(path IN ('fast','slow'))")
            conn.execute("DROP INDEX IF EXISTS uq_analysis_settings_command")
        conn.commit()

    @staticmethod
    def _retire_scopeless_schema(conn: sqlite3.Connection) -> None:
        """§٣٠ — المفتاح صار `account+broker+symbol+analyzer`.

        ⛔ لا تُحذف بيانات معايرة أبدًا: الجدول القديم (بلا `broker`) يُسقَط
           **فقط** إذا كان فارغًا. وإن كان فيه صفّ واحد، يُرفع عطل صريح
           بدل تخمين وسيطٍ لم يُرسله أحد — لا `DEFAULT_BROKER` (§٢).
        """
        for table in ("analysis_settings", "analysis_settings_audit"):
            existing = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()
            if existing is None:
                continue
            columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(%s)" % table)}
            if "broker" in columns:
                continue
            rows = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
            if rows:
                raise RuntimeError(
                    "ANALYSIS_SETTINGS_MIGRATION_REQUIRED: %s فيه %d صفًّا بلا "
                    "`broker`. الوسيط لا يُخمَّن — يُملأ بأمر المالك." % (table, rows))
            conn.execute("DROP TABLE %s" % table)

    @staticmethod
    def defaults(analyzer_id: str) -> dict[str, float | int | str]:
        # NQ-22 Q4: a section id defaults to the equal 100/6 section share;
        # an analyzer id keeps the equal 100/15 analyzer share.
        if analyzer_id in SECTION_IDS:
            weight = SECTION_DEFAULT_WEIGHTS[analyzer_id]
        else:
            weight = DEFAULT_WEIGHTS.get(analyzer_id, 0.0)
        return {**DIAL_DEFAULTS, "weight": weight, "revision": 0,
                "updated_at": 0.0, "updated_by": "system"}

    def get(self, account_id: str, broker: str, symbol: str,
            analyzer_id: str, path: str = PATH_FAST) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("""SELECT required_depth,confidence_threshold,
                strength_threshold,stale_after_s,direction_neutral_band,weight,
                revision,updated_at,updated_by FROM analysis_settings
                WHERE account_id=? AND broker=? AND symbol=? AND analyzer_id=?
                AND path=?""",
                (account_id, broker, symbol, analyzer_id, path)).fetchone()
        return dict(row) if row is not None else self.defaults(analyzer_id)

    def update(self, account_id: str, broker: str, symbol: str, analyzer_id: str,
               updates: dict[str, Any], *, changed_by: str, command_id: str,
               changed_at: float, path: str = PATH_FAST) -> tuple[dict[str, Any], bool]:
        allowed = set(TUNABLE_SETTINGS)
        if not updates or set(updates) - allowed:
            raise ValueError("INVALID_ANALYSIS_SETTING")
        clean: dict[str, float] = {}
        for key, value in updates.items():
            number = _finite(value)
            if number is None or not 0.0 <= number <= 100.0:
                raise ValueError("ANALYSIS_SETTING_OUT_OF_RANGE")
            clean[key] = round(number, 4)
        # §٣٠ — الوسيط جزء من نطاق المعايرة، لا حقل اختياريّ.
        # §١٢ — والأقسام تُعاير من المخزن نفسه، لا من مخزن ثانٍ موازٍ.
        if (not account_id or not broker or not symbol
                or (analyzer_id not in DEFAULT_WEIGHTS
                    and analyzer_id not in SECTION_IDS)):
            raise ValueError("INVALID_ANALYSIS_SCOPE")
        if path not in ANALYSIS_PATHS:
            raise ValueError("INVALID_ANALYSIS_PATH")
        if not changed_by or not command_id:
            raise ValueError("MISSING_ANALYSIS_AUDIT_IDENTITY")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute("""SELECT new_json FROM analysis_settings_audit
                WHERE command_id=? AND account_id=? AND broker=? AND symbol=?
                AND analyzer_id=? AND path=?""",
                (command_id, account_id, broker, symbol, analyzer_id, path)).fetchone()
            if duplicate is not None:
                conn.rollback()
                return json.loads(str(duplicate["new_json"])), False
            row = conn.execute("""SELECT required_depth,confidence_threshold,
                strength_threshold,stale_after_s,direction_neutral_band,weight,
                revision,updated_at,updated_by FROM analysis_settings
                WHERE account_id=? AND broker=? AND symbol=? AND analyzer_id=?
                AND path=?""",
                (account_id, broker, symbol, analyzer_id, path)).fetchone()
            old = dict(row) if row is not None else self.defaults(analyzer_id)
            new = dict(old)
            new.update(clean)
            new["revision"] = int(old["revision"]) + 1
            new["updated_at"] = changed_at
            new["updated_by"] = changed_by
            self._upsert_row(conn, account_id, broker, symbol, analyzer_id, path, new)
            self._audit_row(conn, account_id, broker, symbol, analyzer_id, path,
                            old, new, command_id)
            # NQ-22 Q2: a weight change for one of the fifteen analyzers is
            # paid for by the other fourteen inside the same camp, in the
            # same transaction, so the camp total stays exactly 100.
            if "weight" in clean and analyzer_id in DEFAULT_WEIGHTS:
                self._rebalance_peers(conn, account_id, broker, symbol, path,
                                      analyzer_id, float(old["weight"]),
                                      float(new["weight"]), changed_by=changed_by,
                                      command_id=command_id, changed_at=changed_at)
            conn.commit()
        return new, True

    def set_weights(self, account_id: str, broker: str, symbol: str,
                    weights: dict[str, Any], *, changed_by: str,
                    command_id: str, changed_at: float,
                    path: str = PATH_FAST) -> dict[str, float]:
        """كتابة جدول أوزان المعسكر كاملًا دفعةً واحدة — بلا إعادة توزيع.

        ⛔ **لماذا لزم هذا** (عطل مقيس ٢٠٢٦-٠٨-٢١): قاعدة المالك Q2 تشحن فرق
           وزن محلّل واحد على نظرائه الأربعة عشر — وهي صحيحة لتعديل عيار
           مفرد. لكنّ كتابة جدولٍ كامل عبرها تفسده: الأمر الثاني يزيح ما
           ثبّته الأوّل، والأصفار المقصودة تُملأ من جديد. (قياس: طُلب
           velocity=20 فاستقرّ 17.34، وسبعة أصفار صارت صفرًا واحدًا.)
           و«حفظ إعدادات الكل» باللوحة يمرّ بالطريق نفسه — فما فشل معي كان
           سيفشل مع المالك حرفيًّا.

        فهنا يُكتب الجدول **معًا**: الخمسة عشر إلزاميّون، ومجموعهم 100 شرطًا
        لا تعليقًا، ولا يُوقَظ محرّك التوزيع لأنّ لا فرق يُشحَن — الجدول كلّه
        هو الحقيقة الجديدة. وكل صفّ يُدقَّق باسم آمره ووقته كالمعتاد.
        """
        if path not in ANALYSIS_PATHS:
            raise ValueError("INVALID_ANALYSIS_PATH")
        if not account_id or not broker or not symbol:
            raise ValueError("INVALID_ANALYSIS_SCOPE")
        if not changed_by or not command_id:
            raise ValueError("MISSING_ANALYSIS_AUDIT_IDENTITY")
        if not isinstance(weights, dict) or set(weights) != set(DEFAULT_WEIGHTS):
            raise ValueError("INCOMPLETE_WEIGHT_TABLE")
        clean: dict[str, float] = {}
        for name, value in weights.items():
            number = _finite(value)
            if number is None or not 0.0 <= number <= 100.0:
                raise ValueError("ANALYSIS_SETTING_OUT_OF_RANGE")
            clean[name] = round(number, 4)
        if abs(sum(clean.values()) - 100.0) > 0.01:
            raise ValueError("WEIGHT_TABLE_NOT_100")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # كل صفّ يُدقَّق بمعرّف مركّب `<الأمر>:<المحلّل>`، فالبحث عن
            # التكرار يجب أن يستعمل الصيغة نفسها — وإلّا لم يجد شيئًا وسقط
            # الأمر المعاد على فهرس التفرّد بدل أن يُتجاهَل بهدوء.
            probe = f"{command_id}:{sorted(clean)[0]}"
            duplicate = conn.execute(
                """SELECT 1 FROM analysis_settings_audit WHERE command_id=?
                   AND account_id=? AND broker=? AND symbol=? AND path=?""",
                (probe, account_id, broker, symbol, path)).fetchone()
            if duplicate is not None:
                conn.rollback()
                return clean
            for name, value in clean.items():
                row = conn.execute("""SELECT required_depth,confidence_threshold,
                    strength_threshold,stale_after_s,direction_neutral_band,weight,
                    revision,updated_at,updated_by FROM analysis_settings
                    WHERE account_id=? AND broker=? AND symbol=? AND analyzer_id=?
                    AND path=?""",
                    (account_id, broker, symbol, name, path)).fetchone()
                old = dict(row) if row is not None else self.defaults(name)
                new = dict(old)
                new["weight"] = value
                new["revision"] = int(old["revision"]) + 1
                new["updated_at"] = changed_at
                new["updated_by"] = changed_by
                self._upsert_row(conn, account_id, broker, symbol, name, path, new)
                self._audit_row(conn, account_id, broker, symbol, name, path,
                                old, new, f"{command_id}:{name}")
            conn.commit()
        return clean

    @staticmethod
    def _upsert_row(conn: sqlite3.Connection, account_id: str, broker: str,
                    symbol: str, analyzer_id: str, path: str,
                    row: dict[str, Any]) -> None:
        conn.execute("""INSERT INTO analysis_settings(account_id,broker,symbol,
            analyzer_id,path,required_depth,confidence_threshold,
            strength_threshold,stale_after_s,direction_neutral_band,
            weight,revision,updated_at,updated_by)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(account_id,broker,symbol,analyzer_id,path) DO UPDATE SET
            required_depth=excluded.required_depth,
            confidence_threshold=excluded.confidence_threshold,
            strength_threshold=excluded.strength_threshold,
            stale_after_s=excluded.stale_after_s,
            direction_neutral_band=excluded.direction_neutral_band,
            weight=excluded.weight, revision=excluded.revision,
            updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
            (account_id, broker, symbol, analyzer_id, path, row["required_depth"],
             row["confidence_threshold"], row["strength_threshold"],
             row["stale_after_s"], row["direction_neutral_band"],
             row["weight"], row["revision"],
             row["updated_at"], row["updated_by"]))

    @staticmethod
    def _audit_row(conn: sqlite3.Connection, account_id: str, broker: str,
                   symbol: str, analyzer_id: str, path: str, old: dict[str, Any],
                   new: dict[str, Any], command_id: str) -> None:
        conn.execute("""INSERT INTO analysis_settings_audit(account_id,broker,symbol,
            analyzer_id,path,old_json,new_json,revision,changed_at,changed_by,command_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (account_id, broker, symbol, analyzer_id, path,
             json.dumps(old, ensure_ascii=False, sort_keys=True),
             json.dumps(new, ensure_ascii=False, sort_keys=True),
             new["revision"], new["updated_at"], new["updated_by"], command_id))

    def _rebalance_peers(self, conn: sqlite3.Connection, account_id: str,
                         broker: str, symbol: str, path: str, analyzer_id: str,
                         old_weight: float, new_weight: float, *, changed_by: str,
                         command_id: str, changed_at: float) -> None:
        """Owner ruling NQ-22 Q2 (2026-08-20), literal: the weight delta
        (new - old) of one analyzer is charged EQUALLY to the other fourteen
        of the same (account, broker, symbol, path) camp, so the fifteen
        always sum to 100. Owner example: 25/25/25/25 with the first raised
        to 40 becomes 40/20/20/20 (delta/(n-1) off each other one).

        Mechanical necessities, documented:
        - Clamp at zero: a peer cannot go below 0; its unpaid remainder is
          re-charged equally to the peers still above zero (iterative
          water-fill). Peers already at 0 are untouched and not audited.
        - A peer with no stored row is materialized from the equal default
          (100/15) before the charge, so the charge base is the real camp.
        - Peer values are stored at full float precision (no 4-dp rounding):
          per-row rounding would let repeated rebalances drift the camp
          total away from 100.0000.
        """
        delta = new_weight - old_weight
        if abs(delta) < 1e-9:
            return
        order = sorted(peer for peer in DEFAULT_WEIGHTS if peer != analyzer_id)
        marks = ",".join("?" for _ in order)
        stored: dict[str, dict[str, Any]] = {}
        for row in conn.execute(
                # كل الأعمدة لازمة: الصفّ المقروء هنا يُعاد كتابته بـ
                # `_upsert_row` الذي يطلبها كلّها — نقصُها انهيارٌ عند أوّل
                # تعديل وزن، لا عطلٌ صامت.
                "SELECT analyzer_id,required_depth,confidence_threshold,"
                "strength_threshold,stale_after_s,direction_neutral_band,weight,"
                "revision,updated_at,updated_by FROM analysis_settings "
                "WHERE account_id=? AND broker=? AND symbol=? AND path=? "
                "AND analyzer_id IN (%s)" % marks,
                (account_id, broker, symbol, path, *order)):
            item = dict(row)
            stored[str(item.pop("analyzer_id"))] = item
        olds = {peer: stored.get(peer) or self.defaults(peer) for peer in order}
        values = {peer: float(olds[peer]["weight"]) for peer in order}
        if delta > 0.0:
            remaining = delta
            active = [peer for peer in order if values[peer] > 0.0]
            while remaining > 1e-9 and active:
                share = remaining / len(active)
                exhausted = [peer for peer in active if values[peer] <= share]
                if not exhausted:
                    for peer in active:
                        values[peer] -= share
                    remaining = 0.0
                else:
                    # Clamp at zero, then re-spread the unpaid remainder
                    # equally over the peers still above zero.
                    for peer in exhausted:
                        remaining -= values[peer]
                        values[peer] = 0.0
                    active = [peer for peer in active if values[peer] > 0.0]
            if remaining > 1e-6:
                # Only reachable when the stored camp already summed below
                # 100 (legacy data): refuse loudly instead of silently
                # breaking the 100-total contract.
                raise ValueError("ANALYSIS_WEIGHT_POOL_EXHAUSTED")
        else:
            share = -delta / len(order)
            for peer in order:
                if values[peer] + share > 100.0 + 1e-9:
                    # Only reachable when the stored camp already summed
                    # above 100 (legacy data): the 0..100 row contract wins.
                    raise ValueError("ANALYSIS_WEIGHT_CEILING")
                values[peer] += share
        for peer in order:
            fresh_row = peer not in stored
            changed = abs(values[peer] - float(olds[peer]["weight"])) > 1e-12
            if not fresh_row and not changed:
                continue
            new_row = dict(olds[peer])
            new_row["weight"] = min(100.0, max(0.0, values[peer]))
            new_row["revision"] = int(olds[peer]["revision"]) + 1
            new_row["updated_at"] = changed_at
            new_row["updated_by"] = changed_by
            self._upsert_row(conn, account_id, broker, symbol, peer, path, new_row)
            self._audit_row(conn, account_id, broker, symbol, peer, path,
                            olds[peer], new_row, "%s:eq:%s" % (command_id, peer))


def _median(values: list[float]) -> float:
    """وسيط — يقاوم القفزة المنفردة، بخلاف المتوسّط."""
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


@dataclass
class LiveState:
    prices: deque[float] = field(default_factory=lambda: deque(maxlen=64))
    returns: deque[float] = field(default_factory=lambda: deque(maxlen=63))
    spreads: deque[float] = field(default_factory=lambda: deque(maxlen=64))
    volumes: deque[float] = field(default_factory=lambda: deque(maxlen=64))
    timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=64))
    # خطّ أساس القوّة: مقادير ظاهرة **هذا المحلّل** وحده (§٤). لكل محلّل
    # خطّه، لأنّ `_profile_movement` يعطي لكلٍّ مقداره الخاصّ بمجاله.
    movements: deque[float] = field(default_factory=lambda: deque(maxlen=48))
    sequence: int = 0
    last_payload: dict[str, Any] | None = None
    stale_published_for: int = -1
    last_published_wall: float = 0.0


class LiveAnalyzerKernel:
    def __init__(self, analyzer_id: str, event_out: str,
                 path: str = PATH_FAST) -> None:
        if analyzer_id not in DEFAULT_WEIGHTS:
            raise ValueError("UNKNOWN_ANALYZER")
        if path not in ANALYSIS_PATHS:
            raise ValueError("INVALID_ANALYSIS_PATH")
        self.analyzer_id = analyzer_id
        self.event_out = event_out
        # ختم المالك ٢٠٢٦-٠٨-٢١: النواة صارت **مسارية**. المعادلات نفسها تخدم
        # المسارين — التِكّة والشمعة — فيكون المحلّل نسختين من كائن واحد لا
        # منطقين متباعدين. والمعايرة تُقرأ من مسار هذه النسخة هي.
        self.path = path
        self.mode = MODE_LIVE if path == PATH_FAST else MODE_CANDLE
        self.context: Any = None
        self.running = False
        self.states: dict[tuple[str, str, str], LiveState] = {}
        self.settings = AnalysisSettingsStore()
        self.settings_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.invalid = self.errors = self.published = self.settings_changes = 0
        self.broker_by_account: dict[str, str] = {}
        self.restore_skipped = 0
        self.stale_after_s = 5.0

    async def initialize(self, context: Any) -> None:
        self.context = context
        if self.path == PATH_FAST:
            context.subscribe(EVENT_TICK, self.on_tick)
            context.subscribe(EVENT_SECOND, self.on_second)
        else:
            context.subscribe(EVENT_CANDLE, self.on_candle)
        context.subscribe(EVENT_SETTINGS, self.on_settings)
        context.subscribe(EVENT_ACCOUNT, self.on_account)

    async def on_candle(self, payload: dict[str, Any]) -> None:
        """التوأم الشمعيّ: الشمعة المغلقة تدخل بالمدخل نفسه.

        الإغلاق يقوم مقام السعر، ومدى الشمعة (أعلى−أدنى) يقوم مقام السبريد
        بوصفه مقياس الاحتكاك المتاح على هذا المسار، والحجم كما هو. لا معادلة
        جديدة ولا تفسير ثانٍ: `_analyze` نفسها هي التي تحكم، فيخرج المسار
        البطيء بالحقول الثمانية نفسها — رقمًا لا مفردة.
        """
        if not self.running or not isinstance(payload, dict) or self.context is None:
            return
        close = _finite(payload.get("close"))
        if close is None or close <= 0:
            return
        high = _finite(payload.get("high"), close) or close
        low = _finite(payload.get("low"), close) or close
        stamp = _finite(payload.get("period_start", payload.get("timestamp")))
        if stamp is None or stamp <= 0:
            stamp = _now()
        await self._ingest(payload, price=close,
                           friction=max(0.0, (high - low) / close),
                           volume=_finite(payload.get("volume"), 0.0) or 0.0,
                           source_ts=float(stamp),
                           reason="INVALID_CANDLE_EVIDENCE")

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def _scope(self, payload: dict[str, Any]) -> tuple[str, str, str] | None:
        """§٣٠ — نطاق الحالة الحيّة: حساب + وسيط + أصل.

        ⛔ **عطل مقيس على النظام الحيّ:** التِكّة الحقيقية
           (`market.tick.validated`) تحمل `account_id` و`provider`
           (`CTRADER`/`MT5`) — **ولا تحمل `broker` إطلاقًا**. واشتراطُ
           `broker` فيها رفض كلّ تِكّة حقيقية ⇒ صفر مخرَج.

        ⛔ و`provider` **ليس** `broker`: الأوّل مصدر التغذية، والثاني اسم
           الوسيط (`Raw Trading Ltd`) يصل من `619` على
           `platform.account.state`. مساواتهما تزوير معنى حقل (§٢٧).

        ✅ الوسيط يُحلّ من ملكيّة الحساب عبر `financial_scope.account_broker`
           — آليّة المشروع المعتمدة نفسها التي يستعملها `513` و`516`.
           وهذا ليس تخمينًا بالرمز الذي يمنعه §٢: هو ملكيّة معلنة من `619`.
           وحسابٌ لم يُعلَن وسيطه بعد ⇒ `None` ⇒ يُرفض ويُعلَن، ولا يُخمَّن.

        ⛔ **عطل مقيس ثانٍ (2026-08-18):** `619` مصدرها جسر MT5 وحده —
           حساب cTrader (مثل ١٠٠٩٦٨٣١) لا يظهر في `platform.account.state`
           أبدًا، فيبقى غائبًا عن `broker_by_account` حتى لو وصلت آلاف
           التِكّات الصحيحة تحمل وسيطها معها مباشرة. أمر إعداد لاحق (حفظ
           معايرة من اللوحة) لا يحمل `broker` في حمولته فيُرفض دومًا رغم
           أن الوسيط معروف فعليًّا. الإصلاح: كل نطاق يُحلّ بنجاح — سواء من
           حقل `broker` المباشر في الحمولة أو من `known` سلفًا — يُعاد
           حفظه هنا؛ فوسيط وصل مرّة حقيقيّة عبر تِكّة (لا تخمين) يبقى
           معروفًا لأوامر لاحقة بلا `broker` خاص بها.
        """
        owner = account_broker(payload, self.broker_by_account)
        if owner is None:
            return None
        self.broker_by_account[owner[0]] = owner[1]
        symbol = str(payload.get("symbol") or payload.get("asset") or "").strip().upper()
        return (owner[0], owner[1], symbol) if symbol else None

    async def on_account(self, payload: dict[str, Any]) -> None:
        """ملكيّة الحساب من `619` — المصدر الوحيد لاسم الوسيط."""
        if not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "").strip()
        broker = str(payload.get("broker") or "").strip()
        if account and broker:
            self.broker_by_account[account] = broker

    def _config(self, scope: tuple[str, str, str]) -> dict[str, Any]:
        cached = self.settings_cache.get(scope)
        if cached is None:
            # NQ-22 Q1: the live tick kernel IS the fast camp -- it reads its
            # calibration from path='fast' only.
            cached = self.settings.get(scope[0], scope[1], scope[2],
                                       self.analyzer_id, self.path)
            self.settings_cache[scope] = cached
        return cached

    async def on_tick(self, payload: dict[str, Any]) -> None:
        if not self.running or not isinstance(payload, dict) or self.context is None:
            return
        scope = self._scope(payload)
        if scope is None:
            self.invalid += 1
            return
        bid = _finite(payload.get("bid"))
        ask = _finite(payload.get("ask"))
        price = _finite(payload.get("price", payload.get("last")))
        if price is None and bid is not None and ask is not None:
            price = (bid + ask) / 2.0
        source_ts = _finite(payload.get("source_timestamp",
                            payload.get("timestamp", payload.get("ts"))))
        if price is None or price <= 0 or source_ts is None or source_ts <= 0:
            self.invalid += 1
            await self._publish_invalid(scope, source_ts, "INVALID_TICK_EVIDENCE")
            return
        if bid is not None and ask is not None and (bid <= 0 or ask < bid):
            self.invalid += 1
            await self._publish_invalid(scope, source_ts, "INVALID_BID_ASK")
            return
        spread = (ask - bid) / price if bid is not None and ask is not None else 0.0
        volume = _finite(payload.get("volume", payload.get("size")), 0.0) or 0.0
        await self._ingest(payload, price=price, friction=max(0.0, spread),
                           volume=volume, source_ts=source_ts,
                           reason="NON_MONOTONIC_TICK")

    async def _ingest(self, payload: dict[str, Any], *, price: float,
                      friction: float, volume: float, source_ts: float,
                      reason: str) -> None:
        """جسم الاستيعاب المشترك — المسارَان يدخلان منه، والمعادلة واحدة."""
        scope = self._scope(payload)
        if scope is None:
            self.invalid += 1
            return
        state = self.states.setdefault(scope, LiveState())
        if state.timestamps and source_ts <= state.timestamps[-1]:
            self.invalid += 1
            await self._publish_invalid(scope, source_ts, reason)
            return
        try:
            if state.prices:
                state.returns.append((price - state.prices[-1]) / state.prices[-1])
            state.prices.append(price)
            state.spreads.append(max(0.0, friction))
            state.volumes.append(max(0.0, volume))
            state.timestamps.append(source_ts)
            state.sequence += 1
            result = self._analyze(scope, state, source_ts)
            state.last_payload = result
            state.stale_published_for = -1
            state.last_published_wall = _now()
            await self.context.publish(self.event_out, result)
            self.published += 1
        except Exception as exc:  # عطل محلل واحد لا يوقف بقية المحللين.
            self.errors += 1
            await self._publish_invalid(scope, source_ts, f"ANALYZER_ERROR:{type(exc).__name__}", STATE_ERROR)

    def _profile_movement(self, state: LiveState, recent: list[float],
                          weighted_mean: float, acceleration: float,
                          factor: float = 1.0) -> float:
        """دليل اتجاه خاص بكل محلل؛ لا توجد نتيجة أمّ مشتركة تُنسخ للجميع."""
        if not recent:
            return 0.0
        analyzer = self.analyzer_id
        short = recent[-4:]
        older = recent[:-4] or recent
        short_mean = sum(short) / len(short)
        older_mean = sum(older) / len(older)
        mean_abs = sum(abs(value) for value in recent) / len(recent)
        if analyzer == "trend":
            return weighted_mean
        if analyzer == "momentum":
            return short_mean - older_mean
        if analyzer == "volatility":
            mean = sum(recent) / len(recent)
            variance = sum((value - mean) ** 2 for value in recent) / len(recent)
            return math.copysign(math.sqrt(variance), mean) if mean else 0.0
        if analyzer in {"volume", "volume_quality"}:
            volumes = list(state.volumes)[-len(recent):]
            total = sum(volumes)
            weighted = sum(change * volume for change, volume in zip(recent, volumes)) / total if total else weighted_mean
            if analyzer == "volume_quality" and volumes:
                average = total / len(volumes)
                dispersion = sum(abs(value - average) for value in volumes) / max(total, 1e-9)
                return weighted * max(0.0, 1.0 - dispersion)
            return weighted
        if analyzer == "spread":
            spreads = list(state.spreads)[-len(recent):]
            average_spread = sum(spreads) / len(spreads) if spreads else 0.0
            return weighted_mean / max(1.0, average_spread * 100_000.0)
        if analyzer == "candle":
            return sum(short)
        if analyzer == "gap":
            latest = recent[-1]
            return latest if abs(latest) > max(mean_abs * 1.8, 1e-9) else weighted_mean * 0.25
        if analyzer == "session":
            # نافذة الجلسة: أساس اليوم 32 · أرضية 8 (مفتاح السرعة يشتقها).
            window = list(state.returns)[-speed_window(32, factor, 8):]
            return sum(window) / len(window) if window else 0.0
        if analyzer == "time":
            return sum(recent[-3:]) / min(3, len(recent))
        if analyzer == "velocity":
            return recent[-1]
        if analyzer == "acceleration":
            return acceleration
        if analyzer == "noise":
            net = sum(recent)
            gross = sum(abs(value) for value in recent)
            return (net / gross) * mean_abs if gross else 0.0
        if analyzer == "correlation":
            if len(recent) < 3:
                return 0.0
            mean = sum(recent) / len(recent)
            centered = [value - mean for value in recent]
            variance = sum(value * value for value in centered)
            lag = sum(a * b for a, b in zip(centered, centered[1:]))
            autocorrelation = lag / variance if variance else 0.0
            return mean * autocorrelation
        if analyzer == "relative_strength":
            # النافذة الطويلة: أساس اليوم 24 · أرضية 6 (مفتاح السرعة يشتقها).
            long_window = list(state.returns)[-speed_window(24, factor, 6):]
            long_mean = sum(long_window) / len(long_window) if long_window else 0.0
            return short_mean - long_mean
        return weighted_mean

    def _analyze(self, scope: tuple[str, str, str], state: LiveState,
                 source_ts: float) -> dict[str, Any]:
        account_id, broker, symbol = scope
        config = self._config(scope)
        returns = list(state.returns)
        # مفتاح سرعة التحليل (عقد v1.0 + الملحق): نقطة التطابق 50 = اليوم
        # حرفيًّا (12/32/24/24). النافذة الرئيسة: أساس 12 · أرضية 4.
        speed = speed_value(account_id, symbol)
        factor = speed_factor(speed)
        recent = returns[-speed_window(12, factor, 4):]
        sensitivity, acceleration_mix, depth_factor = PROFILE[self.analyzer_id]
        weighted = 0.0
        divisor = 0.0
        for index, value in enumerate(recent, start=1):
            weighted += value * index
            divisor += index
        mean_return = weighted / divisor if divisor else 0.0
        acceleration = (recent[-1] - recent[-2]) if len(recent) > 1 else 0.0
        movement = self._profile_movement(state, recent, mean_return, acceleration, factor)
        if self.analyzer_id not in {"acceleration", "momentum", "relative_strength"}:
            movement += acceleration * acceleration_mix
        # المقياس محايد تجاه سعر الأصل ومحدود بالعقد -100..+100.
        score = _clip(movement * 250_000.0 * sensitivity, -100.0, 100.0)

        # قاسم كفاية العينة: أساس اليوم 24 · أرضية 6 — أسرع ⇒ يمتلئ الدليل أبكر.
        sample_evidence = _clip((len(returns) / float(speed_window(24, factor, 6))) * 100.0)
        absolute_movement = sum(abs(value) for value in recent)
        movement_evidence = _clip(absolute_movement * 160_000.0 * depth_factor)
        if recent:
            mean = sum(recent) / len(recent)
            variance = sum((value - mean) ** 2 for value in recent) / len(recent)
            noise = math.sqrt(variance)
            mean_absolute = sum(abs(value) for value in recent) / len(recent)
            noise_ratio = noise / max(mean_absolute, 1e-9)
        else:
            noise_ratio = 0.0
        stability_evidence = _clip(100.0 - noise_ratio * 60.0)
        average_spread = sum(state.spreads) / len(state.spreads) if state.spreads else 0.0
        spread_evidence = _clip(100.0 - average_spread * 200_000.0)

        # ═══ §٤ — القوّة: مستقلّة عن الاتجاه تمامًا ══════════════════════
        # strength = normalized(abnormality × integrity)
        #   abnormality: كم خرجت ظاهرة هذا المحلّل عن خطّ أساسه هو.
        #   integrity  : كم هي متّسقة وسليمة — لا قفزة واحدة ولا حركة مشوّهة.
        # ⛔ لا تدخل `score` ولا إشارتها في أيّ من الطرفين.
        magnitude = abs(movement)
        baseline = _median(list(state.movements))
        # أرضية مطلقة: حركة أصغر من عبور السبريد ليست ظاهرة. بدونها يصير
        # السوق الهادئ مولِّدًا آليًّا لقوّة عالية لمجرّد أنّ الحركة نادرة.
        floor = max(average_spread, _MOVEMENT_FLOOR)
        reference = max(baseline, floor)
        abnormality = _clip(magnitude / reference * 50.0) if reference > 0 else 0.0
        absolute_returns = [abs(value) for value in recent]
        gross = sum(absolute_returns)
        if gross > 0 and len(recent) > 1:
            # اتساق الاتجاه: صافي الحركة من إجماليها.
            coherence = _clip(abs(sum(recent)) / gross * 100.0)
            # ليست قفزة واحدة: نصيب أكبر تِكّة من الحركة كلّها.
            concentration = _clip((1.0 - max(absolute_returns) / gross) *
                                  (len(recent) / (len(recent) - 1.0)) * 100.0)
        else:
            coherence = concentration = 0.0
        # استمرار الظاهرة: كم تِكّة بقيت فوق خطّ الأساس، لا تِكّة واحدة.
        if state.movements and baseline > 0:
            above = sum(1 for value in state.movements if value >= baseline)
            persistence = _clip(above / len(state.movements) * 100.0)
        else:
            persistence = 0.0
        integrity = (coherence + concentration + persistence) / 3.0
        strength = _clip(abnormality * integrity / 100.0)
        state.movements.append(magnitude)
        if len(state.timestamps) > 2:
            gaps = [b - a for a, b in zip(state.timestamps, list(state.timestamps)[1:])]
            positive = [gap for gap in gaps if gap > 0]
            mean_gap = sum(positive) / len(positive) if positive else 0.0
            continuity_evidence = _clip(100.0 - max(positive, default=0.0) /
                                        max(mean_gap, 0.001) * 15.0)
        else:
            continuity_evidence = 0.0
        volume_evidence = 100.0 if any(value > 0 for value in state.volumes) else 45.0

        # ═══ §٦ — العمق: أدلّة **الكفاية** وحدها ═════════════════════════
        # «كم جمعتُ قبل أن أسمح لنفسي بالكلام». عدد التِكّات مكوّن تغطية
        # فقط، لا مؤقّت ولا شرط عدد ثابت. ⛔ لا يشارك الثقة أيّ مكوّن.
        current_depth = _clip(0.35 * sample_evidence + 0.30 * movement_evidence +
                              0.20 * continuity_evidence + 0.15 * volume_evidence)

        # ═══ §٥ · §١٠ — الثقة: نضج الأدلّة واتّساقها ════════════════════
        # ⛔ أُخرِج منها عطلان مقيسان:
        #    ١) `direction_clarity = |score|` كانت **٣٠٪** منها — أي أنّ
        #       الثقة كانت تابعة للاتجاه. §٥ يفرض استقلالها عنه.
        #    ٢) `sample_evidence` كانت **١٧٪** منها — وهي `data_completeness`
        #       بعينها. §١٠ يفرض فصلها: امتلاء النافذة ليس نضجًا.
        # ⛔ ولا تشترك مع `current_depth` في مكوّن واحد، ولا مع `strength`:
        #    الاتّساق هنا **زمنيّ** (هل يقول نصفا النافذة الشيء نفسه؟)،
        #    بينما اتّساق القوّة مقداريّ (صافٍ إلى إجماليّ).
        if len(recent) >= 4:
            half = len(recent) // 2
            early = sum(recent[:half])
            late = sum(recent[half:])
            spread_of_halves = abs(early) + abs(late)
            # نصفان متعاكسان ⇒ صفر. متطابقان ⇒ 100. دليلٌ يتقلّب ليس ناضجًا.
            agreement = (_clip(100.0 * (1.0 - abs(early - late) / spread_of_halves))
                         if spread_of_halves > 0 else 0.0)
        else:
            agreement = 0.0
        confidence = _clip(0.40 * stability_evidence + 0.30 * spread_evidence +
                           0.30 * agreement)
        # مفتاح الحدود (ورقة المفاتيح الأربعة ٢٦-٠٨): يضرب العتبات الثلاث معًا
        # بمعامل L/50 — عند 50 = ×1.0 فتبقى قيم المالك المخزنة كما هي حرفيًّا.
        limits_f = limits_factor(account_id, symbol)
        required = min(100.0, float(config["required_depth"]) * limits_f)
        threshold = min(100.0, float(config["confidence_threshold"]) * limits_f)
        # ختم المالك ٢٠٢٦-٠٨-٢١: العتبتان الجديدتان تُقرآن من المعايرة نفسها،
        # وافتراض حاجز القوّة صفر — أي لا يحجب شيئًا حتى يرفعه المالك.
        strength_bar = min(100.0, float(config.get(
            "strength_threshold", DIAL_DEFAULTS["strength_threshold"])) * limits_f)
        neutral_band = float(config.get("direction_neutral_band",
                                        DIAL_DEFAULTS["direction_neutral_band"]))
        if current_depth < required:
            state_name = STATE_ANALYZING
            reason = "DEPTH_NOT_REACHED"
        elif confidence < threshold:
            state_name = STATE_NOT_READY
            reason = "CONFIDENCE_BELOW_THRESHOLD"
        elif strength < strength_bar:
            # ترتيب الحواجز مقصود: العمق أوّلًا (هل جمعتُ كفاية؟) ثمّ الثقة
            # (هل دليلي ناضج؟) ثمّ القوّة (هل الظاهرة تستحقّ أصلًا؟).
            # والقوّة محصورة 0..100، فالحاجز الافتراضي 0.0 لا يتحقّق أبدًا.
            state_name = STATE_NOT_READY
            reason = "STRENGTH_BELOW_THRESHOLD"
        else:
            state_name = STATE_READY
            reason = "DEPTH_AND_CONFIDENCE_READY"
        signal = ("up" if score > neutral_band
                  else "down" if score < -neutral_band else "sideways")
        analysis_ts = _now()
        base_stale = float(config.get("stale_after_s", self.stale_after_s))
        stale_effective = round(max(0.5, min(base_stale * 5.0, base_stale * factor)), 2)
        return {
            "account_id": account_id, "broker": broker,
            "symbol": symbol, "asset": symbol,
            "analysis_speed": round(speed, 2),
            "stale_after_effective_s": stale_effective,
            "limits_factor": round(limits_f, 3),
            "id": self.analyzer_id, "analyzer_id": self.analyzer_id,
            "analysis_mode": self.mode, "live_contract_version": 2,
            "cycle_id": cycle_key(account_id=account_id, broker=broker,
                                  symbol=symbol, timeframe="tick",
                                  period_start=state.sequence),
            "timeframe": "tick",
            "signal": signal, "direction": round(score, 4), "score": round(score, 4),
            # §٤ — القوّة حقل مستقلّ، ومكوّناها معلنان للتشخيص.
            "strength": round(strength, 4),
            "abnormality": round(abnormality, 4), "integrity": round(integrity, 4),
            # §١٠ — اكتمال البيانات ليس ثقة؛ حقل منفصل معلَن.
            "data_completeness": round(sample_evidence, 4),
            "confidence": round(confidence, 4), "current_depth": round(current_depth, 4),
            "required_depth": required, "confidence_threshold": threshold,
            "threshold": threshold, "weight": float(config["weight"]),
            "state": state_name, "analysis_state": state_name,
            "ready": state_name == STATE_READY,
            "status": "ok" if state_name == STATE_READY else "not_ready",
            "reason": reason, "quality": "good" if state_name == STATE_READY else "low",
            "source_timestamp": source_ts, "timestamp": analysis_ts,
            "sequence": state.sequence, "settings_revision": int(config["revision"]),
            "freshness_age_s": max(0.0, analysis_ts - source_ts),
            # الأدلّة معلنة ومقسومة: كلٌّ يخدم حقلًا واحدًا لا حقلين.
            "evidence": {"sample": round(sample_evidence, 3),
                         "movement": round(movement_evidence, 3),
                         "stability": round(stability_evidence, 3),
                         "spread": round(spread_evidence, 3),
                         "continuity": round(continuity_evidence, 3),
                         "volume": round(volume_evidence, 3),
                         "agreement": round(agreement, 3)},
            "evidence_map": {"depth": ["sample", "movement", "continuity", "volume"],
                             "confidence": ["stability", "spread", "agreement"],
                             "strength": ["abnormality", "integrity"]},
        }

    async def _publish_invalid(self, scope: tuple[str, str, str], source_ts: float | None,
                               reason: str, state_name: str = STATE_INVALID) -> None:
        if self.context is None:
            return
        account_id, broker, symbol = scope
        now = _now()
        config = self._config(scope)
        await self.context.publish(self.event_out, {
            "account_id": account_id, "broker": broker,
            "symbol": symbol, "asset": symbol,
            "id": self.analyzer_id, "analyzer_id": self.analyzer_id,
            "analysis_mode": self.mode, "live_contract_version": 2,
            "status": "invalid", "state": state_name, "analysis_state": state_name,
            "ready": False, "reason": reason, "source_timestamp": source_ts,
            "timestamp": now, "sequence": self.states.get(scope, LiveState()).sequence,
            "confidence": 0.0, "current_depth": 0.0,
            "required_depth": config["required_depth"],
            "confidence_threshold": config["confidence_threshold"],
            "weight": config["weight"],
        })

    async def on_second(self, payload: dict[str, Any]) -> None:
        if not self.running or self.context is None:
            return
        pulse_time = payload.get("official_time", payload.get("now")) if isinstance(payload, dict) else None
        now = _finite(pulse_time, _now()) or _now()
        for scope, state in list(self.states.items()):
            last = state.last_payload
            if not last or state.stale_published_for == state.sequence:
                continue
            # ختم المالك ٢٠٢٦-٠٨-٢١: مهلة الطزاجة صارت عيارًا لكل نطاق —
            # تُقرأ من معايرة هذا النطاق نفسه، وتسقط على 5.0 حين لا معايرة.
            base_stale = float(self._config(scope).get(
                "stale_after_s", self.stale_after_s))
            # عائلة الطزاجة تتبع مفتاح السرعة (ورقة v1.0 §14: أسرع ⇒ أفق أقصر).
            # المخزَّن = الأساس عند نقطة التطابق 50؛ الأرضية 0.5ث والسقف الأساس×5.
            account_id, _broker, symbol = scope
            stale_factor = speed_factor(speed_value(account_id, symbol))
            stale_after = max(0.5, min(base_stale * 5.0, base_stale * stale_factor))
            if now - state.last_published_wall <= stale_after:
                # Republish pacing (measured 2026-08-20): pipeline queueing delay
                # made source stamps look stale seconds after a FRESH publish, so
                # every pulse re-announced STALE for every scope right behind the
                # real result -- ~105 redundant events/s feeding the very delay
                # that triggered them. Stall is judged against the last actual
                # publish on the SAME official timebase as the pulse (one shared
                # clock, no private wall clock); genuinely quiet scopes still
                # announce once per TTL exactly as before.
                continue
            source_ts = _finite(last.get("source_timestamp"), 0.0) or 0.0
            if source_ts and now - source_ts > stale_after:
                stale = dict(last)
                stale.update({"state": STATE_STALE, "analysis_state": STATE_STALE,
                              "ready": False, "status": "stale", "reason": "SOURCE_TICK_STALE",
                              "timestamp": now, "freshness_age_s": now - source_ts,
                              "stale_after_effective_s": round(stale_after, 2)})
                state.last_payload = stale
                state.stale_published_for = state.sequence
                state.last_published_wall = now
                await self.context.publish(self.event_out, stale)
                self.published += 1

    async def on_settings(self, payload: dict[str, Any]) -> None:
        if not self.running or self.context is None or not isinstance(payload, dict):
            return
        path = str(payload.get("path") or PATH_FAST).strip().lower() or PATH_FAST
        # Owner stamp 2026-08-21 -- a whole-camp weight table written in one
        # command. The per-analyser rule (Q2) charges one analyser's delta to
        # its fourteen peers, which is right for a single dial and wrong for a
        # table: written one by one, each command shifts what the previous one
        # set. So the table arrives together, is validated to sum 100, and no
        # delta is charged to anyone. Every kernel in the camp drops its cache.
        table = payload.get("weights")
        if isinstance(table, dict) and table:
            scope = self._scope(payload)
            if scope is None or path != self.path:
                return
            command_id = str(payload.get("command_id") or "").strip()
            requested_at = str(payload.get("command_requested_at") or "").strip()
            origin = str(payload.get("origin") or "").strip()
            if requested_at:
                command_id = f"{origin or 'gateway'}:{command_id}:{requested_at}"
            try:
                self.settings.set_weights(
                    scope[0], scope[1], scope[2], table,
                    changed_by=str(payload.get("operator") or "").strip(),
                    command_id=command_id, changed_at=_now(), path=path)
            except ValueError:
                self.invalid += 1
                return
            self.settings_cache.pop(scope, None)
            self.settings_changes += 1
            return
        if str(payload.get("analyzer_id") or "") != self.analyzer_id:
            # NQ-22 Q2: a weight command for a PEER rebalances this analyzer's
            # stored weight too. Drop the cached scope so the next tick
            # re-reads the redistributed value after the peer commits.
            updates = payload.get("settings")
            if (path == self.path and isinstance(updates, dict)
                    and "weight" in updates):
                scope = self._scope(payload)
                if scope is not None:
                    self.settings_cache.pop(scope, None)
            return
        scope = self._scope(payload)
        if scope is None:
            self.invalid += 1
            return
        updates = payload.get("settings")
        if not isinstance(updates, dict):
            self.invalid += 1
            return
        command_id = str(payload.get("command_id") or "").strip()
        requested_at = str(payload.get("command_requested_at") or "").strip()
        origin = str(payload.get("origin") or "").strip()
        if requested_at:
            command_id = f"{origin or 'gateway'}:{command_id}:{requested_at}"
        try:
            new, changed = self.settings.update(
                scope[0], scope[1], scope[2], self.analyzer_id, updates,
                changed_by=str(payload.get("operator") or "").strip(),
                command_id=command_id,
                changed_at=_now(), path=path)
        except ValueError:
            self.invalid += 1
            return
        if path == self.path:
            # Each kernel caches its OWN camp. Before the candle twin existed
            # this read PATH_FAST literally, so a slow-camp command reached the
            # store and never reached the running analyser -- the calibration
            # was saved and ignored.
            self.settings_cache[scope] = dict(new)
        if changed:
            self.settings_changes += 1
        await self.context.publish(EVENT_SETTING_CHANGED, {
            "account_id": scope[0], "broker": scope[1], "symbol": scope[2],
            "analyzer_id": self.analyzer_id, "path": path,
            "settings": new, "changed": changed, "command_id": payload.get("command_id"),
            "timestamp": _now(),
        })

    def snapshot(self) -> dict[str, Any]:
        states: dict[str, Any] = {}
        for (account_id, broker, symbol), state in self.states.items():
            key = json.dumps([account_id, broker, symbol], ensure_ascii=False)
            states[key] = {
                "prices": list(state.prices), "returns": list(state.returns),
                "spreads": list(state.spreads), "volumes": list(state.volumes),
                "timestamps": list(state.timestamps),
                "movements": list(state.movements), "sequence": state.sequence,
                "last_payload": state.last_payload,
                "stale_published_for": state.stale_published_for,
            }
        # §٣٠ — نسخة ٢: النطاق ثلاثيّ `[account, broker, symbol]`.
        return {"contract": 2, "analyzer_id": self.analyzer_id, "states": states,
                "skipped_scopeless": self.restore_skipped}

    def restore(self, data: dict[str, Any]) -> None:
        """يستعيد لقطة نسخة ٢. لقطة نسخة ١ نطاقها ثنائيّ بلا وسيط.

        ⛔ لا تُخمَّن هوية وسيط غير محفوظة (§٢). نطاق قديم يُتخطّى
           **ويُعدّ** في `restore_skipped` — لا يسقط صامتًا.
        """
        if not isinstance(data, dict) or data.get("analyzer_id") != self.analyzer_id:
            return
        for raw_scope, item in (data.get("states") or {}).items():
            if not isinstance(item, dict):
                continue
            try:
                parsed = json.loads(raw_scope)
            except (TypeError, ValueError):
                self.restore_skipped += 1
                continue
            if not isinstance(parsed, list) or len(parsed) != 3:
                self.restore_skipped += 1
                continue
            account_id, broker, symbol = parsed
            state = LiveState()
            for name in ("prices", "returns", "spreads", "volumes", "timestamps",
                         "movements"):
                target = getattr(state, name)
                for value in item.get(name, []):
                    number = _finite(value)
                    if number is not None:
                        target.append(number)
            state.sequence = max(0, int(item.get("sequence", 0)))
            state.last_payload = item.get("last_payload") if isinstance(item.get("last_payload"), dict) else None
            state.stale_published_for = int(item.get("stale_published_for", -1))
            self.states[(str(account_id), str(broker), str(symbol))] = state

    def health_details(self) -> dict[str, Any]:
        return {"live_scopes": len(self.states), "live_published": self.published,
                "live_invalid": self.invalid, "live_errors": self.errors,
                "settings_changes": self.settings_changes,
                "contract": "tick_depth_threshold_weight_v1"}


#: الحقول التي يكتبها التوأم الشمعيّ فوق مخرَج الذرّة — الاتجاهية وحدها.
#: ⛔ `status` من ضمنها عمدًا (عطل مقيس ٢٠٢٦-٠٨-٢١): المجمّع 166 يقبل
#: الوحدة بشرط `status == "ok"` وحده. والتوأم كان يكتب الرقم والحالة ويترك
#: `status` لمنطق الذرّة القديم — فتخرج الحمولة بـ`score = -71.16` و
#: `status = "insufficient_data"` معًا، فيرميها المجمّع ويبقى المسار البطيء
#: بثلاثة مساهمين من خمسة عشر واتجاهه صفر. الرقم كان يُنشر ثمّ يُهدر.
_TWIN_FIELDS = ("signal", "score", "strength", "confidence",
                "current_depth", "required_depth", "ratio", "weight",
                "analysis_state", "ready", "state", "status")


class _TwinContext:
    """سياق التوأم: يلتقط نتيجته بدل نشرها، فلا يزدوج الحدث على السلك.

    التوأم لا يملك حدثًا خاصًّا به — نتيجته تُدمَج في نشرة الذرّة نفسها.
    فلو نشرها لصار لكل شمعة نشرتان بالمعرّف نفسه ولاختار المجمّع آخرَهما
    وصولًا — سباقٌ صامت. فالنشر يُحتجَز هنا ويُقرأ عند نشرة الذرّة.
    """

    def __init__(self, atom: Any, context: Any) -> None:
        self._atom = atom
        self._inner = context

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def subscribe(self, event: str, handler: Any) -> Any:
        return self._inner.subscribe(event, handler)

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        scope = (str(payload.get("account_id") or ""),
                 str(payload.get("broker") or ""),
                 str(payload.get("symbol") or "").upper())
        self._atom._candle_last[scope] = payload


class _EnrichingContext:
    """سياق الذرّة: نشرتها تخرج بالرقم الاتجاهيّ للتوأم، ومفردتها محفوظة."""

    def __init__(self, atom: Any, context: Any, event_out: str) -> None:
        self._atom = atom
        self._inner = context
        self._event_out = event_out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def subscribe(self, event: str, handler: Any) -> Any:
        return self._inner.subscribe(event, handler)

    def _twin_for(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """نتيجة التوأم لهذه الحمولة.

        ⛔ عطل مقيس ٢٠٢٦-٠٨-٢١: حمولة الذرّة تخرج من منطقها **بلا حساب ولا
           وسيط** — الهوية يضيفها غلاف القسم بعدها. فالمطابقة بالمفتاح
           الثلاثيّ كانت تفشل دائمًا والدمج لا يقع أبدًا (التوأم ينشر 30 مرّة
           والذرّة تخرج بمفردتها كما هي). فالمطابقة الآن: بالمفتاح الكامل إن
           وُجد، وإلّا **بالرمز وحده وبشرط أن يكون واحدًا لا غير** — والرمز
           موجود دائمًا. والالتباس يُترك بلا دمج، لا يُخمَّن.
        """
        store = getattr(self._atom, "_candle_last", None)
        if not store:
            return None
        symbol = str(payload.get("symbol") or "").upper()
        account = str(payload.get("account_id") or "")
        broker = str(payload.get("broker") or "")
        if account and broker:
            return store.get((account, broker, symbol))
        matches = [value for key, value in store.items() if key[2] == symbol]
        return matches[0] if len(matches) == 1 else None

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        if event != self._event_out or not isinstance(payload, dict):
            await self._inner.publish(event, payload)
            return
        twin = self._twin_for(payload)
        if twin is None:
            await self._inner.publish(event, payload)
            return
        merged = dict(payload)
        metadata = dict(merged.get("metadata") or {})
        # مفردة الذرّة تُحفَظ ولا تُمحى — هي معرفتها الخاصّة بالشمعة.
        metadata["pattern"] = payload.get("signal")
        metadata["pattern_score"] = payload.get("score")
        metadata["twin"] = MODE_CANDLE
        merged["metadata"] = metadata
        for field_name in _TWIN_FIELDS:
            if field_name in twin:
                merged[field_name] = twin[field_name]
        merged["analysis_mode"] = MODE_CANDLE
        if "unknown_fields" in twin:
            merged["unknown_fields"] = twin["unknown_fields"]
        if "unified" in twin:
            merged["unified"] = twin["unified"]
        await self._inner.publish(event, merged)


def live_analyzer(analyzer_id: str, event_out: str) -> Callable[[type], type]:
    """مزيّن قليل التدخل يحافظ على التحليل القديم ويضيف العقد الحي المستقل."""
    def decorate(cls: type) -> type:
        old_init = cls.__init__
        old_initialize = cls.initialize
        old_start = cls.start
        old_stop = cls.stop
        old_shutdown = cls.shutdown
        old_health = cls.health_check

        def new_init(self: Any, *args: Any, **kwargs: Any) -> None:
            old_init(self, *args, **kwargs)
            self._live_kernel = LiveAnalyzerKernel(analyzer_id, event_out)
            # ختم المالك ٢٠٢٦-٠٨-٢١ — التوأم الشمعيّ. المحلّل نسختان من كائن
            # واحد لا منطقان: نسخة تأكل التِكّة ونسخة تأكل الشمعة، وكلتاهما
            # تُخرجان **الحقول الثمانية نفسها**. قبل هذا كان مسار الشمعة عند
            # ثلاثة عشر محلّلًا ينطق مفردات لا أرقامًا (`engulfing` · `london` ·
            # `week_close`)، فلا يجد المجمّع صوتًا اتجاهيًّا واحدًا ويُخرج صفرًا
            # ويسمّيه «جاهزًا». التوأم لا يُلغي مفردة الذرّة: يبقيها في
            # `metadata.pattern` ويضع الرقم مكان الحقل الاتجاهيّ.
            self._candle_kernel = LiveAnalyzerKernel(analyzer_id, event_out,
                                                     path=PATH_SLOW)
            self._candle_last: dict[tuple[str, str, str], dict[str, Any]] = {}

        async def new_initialize(self: Any, context: Any) -> None:
            # التوأم يشترك **قبل** الذرّة بحدث الشمعة: فحين ينشر منطق الذرّة
            # نتيجته يكون رقم التوأم لهذه الشمعة جاهزًا بالفعل. ترتيبٌ محسوم،
            # لا سباق بين مشتركَين على الحدث نفسه.
            await self._candle_kernel.initialize(_TwinContext(self, context))
            await old_initialize(self, _EnrichingContext(self, context, event_out))
            await self._live_kernel.initialize(context)

        async def new_start(self: Any) -> None:
            await old_start(self)
            self._live_kernel.start()
            self._candle_kernel.start()

        async def new_stop(self: Any) -> None:
            self._live_kernel.stop()
            self._candle_kernel.stop()
            await old_stop(self)

        async def new_shutdown(self: Any) -> None:
            self._live_kernel.stop()
            self._candle_kernel.stop()
            await old_shutdown(self)

        # ختم المالك ٢٠٢٦-٠٨-٢١: الغلاف كان **يستبدل** snapshot/restore، فيقفل
        # الباب على حالة المسار البطيء (الشموع) مهما بنتها الذرّة. النتيجة
        # مقيسة: 151 تطلب 55 شمعة، والشمعة دقيقة — فكل إقلاع يعيدها إلى الصفر
        # ويصمت المسار البطيء 55 دقيقة كاملة. وورقة التنفيذ §٣٦ تنصّ: «لا فقدان
        # للبيانات». صار الغلاف **يسلسل**: حالته تحت `live_analysis`، وحالة
        # الذرّة نفسها تحت `atom` — وكلٌّ يستعيد ما يخصّه ولا يدهس الآخر.
        own_snapshot = cls.__dict__.get("snapshot")
        own_restore = cls.__dict__.get("restore")

        async def new_snapshot(self: Any) -> dict[str, Any]:
            # التوأم الشمعيّ يُحفَظ كما يُحفَظ التِكّيّ. بدون ذلك تبدأ نافذته
            # فارغة بعد كل إقلاع، وهي تُطعَم بشمعة كل دقيقة — أي **قرابة ربع
            # ساعة صمت** قبل أن يبلغ عمقه ويُقبل في الدمج، فيبقى المسار
            # البطيء بلا مساهم واحد ويقرأه المالك ميّتًا وهو يحسب.
            body: dict[str, Any] = {"live_analysis": self._live_kernel.snapshot(),
                                    "candle_analysis": self._candle_kernel.snapshot()}
            if own_snapshot is not None:
                inner = await own_snapshot(self)
                if inner is not None:
                    body["atom"] = inner
            return body

        async def new_restore(self: Any, state: dict[str, Any]) -> None:
            if not isinstance(state, dict):
                return
            self._live_kernel.restore(state.get("live_analysis") or {})
            self._candle_kernel.restore(state.get("candle_analysis") or {})
            if own_restore is not None and isinstance(state.get("atom"), dict):
                await own_restore(self, state["atom"])

        async def new_health(self: Any) -> Any:
            status = await old_health(self)
            try:
                details = dict(status.details or {})
                details.update(self._live_kernel.health_details())
                return type(status)(state=status.state, message=status.message, details=details)
            except Exception:
                return status

        cls.__init__ = new_init
        cls.initialize = new_initialize
        cls.start = new_start
        cls.stop = new_stop
        cls.shutdown = new_shutdown
        cls.snapshot = new_snapshot
        cls.restore = new_restore
        cls.health_check = new_health
        return cls
    return decorate
