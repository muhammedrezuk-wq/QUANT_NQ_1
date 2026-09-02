from __future__ import annotations

import sys

import asyncio
import importlib.util
import json
import sqlite3
from pathlib import Path
from typing import Any

import clock
import pytest
from shared.cycle_identity import cycle_key_of
import yaml

from shared.live_analysis import (
    AnalysisSettingsStore,
    DEFAULT_WEIGHTS,
    EVENT_SETTING_CHANGED,
    EVENT_SETTINGS,
    EVENT_TICK,
    LiveAnalyzerKernel,
    STATE_ANALYZING,
    STATE_NOT_READY,
    STATE_READY,
    STATE_STALE,
)

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

ANALYZERS = {
    151: "trend", 152: "momentum", 153: "volatility", 154: "volume",
    155: "spread", 156: "candle", 157: "gap", 158: "session", 159: "time",
    160: "correlation", 161: "relative_strength", 162: "velocity",
    163: "acceleration", 164: "volume_quality", 165: "noise",
}
EVENTS = {
    "trend": "analysis.trend.state", "momentum": "analysis.momentum.state",
    "volatility": "analysis.volatility.state", "volume": "analysis.volume.state",
    "spread": "analysis.spread.state", "candle": "analysis.candle.state",
    "gap": "analysis.gap.state", "session": "analysis.session.state",
    "time": "analysis.time.state", "velocity": "analysis.velocity.state",
    "acceleration": "analysis.acceleration.state",
    "volume_quality": "analysis.volume_quality.state", "noise": "analysis.noise.state",
    "correlation": "analysis.correlation.state",
    "relative_strength": "analysis.relative_strength.state",
}


def load_atom(atom_id: int):
    path = next((ATOM_ROOT).glob(f"{atom_id}_*/atom.py"))
    spec = importlib.util.spec_from_file_location(f"analysis_test_atom_{atom_id}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class Context:
    def __init__(self, config: dict[str, Any] | None = None, bus=None):
        self.config = config or {}
        self.handlers: dict[str, list] = {}
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.bus = bus

    def subscribe(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)
        if self.bus is not None:
            self.bus.subscribe(event, handler)

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))
        if self.bus is not None:
            await self.bus.publish(event, payload)


class Bus:
    def __init__(self):
        self.handlers: dict[str, list] = {}
        self.events: list[tuple[str, dict[str, Any]]] = []

    def subscribe(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))
        for handler in list(self.handlers.get(event, [])):
            await handler(payload)


@pytest.fixture
def settings_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "analysis_settings.db"
    monkeypatch.setenv("QUANT_ANALYSIS_SETTINGS_DB", str(path))
    return path


@pytest.mark.asyncio
async def test_each_valid_tick_recomputes_without_candle_and_contract_is_bounded(settings_db: Path):
    context = Context()
    kernel = LiveAnalyzerKernel("trend", EVENTS["trend"])
    await kernel.initialize(context)
    kernel.start()
    base = clock.now()
    for index in range(32):
        await kernel.on_tick({"account_id": "A-1", "broker": "BR", "symbol": "NQ", "bid": 20000 + index * 0.5,
                              "ask": 20000.25 + index * 0.5, "price": 20000.125 + index * 0.5,
                              "volume": 10 + index, "timestamp": base + index * 0.01})
    outputs = [payload for event, payload in context.events if event == EVENTS["trend"]]
    assert len(outputs) == 32
    assert [item["sequence"] for item in outputs] == list(range(1, 33))
    assert all(item["analysis_mode"] == "live_tick" and item["timeframe"] == "tick" for item in outputs)
    last = outputs[-1]
    assert -100 <= last["direction"] <= 100
    assert 0 <= last["confidence"] <= 100
    assert 0 <= last["current_depth"] <= 100
    assert 0 <= last["required_depth"] <= 100
    assert last["state"] in {STATE_ANALYZING, STATE_NOT_READY, STATE_READY}
    assert last["source_timestamp"] == pytest.approx(base + 0.31)
    assert "market_data.candle_closed" not in context.handlers
    assert EVENT_TICK in context.handlers


