from __future__ import annotations

import asyncio
import os
import sqlite3
import re
from typing import Any

import clock
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

# ٢٠٢٦-٠٨-٣١ (ختم NQ) — تصحيح ملكيّة المرجع الزمنيّ، لا ترقيع شرط الطزاجة.
#
# كان «الآن» يُؤخذ من حمولة نبضة `SYS_SECOND` المخزَّنة في `_official_time`،
# أي أنّ **صحّة الساعة صارت تابعة لجدولة استهلاك صندوق البريد**. والقياس:
#   • 806 ينتج النبضة 60/60 ثانية (1.000/ث) و`missed_intervals=0`.
#   • الناقل: `dropped=0` · `timeout=0` · `delivered=3714` للنبضة.
#   • ومع ذلك يصل الطابع متأخّرًا **تأخّرًا تراكميًّا**: 3.97ث عند الدقيقة
#     الثانية · 60.56ث عند الثانية عشرة · 97.87ث عند السادسة عشرة (≈0.1–0.15 ث/ث).
# فيخرج `age_s = official − updated_at` **سالبًا** على صفٍّ عمره ثانيتان،
# فيسقط شرط `0 <= age <= max` وتُعلَن بيانات طازجة «قديمة».
#
# الجذر معماريّ: مرجع زمنيّ مركزيّ لا يجوز أن يعتمد على وصول حدث عبر طابور
# مستهلك. `clock` سلطة زمنيّة مباشرة خارج النواة (يكتب فيها 806/608/003
# ويقرؤها سبعَ عشرةَ ذرّةً أصلًا: 513 · 516 · 552 · 609 · 618 …) — بلا طابور
# ولا جدولة. فصارت القراءة منها مباشرة.
# **شرط الطزاجة لم يُمسّ حرفًا**، والعمر السالب يبقى غير صالح كما كان.
# و`SYS_SECOND` بقيت مشتركًا بها — لكن **إشارة مراقبة فقط**: تُعلَن مسافة
# تأخّرها في تفاصيل الصحّة (`pulse_lag_s`) فينكشف اختناق الناقل بدل أن
# يتنكّر في هيئة بيانات قديمة.

ATOM_VERSION = "3.1.0"

EVENT_PULSE = "SYS_SECOND"
DEFAULT_MAX_AGE_S = 300.0

EVENT_OUT = "platform.account.state"
EVENT_TERMINAL = "platform.terminal_state"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_FILE = "BRIDGE_FILE_MISSING"
REASON_NO_TABLE = "ACCOUNT_TABLE_MISSING"
REASON_NEVER_READ = "NO_ACCOUNT_DATA_YET"

_COLUMNS = ("balance", "equity", "margin", "free_margin", "margin_level",
            "currency", "leverage", "open_count", "updated_at")
_PLATFORM_COLUMNS = ("connected", "trade_allowed", "expert_allowed", "bridge_beat")
_IDENTITY_COLUMNS = ("account_id", "broker", "account_server", "margin_mode")

_BUSY_TIMEOUT_MS = 3000
_CONNECT_TIMEOUT_S = 5.0
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_db(configured: str) -> str:
    override = os.environ.get("NQ_BRIDGE_DB", "").strip()
    return override or configured


