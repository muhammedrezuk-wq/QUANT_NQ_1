# -*- coding: utf-8 -*-
"""ورقة الذكاء (منظومة المراقبة الذاتية والتكيّف الآمن) — قبول الأطوار.

كل طور يُقاس على بوّاباته الخمس (وظيفيّ · سلامة بيانات · أداء · أمان · أثريّة)
بأحداث حقيقيّة على ناقل النواة — والقياس الحتميّ للمحاكاة (بناء ٢) هو أرضيّة
عدم التراجع (§31): نفس المقطع مرّتين مع الطبقة التكيّفيّة كاملة = نفس القرارات.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from core.contracts.atom import AtomContext
from core.event_bus import EventBus

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)



class _Log:
    def __getattr__(self, _n: str):
        return lambda *a, **k: None


def load_atom(atom_id: int):
    folder = next((ATOM_ROOT).glob(f"{atom_id}_*/atom.py"))
    sys.path.insert(0, str(folder.parent))
    try:
        spec = importlib.util.spec_from_file_location(f"intel_atom_{atom_id}", folder)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class AdaptiveStack:
    """الناقل + ذرّات الورقة الستّ (810→860) + سلسلة قرار حقيقيّة (150·166·451)."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.bus = EventBus()
        self.modules = {i: load_atom(i) for i in (810, 820, 830, 840, 850, 860, 150, 166, 451)}
        self.atoms: dict[int, Any] = {}
        self.captured: dict[str, list[dict]] = {}

    def on(self, event: str, bucket: str) -> None:
        self.captured.setdefault(bucket, []).clear()
        self.bus.subscribe(event, self.captured[bucket].append, subscriber="probe")

    async def start(self) -> None:
        configs = {
            810: {"out_dir": str(self.tmp / "telemetry"), "batch_size": 5,
                  "flush_interval_s": 1.0, "max_files": 4, "max_buffer": 100},
            820: {"window": 50, "stale_after_s": 60, "warming_min": 2},
            830: {"entry_threshold": 70, "exit_threshold": 45,
                  "confirmation_window": 3, "min_duration_s": 0.0,
                  "volatility_high": 75},
            840: {"warmup_windows": 2, "drift_threshold": 0.5},
            850: {"min_evidence_windows": 1, "max_change_per_step": 0.25,
                  "max_change_per_day": 0.5, "max_active_experiments": 2,
                  "min_dwell_s": 0.0, "cooldown_s": 0.0, "max_changes_per_window": 5},
            860: {"critical_drift": 1.0, "latency_budget_ms": 50.0},
            150: {"timeout_seconds": 5.0, "live_flush_timeout_s": 1.0},
            166: {"agree_threshold": 0.5, "live_stale_after_s": 5.0,
                  "fast_weight": 55.0, "slow_weight": 45.0,
                  "section_weight": 100.0 / 6.0},
            # (nq seal 2026-08-25: 451 v3.0.0 room model — require_same_cycle
            # no longer exists; config needs only expected_families.)
            451: {"expected_families": ["150"]},
        }
        for atom_id, module in self.modules.items():
            atom = module.Atom()
            self.atoms[atom_id] = atom
            await atom.initialize(AtomContext(
                atom_id, configs[atom_id], _Log(),
                lambda name, payload, aid=atom_id: self.bus.publish(name, payload, publisher=str(aid)),
                lambda name, handler, aid=atom_id: self.bus.subscribe(name, handler, subscriber=str(aid)),
                subscribe_all=self.bus.subscribe_all))
            await atom.start()

    async def feed_tick(self, sequence: str, price: float = 100.0, ts: float = 1.0) -> None:
        await self.bus.publish("market.tick.validated", {
            "account_id": "A", "broker": "BR", "symbol": "NQ",
            "timeframe": "tick", "sequence": sequence,
            "timestamp": ts, "price": price}, publisher="112")
        # (nq seal 2026-08-25: EventBus 1.18.0 enqueues to per-handler mailboxes;
        # drain() replaces sleep(0) so delivery completes before the next feed.)
        await self.bus.drain()

    async def feed_analysis_card(self, ts: float, ready: bool = True) -> None:
        await self.bus.publish("analysis.section.live", {
            "account_id": "A", "broker": "BR", "symbol": "NQ", "section_id": "150",
            "status": "ok", "signal": "up", "score": 70, "confidence": 72,
            "quality": "good", "state": "READY" if ready else "ANALYZING",
            "ready": ready, "timestamp": ts,
            "unified": {"state": "READY" if ready else "ANALYZING",
                        "direction": 60.0, "strength": 70.0, "weight": 16.66,
                        "weight_effect": 16.66 if ready else 0.0}}, publisher="150")
        await self.bus.drain()  # (nq seal 2026-08-25: async mailbox delivery)