@pytest.mark.asyncio
async def test_analyzers_have_independent_evidence_profiles_not_one_copied_result(settings_db: Path):
    kernels: list[LiveAnalyzerKernel] = []
    contexts: list[Context] = []
    for analyzer_id in ANALYZERS.values():
        context = Context(); kernel = LiveAnalyzerKernel(analyzer_id, EVENTS[analyzer_id])
        await kernel.initialize(context); kernel.start()
        contexts.append(context); kernels.append(kernel)
    base = clock.now()
    price = 20000.0
    for index in range(30):
        price += (0.25 if index % 5 else -0.8) + index * 0.01
        tick = {"account_id": "A", "broker": "BR", "symbol": "NQ", "bid": price,
                "ask": price + (0.2 if index % 3 else 0.5), "price": price + 0.1,
                "volume": 1 + (index % 7) * 3, "timestamp": base + index * 0.01}
        for kernel in kernels:
            await kernel.on_tick(tick)
    directions = [kernel.states[("A", "BR", "NQ")].last_payload["direction"] for kernel in kernels]
    assert len(set(directions)) >= 8
    assert all(kernel.states[("A", "BR", "NQ")].sequence == 30 for kernel in kernels)


@pytest.mark.asyncio
async def test_scope_isolation_and_depth_is_evidence_not_a_fixed_tick_counter(settings_db: Path):
    context = Context()
    kernel = LiveAnalyzerKernel("momentum", EVENTS["momentum"])
    await kernel.initialize(context)
    kernel.start()
    base = clock.now()
    for index in range(28):
        # نفس عدد التكات، لكن الدليل السعري مختلف جذريًا.
        for account, symbol, movement in (("A", "NQ", 0.0), ("B", "ES", (1 if index % 2 else -1) * 2.0)):
            price = 10000 + movement * index
            await kernel.on_tick({"account_id": account, "broker": "BR",
                                  "symbol": symbol, "bid": price,
                                  "ask": price + 0.25, "price": price + 0.125,
                                  "volume": 2, "timestamp": base + index * 0.02})
    a = kernel.states[("A", "BR", "NQ")].last_payload
    b = kernel.states[("B", "BR", "ES")].last_payload
    assert a and b
    assert a["sequence"] == b["sequence"] == 28
    assert a["current_depth"] != b["current_depth"]
    assert set(kernel.states) == {("A", "BR", "NQ"), ("B", "BR", "ES")}
    before = kernel.states[("B", "BR", "ES")].sequence
    await kernel.on_tick({"account_id": "A", "broker": "BR", "symbol": "NQ", "bid": 10001, "ask": 10001.25,
                          "price": 10001.125, "volume": 1, "timestamp": base + 1})
    assert kernel.states[("B", "BR", "ES")].sequence == before