def _bridge_connect(db_path: str) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=_CONNECT_TIMEOUT_S)
    connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return connection


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._db_path = ""
        self._table = "account_v2"
        self._poll_interval_s = 0.0
        self._last_state: dict[str, dict[str, Any]] = {}
        self._last_updated_at: dict[str, float | None] = {}
        # ٢٠٢٦-٠٨-٣١: يُعلَن البيات عند **تبدّله** لا عند كل دورة قراءة — وإلّا
        # صار صفٌّ بائت ثابت يُنشَر كل ثانية بلا جديد (عقد «انشر عند التغيّر»).
        self._last_stale: dict[str, bool] = {}
        self._official_time: float | None = None
        self._max_age_s = DEFAULT_MAX_AGE_S
        self._last_error = ""
        self.read_count = 0
        self.terminal_count = 0
        self.no_identity_count = 0
        self.publish_count = 0
        self.failure_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._db_path = _resolve_db(str(cfg["db_path"]))
        self._table = str(cfg["table_name"])
        if self._table != "account_v2" or not _IDENTIFIER.fullmatch(self._table):
            self._table = "account_v2"; self._last_error = "LEGACY_ACCOUNT_TABLE_FORBIDDEN"
        self._poll_interval_s = float(cfg["poll_interval_s"])
        self._max_age_s = float(cfg["max_age_s"])
        context.subscribe(EVENT_PULSE, self._on_pulse)

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        official = payload.get("official_time")
        if isinstance(official, (int, float)) and not isinstance(official, bool):
            self._official_time = float(official)

    async def start(self) -> None:
        if self._running or self._context is None:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self.failure_count = 0
        self._last_error = ""

    async def shutdown(self) -> None:
        await self.stop()

    def _read_row(self) -> list[dict[str, Any]]:
        columns = ", ".join(_COLUMNS + _PLATFORM_COLUMNS + _IDENTITY_COLUMNS)
        connection = _bridge_connect(self._db_path)
        try:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(
                f"SELECT {columns} FROM account_v2 "
                "WHERE account_id IS NOT NULL AND account_id<>'' ORDER BY account_id")]
        finally:
            connection.close()

    async def _read_once(self) -> None:
        if self._context is None: return
        try: raw = await asyncio.to_thread(self._read_row)
        except sqlite3.Error as exc:
            self.failure_count += 1; message=str(exc); self._last_error=(REASON_NO_TABLE if "no such table" in message.lower() else REASON_NO_FILE if "unable to open" in message.lower() else message); self._context.logger.warning("619 read failed: %s",message); return
        self._last_error=""
        rows = raw if isinstance(raw,list) else ([raw] if isinstance(raw,dict) else [])
        for row in rows:
            account_id=str(row.get("account_id") or "")
            if not account_id:
                self.no_identity_count+=1;continue
            self.read_count+=1;await self._publish_terminal(row)
            updated_at=_to_float(row.get("updated_at"));official=clock.now();age_s=(official-updated_at if updated_at is not None else None);stale=age_s is None or age_s<0 or age_s>self._max_age_s;changed=updated_at is None or updated_at!=self._last_updated_at.get(account_id)
            if not changed and stale==self._last_stale.get(account_id):continue
            state={"account_id":account_id,"balance":_to_float(row.get("balance")),"equity":_to_float(row.get("equity")),"margin":_to_float(row.get("margin")),"free_margin":_to_float(row.get("free_margin")),"margin_level":_to_float(row.get("margin_level")),"currency":row.get("currency"),"leverage":row.get("leverage"),"open_count":row.get("open_count"),"broker":row.get("broker"),"server":row.get("account_server"),"margin_mode":row.get("margin_mode"),"measured_at":updated_at,"age_s":age_s,"stale":stale,"changed":changed,"max_age_s":self._max_age_s}
            self._last_state[account_id]=state;self._last_updated_at[account_id]=updated_at;self._last_stale[account_id]=stale;self.publish_count+=1;await self._context.publish(EVENT_OUT,dict(state))

    async def _publish_terminal(self, row: dict[str, Any]) -> None:
        if self._context is None:
            return
        if not any(column in row for column in _PLATFORM_COLUMNS):
            return
        await self._context.publish(EVENT_TERMINAL, {
            "account_id": str(row.get("account_id")),
            "connected": bool(row.get("connected")),
            "trade_allowed": bool(row.get("trade_allowed")),
            "expert_allowed": bool(row.get("expert_allowed")),
            "bridge_beat": _to_float(row.get("bridge_beat")),
            "timestamp": _to_float(row.get("updated_at")),
        })
        self.terminal_count += 1

    async def _loop(self) -> None:
        try:
            while self._running:
                await self._read_once()
                await asyncio.sleep(self._poll_interval_s)
        except asyncio.CancelledError:
            pass

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {
            "reads": self.read_count,
            "published": self.publish_count,
            "failures": self.failure_count,
            "no_identity": self.no_identity_count,
            "last_error": self._last_error,
            "accounts": len(self._last_state),
            "stale_accounts": sorted(a for a,row in self._last_state.items() if row.get("stale")),
            # مراقبة فقط: كم تأخّرت آخر نبضة عن السلطة الزمنيّة. لا تدخل أي
            # حكم — وجودها يكشف اختناق الناقل بدل أن يظهر كبيانات قديمة.
            "pulse_lag_s": (None if self._official_time is None
                            else round(clock.now()-self._official_time, 3)),
        }
        if self._last_error:
            return HealthStatus(state=HealthState.DEGRADED, message=self._last_error, details=details)
        if not self._last_state:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NEVER_READ, details=details)
        if details["stale_accounts"]:
            return HealthStatus(state=HealthState.DEGRADED, message="ACCOUNT_STATE_STALE", details=details)
        if self._task is not None and self._task.done():
            return HealthStatus(state=HealthState.UNHEALTHY, message="ACCOUNT_READER_STOPPED", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message=f"published={self.publish_count}", details=details)