@pytest.mark.asyncio
async def test_phase1_observability_contract(tmp_path: Path):
    """الطور ١ — عقد المشاهدة: الظرف الثماني عشر حقلًا، والمجهول معلَن بالاسم."""
    from shared.observability_contract import (ENVELOPE_FIELDS, core_state_of,
                                               stamp_observability)
    card = stamp_observability({"signal": "up"}, atom_id="150", section_id="150",
                               latency_ms=1.2)
    assert card["signal"] == "up"
    env = card["observability"]
    assert env["atom_id"] == "150" and env["latency_ms"] == 1.2
    # غير المقيس معلَن بالاسم — لا صفر مخترع (قاعدة §9)
    assert "regime_id" in card["observability_unknown"]
    assert "calibration_version" in card["observability_unknown"]
    assert set(ENVELOPE_FIELDS) >= {"atom_id", "section_id", "regime_id",
                                    "calibration_version", "technical_health"}
    # خريطة الحالات العشر → حدود النواة الأربع (قرار المراجعة — النواة لا تُمسّ)
    assert core_state_of("WARMING") == "DEGRADED"
    assert core_state_of("INSUFFICIENT_DATA") == "UNKNOWN"
    assert core_state_of("HEALTHY") == "HEALTHY"


@pytest.mark.asyncio
async def test_phase2_telemetry_batches_never_touch_hot_path(tmp_path: Path):
    """الطور ٢ — ناقل القياس: دفعات تُكتب خارج مخازن الإنتاج، والقرار يصدر
    رغم وجوده (البوّابة الوظيفيّة لعدم العرقلة)، والذاكرة محدودة."""
    stack = AdaptiveStack(tmp_path); await stack.start()
    stack.on("decision.aggregated.state", "decisions")
    stack.on("telemetry.batch.closed", "batches")

    for i in range(12):
        await stack.feed_analysis_card(ts=1.0 + i)
        await stack.feed_tick(sequence=str(i), ts=1.0 + i)
    await stack.atoms[810]._flush()
    # (nq seal 2026-08-25: batch.closed is enqueued by the sealed bus — drain
    # before asserting the probe captured it.)
    await stack.bus.drain()

    # بوابة الوظيفة: القرار صدر والمسار الساخن لم يُعرقل
    assert stack.captured["decisions"], "القرار تعطّل بوجود طبقة القياس"
    # بوابة سلامة البيانات: دفعة مكتوبة خارج var/store وبصمة أثر محفوظة
    assert stack.captured["batches"] and stack.captured["batches"][-1]["rows"] > 0
    tel_dir = tmp_path / "telemetry"
    assert tel_dir.is_dir() and list(tel_dir.rglob("*.jsonl.gz"))
    assert not any((ROOT / "var" / "store").rglob("telemetry-*"))
    # بوابة الأداء: العازلة محدودة السقف والترياق معدود
    health = await stack.atoms[810].health_check()
    assert health.details["buffered"] <= 100


@pytest.mark.asyncio
async def test_phase3_measurement_three_levels_unknown_declared(tmp_path: Path):
    """الطور ٣ — المحرك: ثلاثة مستويات لا تُخلط، والمنفعة UNKNOWN بالاسم."""
    stack = AdaptiveStack(tmp_path); await stack.start()
    stack.on("measurement.health.state", "health")
    stack.on("measurement.latency.state", "latency")
    for i in range(4):
        await stack.feed_analysis_card(ts=1.0 + i)
        await stack.feed_tick(sequence=str(i), ts=1.0 + i)
    assert stack.captured["health"], "لم يقس المحرك شيئًا"
    last = stack.captured["health"][-1]
    assert last["section"] == "150"
    assert last["technical_health"] in ("HEALTHY", "WARMING")
    assert last["analytical_health"] in ("HEALTHY", "WARMING", "UNKNOWN")
    # §10: المنفعة مستوى مستقل — UNKNOWN معلنة لا صفر
    assert last["trading_utility"] == "UNKNOWN"
    assert stack.captured["latency"][-1]["samples"] >= 1


@pytest.mark.asyncio
async def test_phase4_regime_hysteresis_no_flip_flop(tmp_path: Path):
    """الطور ٤ — محرك النظام: الحسميات تمنع التذبذب، ولا صلاحية له على شيء."""
    stack = AdaptiveStack(tmp_path); await stack.start()
    stack.on("market.regime.state", "regime")

    async def trend(value: float):
        await stack.bus.publish("structure.trend.state", {
            "symbol": "NQ", "direction": value, "score": value}, publisher="201")
        await stack.bus.drain()  # (nq seal 2026-08-25: async mailbox delivery)

    await trend(80)   # فوق عتبة الدخول — تأكيد 1/3
    await trend(80)   # 2/3
    await trend(80)   # 3/3 → TRANSITION يكتمل إلى TRENDING
    states = [p["regime"] for p in stack.captured["regime"]]
    assert "TRENDING" in states, states
    # اهتزاز تحت عتبة الخروج مرّتين فقط — لا يرتد فورًا (نافذة التأكيد)
    await trend(50); await trend(50)
    latest = stack.captured["regime"][-1]
    assert latest["regime"] in ("TRENDING", "TRANSITION"), latest
    # §14: الناقل يعلن في حمولته نفسها أنه بلا صلاحية
    assert "OBSERVATION_ONLY" in latest["authority"]