@pytest.mark.asyncio
async def test_threshold_depth_and_weight_are_independent_and_persist_with_audit(settings_db: Path):
    context = Context()
    kernel = LiveAnalyzerKernel("trend", EVENTS["trend"])
    await kernel.initialize(context)
    kernel.start()
    base = clock.now()
    for index in range(26):
        await kernel.on_tick({"account_id": "A", "broker": "BR", "symbol": "NQ", "bid": 20000 + index,
                              "ask": 20000.25 + index, "price": 20000.125 + index,
                              "volume": 5, "timestamp": base + index * 0.01})
    state = kernel.states[("A", "BR", "NQ")]
    baseline = kernel._analyze(("A", "BR", "NQ"), state, state.timestamps[-1])

    await kernel.on_settings({"account_id": "A", "broker": "BR", "symbol": "NQ", "analyzer_id": "trend",
                              "settings": {"required_depth": 0, "confidence_threshold": 0, "weight": 23},
                              "operator": "المالك", "command_id": "cmd-1"})
    ready = kernel._analyze(("A", "BR", "NQ"), state, state.timestamps[-1])
    assert ready["state"] == STATE_READY and ready["weight"] == 23
    assert ready["direction"] == baseline["direction"]
    assert ready["confidence"] == baseline["confidence"]

    await kernel.on_settings({"account_id": "A", "broker": "BR", "symbol": "NQ", "analyzer_id": "trend",
                              "settings": {"confidence_threshold": 100},
                              "operator": "المالك", "command_id": "cmd-2"})
    blocked = kernel._analyze(("A", "BR", "NQ"), state, state.timestamps[-1])
    assert blocked["state"] == STATE_NOT_READY
    assert blocked["direction"] == ready["direction"]
    assert blocked["weight"] == ready["weight"]

    reopened = AnalysisSettingsStore(settings_db).get("A", "BR", "NQ", "trend")
    assert reopened["required_depth"] == 0
    assert reopened["confidence_threshold"] == 100
    assert reopened["weight"] == 23
    with sqlite3.connect(settings_db) as connection:
        audit = connection.execute("SELECT command_id,old_json,new_json FROM analysis_settings_audit ORDER BY audit_id").fetchall()
    # NQ-22 ق٢ (2026-08-20): أمر الوزن `cmd-1` وزّع فرقه بالتساوي على الأربعة
    # عشر الآخرين بنفس المناقلة — صفّ أساس لكل أمر + ١٤ صفّ ":eq" لأمر الوزن.
    primary = [row[0] for row in audit if ":eq:" not in row[0]]
    equalized = [row[0] for row in audit if ":eq:" in row[0]]
    assert primary == ["cmd-1", "cmd-2"]
    assert len(equalized) == 14
    assert all(command.startswith("cmd-1:eq:") for command in equalized)
    assert json.loads(audit[-1][2])["revision"] == 2
    store = AnalysisSettingsStore(settings_db)
    total = sum(store.get("A", "BR", "NQ", analyzer)["weight"] for analyzer in DEFAULT_WEIGHTS)
    assert round(total, 4) == 100.0
    changed = [payload for event, payload in context.events if event == EVENT_SETTING_CHANGED]
    assert len(changed) == 2


@pytest.mark.asyncio
async def test_stale_replaces_ready_and_never_remains_usable(settings_db: Path):
    context = Context()
    kernel = LiveAnalyzerKernel("trend", EVENTS["trend"])
    await kernel.initialize(context)
    kernel.start()
    await kernel.on_settings({"account_id": "A", "broker": "BR", "symbol": "NQ", "analyzer_id": "trend",
                              "settings": {"required_depth": 0, "confidence_threshold": 0},
                              "operator": "المالك", "command_id": "ready"})
    base = clock.now()
    await kernel.on_tick({"account_id": "A", "broker": "BR", "symbol": "NQ", "bid": 100, "ask": 100.1,
                          "price": 100.05, "volume": 1, "timestamp": base})
    assert kernel.states[("A", "BR", "NQ")].last_payload["state"] == STATE_READY
    await kernel.on_second({"official_time": base + 6})
    stale = kernel.states[("A", "BR", "NQ")].last_payload
    assert stale["state"] == STATE_STALE
    assert stale["ready"] is False and stale["status"] == "stale"


