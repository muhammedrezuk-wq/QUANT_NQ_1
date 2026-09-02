# -*- coding: utf-8 -*-
"""Calibration Brain (870) -- the learning/calibration manager the owner asked
for by his words (2026-08-26: "had badde yah"), under his AI constitution v2.1.

The three owner switches are the EXISTING ones -- nothing new to hold:
    WATCH     : always on -- this atom always observes, journals, recommends.
    CALIBRATE : the adaptation switch (860/901 adaptation_switch, owner button).
    EXECUTE   : the trading gate (552/575) -- untouched, unrelated, unread.

The full cycle (owner's paper): problem -> hypothesis -> change -> test ->
result -> compare -> accept/reject -> COMPLETE JOURNAL. In v1 the one
apply-able calibration is REBASELINE of the drift engine (840) for a drifted
section -- measurement layer only, reversible, zero trading surface:

    840 proposes (drift persists) -> 850 gates (shadow authority) ->
    870 EVALUATES over a window -> verdict:
        drift cleared            -> NO_CHANGE_NEEDED (journal, close)
        persists + WATCH mode    -> WAITING_PERMISSION (owner card)
        persists + CALIBRATE on  -> APPLY rebaseline -> VERIFY window ->
                                    VERIFIED_OK or auto ROLLED_BACK (SS23/SS45)

Forbidden forever (owner's tree): Risk Dial, R_B, account limits, safety,
governance, the owner keys -- this atom holds no path to any of them.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.1"

EVENT_EXPERIMENT = "experiment.state"
EVENT_DRIFT = "drift.vector.state"
EVENT_DIAGNOSIS = "system.diagnosis.state"
EVENT_KILL = "adaptation.kill_switch.state"
EVENT_TIME = "SYS_SECOND"
EVENT_APPLY = "recalibration.applied"
EVENT_OUT = "ai.brain.state"

MODE_WATCH = "WATCH"
MODE_CALIBRATE = "CALIBRATE"

ST_EVALUATING = "EVALUATING"
ST_NO_CHANGE = "NO_CHANGE_NEEDED"
ST_WAITING = "WAITING_PERMISSION"
ST_APPLIED = "APPLIED"
ST_VERIFYING = "VERIFYING"
ST_VERIFIED = "VERIFIED_OK"
ST_ROLLED_BACK = "ROLLED_BACK"

ACTION_REBASELINE = "REBASELINE"

_DEFAULT_EVAL_S = 600
_DEFAULT_VERIFY_S = 300
_DEFAULT_DB = "var/store/ai_journal_870.db"
_PUBLISH_EVERY_S = 5
_RECENT_ROWS = 12

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    target       TEXT NOT NULL,
    reason       TEXT,
    status       TEXT NOT NULL,
    action       TEXT,
    mode         TEXT,
    opened_at    REAL NOT NULL,
    decided_at   REAL,
    baseline_json TEXT,
    result_json  TEXT,
    note         TEXT
)
"""


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._db_path = _DEFAULT_DB
        self._eval_s = _DEFAULT_EVAL_S
        self._verify_s = _DEFAULT_VERIFY_S
        self._adaptation_off = True
        self._kill_reason = ""
        self._diagnosis: dict[str, Any] | None = None
        #: آخر انحراف لكل قسم: {section: {overall, threshold, baseline}}
        self._drift: dict[str, dict[str, Any]] = {}
        #: التجارب المفتوحة بالذاكرة: journal_id -> row
        self._open: dict[int, dict[str, Any]] = {}
        self._dirty = False
        self._since_publish = 0
        self._seen = 0
        # Inputs outside this atom's shadow-approval contract stay visible:
        # unrelated experiment states are ignored deliberately; malformed
        # approvals are rejected.  Neither category may disappear silently.
        self._ignored_status = 0
        self._invalid = 0
        self._emitted = 0
        self._applied = 0
        self._rolled_back = 0
        self._store_error = ""

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._db_path = str(cfg.get("db_path") or _DEFAULT_DB)
        self._eval_s = max(5, int(cfg.get("eval_window_s") or _DEFAULT_EVAL_S))
        self._verify_s = max(5, int(cfg.get("verify_window_s") or _DEFAULT_VERIFY_S))
        self._ensure_store()
        context.subscribe(EVENT_EXPERIMENT, self._on_experiment)
        context.subscribe(EVENT_DRIFT, self._on_drift)
        context.subscribe(EVENT_DIAGNOSIS, self._on_diagnosis)
        context.subscribe(EVENT_KILL, self._on_kill)
        context.subscribe(EVENT_TIME, self._on_second)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # ------------------------------------------------------------- اليومية
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=3000")
        return connection

    def _ensure_store(self) -> None:
        try:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(_SCHEMA)
                connection.commit()
            self._store_error = ""
        except (sqlite3.Error, OSError) as exc:
            self._store_error = str(exc)

    def _journal_insert(self, row: dict[str, Any]) -> int | None:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO experiments(experiment_id,target,reason,status,"
                    "action,mode,opened_at,baseline_json,note) VALUES(?,?,?,?,?,?,?,?,?)",
                    (row["experiment_id"], row["target"], row.get("reason"),
                     row["status"], row.get("action"), row.get("mode"),
                     row["opened_at"], json.dumps(row.get("baseline"), ensure_ascii=False),
                     row.get("note")))
                connection.commit()
                return int(cursor.lastrowid)
        except (sqlite3.Error, OSError) as exc:
            self._store_error = str(exc)
            return None

    def _journal_update(self, journal_id: int, *, status: str,
                        result: Any = None, note: str = "") -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE experiments SET status=?, decided_at=?, result_json=?, "
                    "note=? WHERE id=?",
                    (status, time.time(), json.dumps(result, ensure_ascii=False),
                     note, journal_id))
                connection.commit()
        except (sqlite3.Error, OSError) as exc:
            self._store_error = str(exc)

    def _journal_recent(self) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT experiment_id,target,status,action,mode,reason,note,"
                    "opened_at,decided_at FROM experiments ORDER BY id DESC LIMIT ?",
                    (_RECENT_ROWS,)).fetchall()
            return [dict(row) for row in rows]
        except (sqlite3.Error, OSError) as exc:
            self._store_error = str(exc)
            return []

    # ------------------------------------------------------------- المداخل
    def _mode(self) -> str:
        return MODE_WATCH if self._adaptation_off else MODE_CALIBRATE

    async def _on_kill(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        self._adaptation_off = bool(payload.get("adaptation_off") is True
                                    or payload.get("active") is False)
        self._kill_reason = str(payload.get("reason") or "")
        self._dirty = True

    async def _on_diagnosis(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict):
            self._diagnosis = payload
            self._dirty = True

    async def _on_drift(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        section = str(payload.get("section") or "")
        if not section:
            return
        self._drift[section] = {
            "overall": _num(payload.get("overall_drift")),
            "threshold": _num(payload.get("threshold")),
            "baseline": payload.get("baseline") or {},
        }

    async def _on_experiment(self, payload: dict[str, Any]) -> None:
        """موافقة الحاكم 850 بسقف الظل تفتح تجربة تقييم — لا تطبيق هنا."""
        if not self._running:
            return
        if not isinstance(payload, dict):
            self._invalid += 1
            self._dirty = True
            return
        if str(payload.get("status")) != "APPROVED_FOR_SHADOW":
            self._ignored_status += 1
            self._dirty = True
            return
        experiment_id = str(payload.get("experiment_id") or "")
        target = str(payload.get("target") or "")
        if not experiment_id or not target:
            self._invalid += 1
            self._dirty = True
            return
        self._seen += 1
        drift = self._drift.get(target, {})
        row = {"experiment_id": experiment_id, "target": target,
               "reason": str(payload.get("reason") or ""),
               "status": ST_EVALUATING, "action": ACTION_REBASELINE,
               "mode": self._mode(), "opened_at": time.time(),
               "baseline": {"drift": drift.get("overall"),
                            "threshold": drift.get("threshold"),
                            "engine_baseline": drift.get("baseline")},
               "note": "فرضية: الانحراف ثابت لا عابر — يقيَّم %d ث" % self._eval_s}
        journal_id = self._journal_insert(row)
        if journal_id is None:
            return
        row["deadline"] = self._eval_s
        row["journal_id"] = journal_id
        self._open[journal_id] = row
        self._dirty = True

    # ------------------------------------------------------------- الدورة
    async def _on_second(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        for journal_id, row in list(self._open.items()):
            row["deadline"] -= 1
            if row["deadline"] > 0:
                continue
            await self._decide(journal_id, row)
        self._since_publish += 1
        if self._dirty and self._since_publish >= _PUBLISH_EVERY_S:
            self._since_publish = 0
            self._dirty = False
            await self._publish()

    async def _decide(self, journal_id: int, row: dict[str, Any]) -> None:
        target = row["target"]
        drift = self._drift.get(target, {})
        overall = drift.get("overall")
        threshold = drift.get("threshold")
        persists = (overall is not None and threshold is not None
                    and overall >= threshold)
        result = {"drift_now": overall, "threshold": threshold}
        if row["status"] == ST_EVALUATING:
            if not persists:
                self._journal_update(journal_id, status=ST_NO_CHANGE, result=result,
                                     note="الانحراف زال خلال نافذة التقييم — لا تغيير (القرار الصحيح أحيانًا: لا شيء)")
                self._open.pop(journal_id, None)
            elif self._mode() == MODE_WATCH:
                self._journal_update(journal_id, status=ST_WAITING, result=result,
                                     note="الانحراف ثابت — الإجراء المقترح: إعادة تأسيس خط أساس %s. بانتظار سماح المالك (زر المعايرة)" % target)
                row["status"] = ST_WAITING
                self._open.pop(journal_id, None)
            else:
                # CALIBRATE: التطبيق — إعادة تأسيس خط الأساس، مع حفظ القديم للرجوع.
                old_baseline = drift.get("baseline") or {}
                await self._context.publish(EVENT_APPLY, {
                    "target": target, "action": ACTION_REBASELINE,
                    "experiment_id": row["experiment_id"], "by": "870",
                    "old_baseline": old_baseline})
                self._applied += 1
                row["status"] = ST_VERIFYING
                row["pre_apply_drift"] = overall
                row["old_baseline"] = old_baseline
                row["deadline"] = self._verify_s
                self._journal_update(journal_id, status=ST_APPLIED, result=result,
                                     note="طُبّق: إعادة تأسيس %s (القديم محفوظ للرجوع) — تحقق %d ث" % (target, self._verify_s))
        elif row["status"] == ST_VERIFYING:
            pre = _num(row.get("pre_apply_drift"))
            worse = (overall is not None and pre is not None and overall >= pre)
            if persists and worse:
                # §23/§45: فشل التحقق ⇒ رجوع آلي بإعادة الخط القديم.
                await self._context.publish(EVENT_APPLY, {
                    "target": target, "action": ACTION_REBASELINE,
                    "experiment_id": row["experiment_id"], "by": "870",
                    "restore_baseline": row.get("old_baseline") or {}})
                self._rolled_back += 1
                self._journal_update(journal_id, status=ST_ROLLED_BACK, result=result,
                                     note="التحقق فشل (الانحراف لم يتحسن) — أُعيد الخط القديم")
            else:
                self._journal_update(journal_id, status=ST_VERIFIED, result=result,
                                     note="التحقق نجح — الانحراف بعد إعادة التأسيس: %s" % overall)
            self._open.pop(journal_id, None)
        self._dirty = True

    async def _publish(self) -> None:
        if self._context is None:
            return
        recent = self._journal_recent()
        diagnosis = self._diagnosis or {}
        self._emitted += 1
        await self._context.publish(EVENT_OUT, {
            "id": "ai_brain",
            "mode": self._mode(),
            "calibrate_reason_off": self._kill_reason if self._adaptation_off else "",
            "diagnosis": {"state": diagnosis.get("state"),
                          "primary_cause": diagnosis.get("primary_cause"),
                          "because": diagnosis.get("because")},
            "open_experiments": [
                {"experiment_id": r["experiment_id"], "target": r["target"],
                 "status": r["status"], "seconds_left": max(0, int(r["deadline"]))}
                for r in self._open.values()],
            "waiting_permission": [r for r in recent if r["status"] == ST_WAITING],
            "journal_recent": recent,
            "applied": self._applied, "rolled_back": self._rolled_back,
            "ignored_status": self._ignored_status, "invalid": self._invalid,
            "store_error": self._store_error})

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "seen": self._seen,
                "ignored_status": self._ignored_status, "invalid": self._invalid,
                "applied": self._applied, "rolled_back": self._rolled_back,
                "open": [{**{k: v for k, v in row.items() if k != "journal_id"},
                          "journal_id": journal_id}
                         for journal_id, row in self._open.items()]}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        self._seen = int(state.get("seen") or 0)
        self._ignored_status = int(state.get("ignored_status") or 0)
        self._invalid = int(state.get("invalid") or 0)
        self._applied = int(state.get("applied") or 0)
        self._rolled_back = int(state.get("rolled_back") or 0)
        self._open = {}
        for row in state.get("open") or []:
            if isinstance(row, dict) and row.get("journal_id") is not None:
                self._open[int(row["journal_id"])] = dict(row)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"mode": self._mode(), "open": len(self._open),
                   "seen": self._seen, "ignored_status": self._ignored_status,
                   "invalid": self._invalid, "applied": self._applied,
                   "rolled_back": self._rolled_back,
                   "store_error": self._store_error}
        if self._store_error:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=self._store_error, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="mode=%s open=%d applied=%d" % (
                self._mode(), len(self._open), self._applied),
            details=details)