@pytest.mark.asyncio
async def test_phase5_and_6_drift_proposal_governor_kill_switch(tmp_path: Path):
    """الطوران ٥ و٦ — الانحراف يقترح، الحاكم يحكم بحدوده، والمفتاح يوقف
    التكيّف وحده والتداول مستمر."""
    stack = AdaptiveStack(tmp_path); await stack.start()
    stack.on("drift.vector.state", "drift")
    stack.on("experiment.state", "experiments")
    stack.on("decision.aggregated.state", "decisions")

    gov = stack.atoms[850]
    drift = stack.atoms[840]

    # بناء خط الأساس ثم تدهور حادّ في الجاهزية
    for i in range(3):
        await stack.atoms[820]._emit("150") if False else None
        await stack.bus.publish("measurement.health.state", {
            "section": "150", "state": "HEALTHY",
            "technical_health": "HEALTHY", "analytical_health": "HEALTHY",
            "trading_utility": "UNKNOWN", "ready_ratio": 0.95, "samples": 10},
            publisher="820")
        await stack.bus.drain()  # (nq seal 2026-08-25: async mailbox delivery)
    for i in range(3):
        await stack.bus.publish("measurement.health.state", {
            "section": "150", "state": "HEALTHY",
            "technical_health": "HEALTHY", "analytical_health": "DEGRADED",
            "trading_utility": "UNKNOWN", "ready_ratio": 0.10, "samples": 10},
            publisher="820")
        await stack.bus.drain()  # (nq seal 2026-08-25: async mailbox delivery)

    assert stack.captured["drift"], "لم يُقس انحراف"
    last = stack.captured["drift"][-1]
    assert last["overall_drift"] is not None and last["overall_drift"] > 0

    # الحاكم حكم: في حدود السلطته — APPROVED_FOR_SHADOW كحدّ أقصى، أو رفض برمز
    assert stack.captured["experiments"]
    verdict = stack.captured["experiments"][-1]
    assert verdict["status"] in ("APPROVED_FOR_SHADOW", "REJECTED")
    assert verdict["max_authority"] == "APPROVED_FOR_SHADOW"
    assert isinstance(verdict["gates"], list) and verdict["gates"]

    # §27: أمر المالك يُسقط المفتاح — والحاكم يرفض كل جديد بعده
    await stack.bus.publish("adaptation.kill_switch.command",
                            {"owner": "owner", "action": "OFF"}, publisher="owner")
    await stack.bus.drain()  # (nq seal 2026-08-25: async mailbox delivery)
    kill = stack.atoms[860]
    assert kill._adaptation_off
    # والتداول مستمر رغم إيقاف التكيّف: قرار جديد يصدر
    await stack.feed_analysis_card(ts=99.0)
    await stack.feed_tick(sequence="after-off", ts=99.0)
    assert stack.captured["decisions"], "التداول تأثّر بمفتاح التكيّف — ممنوع"

    # إعادة التسليح بأمر المالك وحده
    await stack.bus.publish("adaptation.kill_switch.command",
                            {"owner": "owner", "action": "ON"}, publisher="owner")
    await stack.bus.drain()  # (nq seal 2026-08-25: async mailbox delivery)
    assert not kill._adaptation_off
    # أمر مجهول الهوية يُرفض ويُحصى — لا تسلّيح خفيًّا
    await stack.bus.publish("adaptation.kill_switch.command",
                            {"action": "OFF"}, publisher="who")
    await stack.bus.drain()  # (nq seal 2026-08-25: async mailbox delivery)
    assert not kill._adaptation_off
    assert kill._drop_reasons.get("KILL_SWITCH_COMMAND_INVALID") == 1


@pytest.mark.asyncio
async def test_non_regression_full_stack_same_decisions_twice(tmp_path: Path):
    """§31 — بوّابة عدم التراجع: نفس المقطع مرّتين مع الطبقة التكيّفيّة كاملة
    = نفس القرارات (الهويّة الحتميّة من بناء ١+٢ لا تتأثر بالقياس)."""
    async def run_once() -> list:
        stack = AdaptiveStack(tmp_path / ("run-" + str(id(run_once))))
        await stack.start()
        stack.on("decision.aggregated.state", "decisions")
        for i in range(3):
            await stack.feed_analysis_card(ts=1.0 + i)
            await stack.feed_tick(sequence=f"d{i}", ts=1.0 + i)
        return [(d["cycle_id"], d.get("direction")) for d in stack.captured["decisions"]]

    first = await run_once()
    second = await run_once()
    assert first and first == second