@pytest.mark.asyncio
async def test_manager_and_fusion_use_latest_ready_only_without_waiting_for_all(settings_db: Path):
    manager_module, fusion_module = load_atom(150), load_atom(166)
    bus = Bus()
    manager, fusion = manager_module.Atom(), fusion_module.Atom()
    await manager.initialize(Context({"timeout_seconds": 5.0}, bus))
    await fusion.initialize(Context({"agree_threshold": 0.2}, bus))
    await manager.start(); await fusion.start()
    common = {"account_id": "A", "broker": "BR", "symbol": "NQ", "analysis_mode": "live_tick",
              "live_contract_version": 1, "timeframe": "tick", "source_timestamp": 100.0,
              "timestamp": 100.1, "required_depth": 60, "confidence_threshold": 60,
              "threshold": 60}
    # عقد NQ-22: مدير 150 يلمّ دفعة التِكّة ويدفقها بالمهلة (لا انتظار الكل)،
    # و166 ينشر جسم القسم (المسارين 55/45) على analysis.raw.completed.
    await bus.publish("SYS_SECOND", {"official_time": 100.0})
    await bus.publish(EVENTS["trend"], {**common, "id": "trend", "analyzer_id": "trend",
        "sequence": 1, "score": 80, "direction": 80, "confidence": 80, "current_depth": 80,
        "weight": 15, "state": STATE_READY, "analysis_state": STATE_READY, "ready": True, "status": "ok"})
    await bus.publish("SYS_SECOND", {"official_time": 102.0})
    fused = [p for e, p in bus.events if e == "analysis.raw.completed"][-1]
    assert fused["status"] == "ok" and fused["score"] == 80
    assert fused["timeframe"] == "section"
    # 451 يرفض حمولة حية بلا حساب/وسيط — الهوية تعبر جسم القسم كاملة.
    assert fused["account_id"] == "A" and fused["broker"] == "BR"
    fast_card = fused["paths"]["fast"]
    assert fast_card["active_weight"] == 15
    assert fused["contributors"]["trend"]["included"] is True
    # المسار البطيء غائب: يُعلَن بوزنه لا يُخفى، والدمج لا ينتظره.
    assert fused["paths"]["slow"] is None
    assert "missing_path" in fused["warnings"]
    assert fused["path_missing_weight"] == 45.0
    assert fused["section_contract"]["direction"] == 80

    await bus.publish(EVENTS["momentum"], {**common, "id": "momentum", "analyzer_id": "momentum",
        "sequence": 1, "score": -100, "direction": -100, "confidence": 99, "current_depth": 20,
        "weight": 10, "state": STATE_ANALYZING, "analysis_state": STATE_ANALYZING,
        "ready": False, "status": "not_ready"})
    await bus.publish("SYS_SECOND", {"official_time": 104.0})
    fused = [p for e, p in bus.events if e == "analysis.raw.completed"][-1]
    assert fused["score"] == 80  # غير الجاهز ليس صوت حياد ولا صوتًا معاكسًا.
    assert fused["paths"]["fast"]["active_weight"] == 15
    assert fused["contributors"]["momentum"]["included"] is False
    assert fused["contributors"]["momentum"]["weight_applied"] == 0

    await bus.publish(EVENTS["trend"], {**common, "id": "trend", "analyzer_id": "trend",
        "sequence": 2, "score": 80, "confidence": 80, "current_depth": 80, "weight": 15,
        "state": STATE_STALE, "analysis_state": STATE_STALE, "ready": False, "status": "stale"})
    await bus.publish("SYS_SECOND", {"official_time": 106.0})
    fused = [p for e, p in bus.events if e == "analysis.raw.completed"][-1]
    assert fused["status"] == "insufficient_data"
    assert fused["signal"] is None and fused["score"] is None
    assert fused["paths"]["fast"]["active_weight"] == 0
    # الغائب يُعلَن مجهولًا بالعقد لا صفرًا معلومًا.
    assert "direction" in fused["section_contract"]["unknown_fields"]


