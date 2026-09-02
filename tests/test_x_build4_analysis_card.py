"""ورقة X — البناء ٤: مجمّع قسم التحليل — بطاقة واحدة بطريق واحد.

مقاييس الورقة الستّ:
  ١ · 166 يترك slow=0 — العدّاد > صفر
  ٢ · fused > 0 — الدمج يحدث فعلًا
  ٣ · يظهر حدث analysis.section.live
  ٤ · البطاقة بالحقول الثمانية كاملة وبلا صفر مصطنع (المجهول معلَن)
  ٥ · في دورة 451 واحدة: دليل التحليل مرّة واحدة (150) — والطريق القديم (166) مغلق
  ٦ · صفّ البطيء يمتلئ — analysis.slow.state يصدر ببيانات

السلسلة حقيقية بالكامل: 150 ← 166 ← 451 على ناقل النواة.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from core.contracts.atom import AtomContext
from core.event_bus import EventBus
from shared.cycle_identity import cycle_key_of

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)


UNIT_IDS = ("trend", "momentum", "volatility", "volume", "spread",
            "candle", "gap", "session", "time", "velocity", "acceleration",
            "volume_quality", "noise", "correlation", "relative_strength")
UNIT_EVENTS = {u: f"analysis.{u.replace('_', '_')}.state" for u in UNIT_IDS}
# أسماء أحداث الوحدات كما في 150 حرفيًّا
UNIT_EVENTS = dict(zip(UNIT_IDS, (
    "analysis.trend.state", "analysis.momentum.state", "analysis.volatility.state",
    "analysis.volume.state", "analysis.spread.state", "analysis.candle.state",
    "analysis.gap.state", "analysis.session.state", "analysis.time.state",
    "analysis.velocity.state", "analysis.acceleration.state",
    "analysis.volume_quality.state", "analysis.noise.state",
    "analysis.correlation.state", "analysis.relative_strength.state")))


class _Log:
    def __getattr__(self, _n: str):
        return lambda *a, **k: None


def load_atom(atom_id: int):
    folder = next((ATOM_ROOT).glob(f"{atom_id}_*/atom.py"))
    sys.path.insert(0, str(folder.parent))
    try:
        spec = importlib.util.spec_from_file_location(f"x_build4_atom_{atom_id}", folder)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)



import os
import tempfile


def _approve_all_parameters(tmp_dir: Path) -> None:
    """اعتماد معاملات محرك الدمج في قاعدة مؤقتة — الطريق القانوني نفسه
    (قرار مالك: SOURCE_OWNER بهوية أمر) ثم تفريغ بوابة الكاش."""
    import shared.parameter_registry as pr
    db = tmp_dir / "params.db"
    os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = str(db)
    store = pr.ParameterRegistry()
    for row in store.all():
        if row["status"] != pr.STATUS_APPROVED:
            store.approve(row["name"], value=float(row["value"]),
                          source=pr.SOURCE_OWNER, approved_by="owner",
                          command_id=f"x-build4-{row['name']}", approved_at=1.0)
    pr.refresh_gate()


class Chain:
    def __init__(self):
        self.bus = EventBus()
        self.modules = {i: load_atom(i) for i in (150, 166, 451)}
        self.atoms: dict[int, Any] = {}
        self.section_cards: list[dict] = []
        self.slow_states: list[dict] = []
        self.decisions: list[dict] = []

    async def start(self):
        configs = {
            150: {"timeout_seconds": 5.0, "live_flush_timeout_s": 1.0},
            166: {"section_weight": 100.0 / 6.0, "agree_threshold": 0.5,
                  "live_stale_after_s": 5.0,
                  "fast_weight": 55.0, "slow_weight": 45.0},
            # (nq seal 2026-08-25: 451 v3.0.0 room model — require_same_cycle
            # no longer exists; config requires only expected_families.)
            451: {"expected_families": ["150"]},
        }
        for atom_id, module in self.modules.items():
            atom = module.Atom()
            self.atoms[atom_id] = atom
            await atom.initialize(AtomContext(
                atom_id, configs[atom_id], _Log(),
                lambda name, payload, aid=atom_id: self.bus.publish(name, payload, publisher=str(aid)),
                lambda name, handler, aid=atom_id: self.bus.subscribe(name, handler, subscriber=str(aid))))
            await atom.start()
        self.bus.subscribe("analysis.section.live", self.section_cards.append, subscriber="probe")
        self.bus.subscribe("analysis.slow.state", self.slow_states.append, subscriber="probe")
        self.bus.subscribe("decision.aggregated.state", self.decisions.append, subscriber="probe")

    async def feed_live(self, sequence: int = 1, ts: float = 1_800_000_000.0):
        """١٥ صفًّا حيًّا كما تنشرها المحللات ← دورة live تكتمل فور اكتمال الدفعة."""
        for unit in UNIT_IDS:
            await self.bus.publish(UNIT_EVENTS[unit], {
                "account_id": "A", "broker": "BR", "symbol": "NQ",
                "analyzer_id": unit, "sequence": sequence,
                "analysis_state": "DECISION_READY", "ready": True,
                "weight": 100.0 / 15.0, "confidence": 70.0,
                "current_depth": 80.0, "required_depth": 60.0,
                "confidence_threshold": 50.0,
                "direction": 60.0, "score": 60.0, "quality": "good",
                "source_timestamp": ts, "timestamp": ts}, publisher=unit)
        # (nq seal 2026-08-25: EventBus 1.18.0 — publish enqueues to per-handler
        # mailboxes; drain waits for actual delivery before asserting.)
        await self.bus.drain()

    async def feed_candle(self, period_start: float = 1_800_000_060.0):
        """شمعة تُغلق ثم ١٥ صفّ شمعة كما تخرج من محللات الشموع ← دورة candle."""
        candle = {"account_id": "A", "broker": "BR", "symbol": "NQ",
                  "timeframe": "1m", "period_start": period_start,
                  "timestamp": period_start, "close": 20050.0}
        await self.bus.publish("market_data.candle_closed", candle, publisher="103")
        cycle_id = cycle_key_of(candle, symbol="NQ", timeframe="1m",
                                period_start=period_start)
        for unit in UNIT_IDS:
            await self.bus.publish(UNIT_EVENTS[unit], {
                "symbol": "NQ", "id": unit, "analyzer_id": unit,
                "cycle_id": cycle_id, "timeframe": "1m",
                "status": "ok", "signal": "buy", "score": 65.0,
                "confidence": 68.0, "strength": "normal", "quality": "good"}, publisher=unit)
        await self.bus.drain()  # (nq seal 2026-08-25: async mailbox delivery)
        return cycle_id

    async def feed_tick(self, sequence: str = "77"):
        await self.bus.publish("market.tick.validated", {
            "account_id": "A", "broker": "BR", "symbol": "NQ",
            "timeframe": "tick", "sequence": sequence,
            "timestamp": 1_800_000_090.0, "price": 20050.0}, publisher="112")
        await self.bus.drain()  # (nq seal 2026-08-25: async mailbox delivery)


@pytest.mark.asyncio
async def test_build4_all_six_measures(tmp_path: Path):
    _approve_all_parameters(tmp_path)
    chain = Chain()
    await chain.start()

    # ── المسار السريع أولًا: دورة live من 150 → 166 يخزن fast وينشر بطاقة قسم
    await chain.feed_live(sequence=1)
    assert chain.section_cards, "لم تُنشر بطاقة القسم من المسار السريع"

    # ── المسار البطيء: شمعة → دورة candle من 150 → 166 يخزن slow
    await chain.feed_candle()

    a166 = chain.atoms[166]
    # المقياس ١: slow ترك الصفر
    assert getattr(a166, "_slow_published", 0) > 0, "slow ما زال صفرًا"
    # المقياس ٢: الدمج يحدث
    assert getattr(a166, "_fused", 0) > 0, "fused ما زال صفرًا"
    # المقياس ٣: حدث البطاقة القسمية ظهر
    assert chain.section_cards, "analysis.section.live لم يظهر"
    # المقياس ٦: صفّ البطيء يمتلئ — analysis.slow.state ببيانات
    assert chain.slow_states and chain.slow_states[-1].get("confidence") is not None

    # ── المقياس ٤: البطاقة الثمانية كاملة — بلا صفر مصطنع والمجهول معلَن
    card = chain.section_cards[-1]
    assert card["section_id"] == "150"
    eight = card["section_contract"]
    for field in ("direction", "strength", "confidence", "current_depth",
                  "required_depth", "weight", "ratio", "state"):
        assert field in eight, field
    # وزن القسم = 100/6 (قاعدة التساوي المختومة) لا مجموع 55+45
    assert eight["weight"] == pytest.approx(100.0 / 6.0, rel=1e-3)
    # المساران داخل البطاقة للتشخيص فقط
    assert set(card["paths"].keys()) <= {"fast", "slow"} and "slow" in card["paths"]

    # ── المقياس ٥: دورة قرار واحدة — دليل التحليل مرة واحدة (150) والقديم مغلق
    await chain.feed_tick()
    assert chain.decisions, "لم تصدر دورة قرار"
    evidence = chain.decisions[-1]["evidence"]

    def family(row: dict) -> str:
        return str(row.get("source", "")).split(":", 1)[0].removesuffix("-live")

    rows_150 = [r for r in evidence if family(r) == "150"]
    rows_166 = [r for r in evidence if family(r) == "166"]
    assert len(rows_150) == 1, f"دليل التحليل تكرر: {len(rows_150)}"
    assert not rows_166, "الطريق القديم (166) مفتوح — دليل مكرر"


@pytest.mark.asyncio
async def test_build4_not_ready_card_is_present_but_contributes_nothing(tmp_path: Path):
    # (nq seal 2026-08-25: 451 v3.0.0 room model — the old guard "no decision
    # until READY" is replaced ON PURPOSE: every validated tick now publishes
    # ONE decision.aggregated.state snapshot, and a NOT_READY section card IS
    # admitted as presence (evidence "150-live") but contributes NOTHING —
    # weight_effect 0.0, weight_known False, zero active_weight, honest None
    # numbers. Same intent: a not-ready card never moves the decision.)
    _approve_all_parameters(tmp_path)
    chain = Chain()
    await chain.start()
    # بطاقة غير جاهزة (مسار سريع وحده قبل النضج يظل state غير READY) ثم تكة:
    await chain.bus.publish("analysis.section.live", {
        "account_id": "A", "broker": "BR", "symbol": "NQ", "section_id": "150",
        "state": "ANALYZING", "ready": False, "unified": {"state": "ANALYZING"}},
        publisher="probe")
    await chain.bus.drain()
    await chain.feed_tick(sequence="88")
    # قرار يصدر لكل تكة — لكن البطاقة غير الجاهزة حضورٌ بلا مساهمة.
    assert chain.decisions, "لم يصدر قرار للتكة — قانون الغرفة ينشر كل تكة"
    decision = chain.decisions[-1]
    live_rows = [r for r in decision["evidence"] if r.get("source") == "150-live"]
    assert live_rows, "البطاقة غير الجاهزة لم تدخل الغرفة كحضور"
    assert live_rows[0]["weight_effect"] == 0.0
    assert live_rows[0]["weight_known"] is False
    assert decision["active_weight"] == 0.0
    assert decision["score"] is None and decision["confidence"] is None
    assert decision["aggregate_state"] == "ANALYZING"