@pytest.mark.asyncio
async def test_decision_consumer_accepts_only_ready_live_analysis(settings_db: Path):
    # البناء ٤ (أمر المالك ٢٠٢٦-٠٨-٢٣): التحليل يدخل 451 بطاقة قسم واحدة على
    # analysis.section.live (عائلة 150) — الطريق القديم (analysis.raw.completed
    # كعائلة 166) مُغلق نهائيًّا.
    # (nq seal 2026-08-25: 451 v3.0.0 room model — READY-only admission is
    # REPLACED. A NOT_READY card is now ADMITTED to the room and stored as
    # evidence "150-live" with weight_effect 0.0 and weight_known False, so it
    # contributes NOTHING to the weighted numbers: presence != readiness.
    # Old attrs _latest_section_live/_published are gone; every validated tick
    # publishes ONE decision snapshot with decision_id "dec:"+cycle_id.)
    module = load_atom(451)
    atom = module.Atom()
    context = Context({"expected_families": ["150"]})
    await atom.initialize(context); await atom.start()

    def card(state: str, ready: bool, signal: str = "up", seq: int = 8) -> dict:
        # البطاقة غير الجاهزة تعلن مجهولها None بالعقد — لا صفرًا مصطنعًا.
        return {"account_id": "A", "broker": "BR", "symbol": "NQ",
                "section_id": "150", "status": "ok", "signal": signal,
                "score": 40, "confidence": 70, "quality": "good",
                "state": state, "ready": ready, "sequence": seq,
                "unified": {"state": state,
                            "direction": 60.0 if ready else None,
                            "strength": 40.0 if ready else None,
                            "weight": 16.6667 if ready else None,
                            "unknown_fields": [] if ready else
                                ["direction", "strength", "weight"],
                            "weight_effect": 16.6667 if ready else 0.0}}

    scope = ("A", "BR", "NQ")
    await atom._on_section_live(card("ANALYZING", False))
    # حضور بلا مساهمة: البطاقة غير الجاهزة تدخل الغرفة والدليل — بوزن معدوم.
    assert atom._room[scope]["150"]["state"] == "ANALYZING"
    not_ready_row = atom._evidence_store[scope]["150-live"]
    assert not_ready_row["weight_effect"] == 0.0
    assert not_ready_row["weight_known"] is False

    tick = {"account_id": "A", "broker": "BR", "symbol": "NQ", "timeframe": "tick",
            "sequence": "8", "timestamp": 1.0, "price": 100}
    await atom._on_tick(tick)
    published = [payload for name, payload in context.events if name == module.EVENT_OUT]
    assert published, "لم تُنشر لقطة قرار للتكة"
    snapshot = published[-1]
    # الحضور يكمل العائلة — لكنه لا يحرّك رقمًا واحدًا في القرار.
    assert snapshot["complete"] is True
    assert snapshot["active_weight"] == 0.0
    assert snapshot["score"] is None and snapshot["confidence"] is None
    assert snapshot["calibrated"] is False

    await atom._on_section_live(card("READY", True))
    ready_row = atom._evidence_store[scope]["150-live"]
    assert ready_row["weight_known"] is True
    assert ready_row["weight_effect"] == pytest.approx(16.6667)
    await atom._on_tick(tick)
    published = [payload for name, payload in context.events if name == module.EVENT_OUT]
    decided = published[-1]
    cycle_id = cycle_key_of(tick, timeframe="tick", period_start="8")
    assert decided["decision_id"] == "dec:" + cycle_id
    def family(row: dict) -> str:
        return str(row.get("source", "")).split(":", 1)[0].removesuffix("-live")
    rows_150 = [row for row in decided["evidence"] if family(row) == "150"]
    rows_166 = [row for row in decided["evidence"] if family(row) == "166"]
    assert rows_150 and rows_150[0]["unified_state"] == "READY"
    assert not rows_166, "الطريق القديم (عائلة 166) لم يُغلق — دليل مكرر"
    # الجاهزة وحدها تساهم بالأرقام الموزونة.
    assert decided["active_weight"] == pytest.approx(16.6667)
    assert decided["score"] == pytest.approx(60.0)
    # بطاقة راجعة إلى غير جاهزة تبقى حاضرة — وتفقد مساهمتها فورًا.
    await atom._on_section_live(card("ANALYZING", False))
    assert atom._room[scope]["150"]["state"] == "ANALYZING"
    assert atom._evidence_store[scope]["150-live"]["weight_effect"] == 0.0
    await atom._on_tick(tick)
    reverted = [payload for name, payload in context.events
                if name == module.EVENT_OUT][-1]
    assert reverted["active_weight"] == 0.0 and reverted["score"] is None


@pytest.mark.asyncio
async def test_owner_setting_crosses_901_and_reaches_only_target_analyzer(settings_db: Path, tmp_path: Path):
    gateway_module = load_atom(901)
    bus = Bus()
    gateway = gateway_module.Atom()
    command_db = tmp_path / "commands.db"
    await gateway.initialize(Context({"db_path": str(command_db), "max_age_s": 60.0,
                                      "batch_limit": 10}, bus))
    target = LiveAnalyzerKernel("noise", EVENTS["noise"])
    other = LiveAnalyzerKernel("trend", EVENTS["trend"])
    await target.initialize(Context(bus=bus)); await other.initialize(Context(bus=bus))
    await gateway.start(); target.start(); other.start()
    now = clock.now()
    connection = gateway._connect()
    connection.execute("""INSERT INTO commands(action,operator,requested_at,status,payload_json)
        VALUES(?,?,?,?,?)""", ("analysis_setting", "المالك", now, "PENDING", json.dumps({
            "account_id": "A", "broker": "BR", "symbol": "NQ", "analyzer_id": "noise",
            "settings": {"required_depth": 72, "confidence_threshold": 68, "weight": 9},
        })))
    connection.commit()
    await gateway._on_pulse({"official_time": now + 0.1})
    stored = AnalysisSettingsStore(settings_db).get("A", "BR", "NQ", "noise")
    assert (stored["required_depth"], stored["confidence_threshold"], stored["weight"]) == (72, 68, 9)
    # NQ-22 ق٢: الأمر نفسه يصل لمحلّله الهدف وحده — عمق `trend` وعتبته لم
    # يُمسّا — لكن وزنه دفع حصّته المتساوية من فرق وزن `noise` بنفس المناقلة.
    untouched = AnalysisSettingsStore(settings_db).get("A", "BR", "NQ", "trend")
    assert (untouched["required_depth"], untouched["confidence_threshold"]) == (60.0, 60.0)
    assert round(untouched["weight"], 4) == 6.5
    assert untouched["revision"] == 1
    with sqlite3.connect(command_db) as check:
        assert check.execute("SELECT status FROM commands").fetchone()[0] == "DONE"


def test_all_fifteen_manifests_and_sources_have_live_contract():
    manifest_versions = set()
    for atom_id, analyzer_id in ANALYZERS.items():
        folder = next((ATOM_ROOT).glob(f"{atom_id}_*"))
        manifest = yaml.safe_load((folder / "manifest.yaml").read_text())
        source = (folder / "atom.py").read_text()
        # عقد الحيّ خطّ `2.x`: الرقم الأخير يرتفع كلّما تغيّر الكود — وهو
        # إشارة إعادة التحميل الحارّ في هذا المشروع، ولا يغيّر العقد نفسه.
        # ⛔ تثبيته على `2.0.0` كان يمنع ترقية الذرّة بلا سبب تعاقديّ، ثم
        # تثبيته على `2.0.` منع ارتقاء الخط كلّه معًا (صاروا 2.1.0).
        assert str(manifest["version"]).startswith("2.")
        manifest_versions.add(str(manifest["version"]))
        assert {EVENT_TICK, EVENT_SETTINGS, "SYS_SECOND"}.issubset(manifest["subscribes"])
        assert EVENT_SETTING_CHANGED in manifest["publishes"]
        assert f'@live_analyzer("{analyzer_id}", EVENT_OUT)' in source
        assert 'market_data.candle_closed' in manifest["subscribes"]  # مسار قديم فقط، لا مصدر العقد الحي.
    assert sum(DEFAULT_WEIGHTS.values()) == 100
    # الخطّ موحّد: الخمسة عشر محلّلًا على إصدار واحد — لا يتفرّقون أبدًا.
    assert len(manifest_versions) == 1, sorted(manifest_versions)
