"""ورقة X — البناء ١: إحياء سلسلة القرار — اختبارات القبول.

مقاييس الورقة الخمسة، منقولًا إلى عمليّات قابلة للتشغيل هنا:
  ١ · 451 يترك NO_CYCLES_YET وعدّاد الدورات > صفر        (المقياس ١)
  ٢ · يظهر decision.aggregated.state                     (المقياس ٢)
  ٣ · تصل السلسلة إلى decision.scored.state              (المقياس ٣)
  ٤ · cycle_id عند 453 = cycle_id عند القسم المغذّي       (المقياس ٤)
  ٥ · صفر خطأ جديد في الناقل                              (المقياس ٥ — في الجهاز
       الحيّ يُقرأ من var/logs/errors-*.log؛ هنا من عدّادات الناقل)

والسيناريو الأهمّ — إحياء عطل الجهاز الحيّ نفسه: تِكّة بلا sequence من مصدر
ترقيميّ (613 قبل 2.4.0 كان يُسقطها) — يجب أن تُفتح الدورة رغم ذلك عبر سلسلة
احتياط هوية المحوّل القانوني.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from core.contracts.atom import AtomContext
from core.event_bus import EventBus
from shared.tick_contract import as_validated_tick

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)



class _Log:
    def __getattr__(self, _name: str):
        return lambda *a, **k: None


def load_atom(atom_id: int):
    folder = next((ATOM_ROOT).glob(f"{atom_id}_*/atom.py"))
    sys.path.insert(0, str(folder.parent))
    try:
        spec = importlib.util.spec_from_file_location(f"x_build1_atom_{atom_id}", folder)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class Harness:
    """ناقل حقيقي + ذرّات حقيقيّة على مسار التغذية الحيّ كاملًا."""

    def __init__(self):
        self.bus = EventBus()
        self.modules = {i: load_atom(i) for i in (613, 112, 451, 452, 453)}
        self.atoms: dict[int, Any] = {}

    async def start(self, *, expected: tuple[str, ...] = ("150", "400"),
                    directional_sources: list[str] | None = None) -> None:
        configs = {
            613: {"routes": {"feed.ctrader.tick": "market.tick",
                              "feed.mt5.tick": "market.tick"},
                  "provider_timeout_s": 30, "max_input_silence_seconds": 60,
                  "preferred_provider": "CTRADER"},
            112: {},
            # (nq seal 2026-08-25: 451 v3.0.0 room model -- require_same_cycle
            # no longer exists; config takes only expected_families [+optional
            # family_freshness_s]. Freshness, not same-cycle batching.)
            451: {"expected_families": list(expected)},
            452: {"low_quality_factor": 0.5, "min_confidence": 0.0},
            453: {"directional_weight": 1.0, "context_weight": 0.0556,
                  "min_participation": 0.2,
                  "directional_sources": directional_sources or ["400"]},
        }
        for atom_id, module in self.modules.items():
            atom = module.Atom()
            self.atoms[atom_id] = atom
            await atom.initialize(AtomContext(
                atom_id, configs[atom_id], _Log(),
                lambda name, payload, aid=atom_id: self.bus.publish(name, payload, publisher=str(aid)),
                lambda name, handler, aid=atom_id: self.bus.subscribe(name, handler, subscriber=str(aid))))
            await atom.start()

    def rows(self, event: str) -> list[dict]:
        collected: list[dict] = []
        self.bus.subscribe(event, lambda p: collected.append(p), subscriber="probe")
        return collected  # يُملأ لاحقًا بالأحداث القادمة فقط — لذا نستخدم grab


async def feed_622_style(h: Harness, *, sequence: str | None,
                         symbol: str = "NQ", ts: float = 1_800_000_000.0) -> str:
    """تِكّة بشكل 622 تمامًا (حمولة payload_json المسجّلة في market_data.db)."""
    payload: dict[str, Any] = {
        "account_id": "A", "broker": "Raw Trading Ltd", "symbol": symbol,
        "provider": "CTRADER",
        "bid": 20000.24, "ask": 20000.26, "price": 20000.25,
        "volume": 12, "timestamp": ts, "exchange_timestamp": ts,
    }
    if sequence is not None:
        payload["sequence"] = sequence
    # نلتقط التِكّة كما صادق عليها 112 فعليًا — فالهوية تُشتقّ من الحمولة الواصلة
    # إلى 451 (الناقل يحقن event_id عند النشر فيسبق stamp@bid:ask في سلسلة
    # الاحتياط — والاشتقاق المحلّي قبل النشر يعطي معرّفًا مختلفًا).
    validated_capture: list[dict] = []
    h.bus.subscribe("market.tick.validated", validated_capture.append, subscriber="identity_probe")
    await h.bus.publish("feed.ctrader.tick", payload, publisher="test_622")
    # (nq seal 2026-08-25: EventBus 1.18.0 enqueues -- delivery is async.
    # drain() waits for the whole 613->112->451->452->453 cascade to land
    # before we read the captured identity or unsubscribe the probe.)
    assert await h.bus.drain(timeout_s=10.0), "الناقل لم يفرغ صناديقه في المهلة"
    h.bus.unsubscribe("market.tick.validated", validated_capture.append)
    assert validated_capture, "التِكّة لم تُصادَق عليها"
    return as_validated_tick(validated_capture[-1])["cycle_id"]


# البناء ٤: التحليل يدخل بطاقة قسم (150) على analysis.section.live
ANALYSIS_LIVE_EVENT = "analysis.section.live"

def analysis_card() -> dict:
    return {"account_id": "A", "broker": "Raw Trading Ltd", "symbol": "NQ",
            "section_id": "150", "status": "ok", "signal": "up", "score": 70,
            "confidence": 72, "quality": "good", "state": "READY", "ready": True,
            "unified": {"state": "READY", "direction": 60.0, "strength": 70.0,
                        "weight": 16.6667, "weight_effect": 16.6667}}


STRATEGY_LIVE_EVENT = "strategy.section.live"

def strategy_card(cycle_id: str) -> dict:
    # بطاقة القسم الوصفي كما تنشرها 400 فعليًا على strategy.section.live
    # (451 لا تستهلك strategy.cycle.collected — تعليقها: «400 وصفيّة»)
    return {"account_id": "A", "broker": "Raw Trading Ltd", "symbol": "NQ",
            "section_id": "400",
            "cycle_id": cycle_id, "status": "ok", "signal": "buy",
            "score": 80, "confidence": 90, "quality": "good",
            "state": "READY", "ready": True,
            "unified": {"state": "READY", "direction": 1.0, "strength": 80.0,
                        "weight": 20.0, "weight_effect": 20.0}}


@pytest.mark.asyncio
async def test_measure_1_and_2_chain_opens_and_publishes_with_sequence():
    """٦٢٢ → ٦١٣ → ١١٢ → ٤٥١: كل تِكّة تنشر لقطة decision.aggregated.state.

    (nq seal 2026-08-25: 451 v3.0.0 room model -- "opens a cycle" became
    "publishes a decision snapshot with decision_id dec:<cycle_id>"; a second
    tick publishes a SECOND snapshot with the new cycle identity, no
    superseded/deadline machinery exists any more.)
    """
    h = Harness(); await h.start()
    aggregated: list[dict] = []
    h.bus.subscribe("decision.aggregated.state", aggregated.append, subscriber="probe")
    await h.bus.publish(ANALYSIS_LIVE_EVENT, analysis_card(), publisher="150")
    await h.bus.publish(STRATEGY_LIVE_EVENT, strategy_card("pending"), publisher="400")
    cycle_id = await feed_622_style(h, sequence="1001")

    # المقياس ١: 451 ترك NO_CYCLES_YET وعدّاده > صفر
    health = await h.atoms[451].health_check()
    assert "NO_CYCLES_YET" not in (health.message or "")
    assert h.atoms[451]._ticks_seen >= 1
    # المقياس ٢: الحدث ظهر بهوية القرار الحتمية dec:<cycle_id>
    assert aggregated and aggregated[-1]["cycle_id"] == cycle_id
    assert aggregated[-1]["decision_id"] == "dec:" + cycle_id
    # تِكّة ثانية = لقطة ثانية بهوية الدورة الجديدة (لا "تجاوز" بعد اليوم)
    first_count = len(aggregated)
    second_cycle = await feed_622_style(h, sequence="1002",
                                        ts=1_800_000_001.0)
    assert len(aggregated) > first_count
    assert second_cycle != cycle_id
    assert aggregated[-1]["cycle_id"] == second_cycle
    assert aggregated[-1]["decision_id"] == "dec:" + second_cycle


@pytest.mark.asyncio
async def test_live_failure_repro_unnumbered_tick_still_opens_cycle():
    """عطل الجهاز الحيّ نفسه: 613 القديم كان يُسقط sequence — التِكّة تصل 451 بلا
    ترقيم. بالمحوّل القانوني تُفتح الدورة عبر سلسلة الاحتياط stamp@bid:ask."""
    h = Harness(); await h.start()
    aggregated: list[dict] = []
    h.bus.subscribe("decision.aggregated.state", aggregated.append, subscriber="probe")
    await h.bus.publish(ANALYSIS_LIVE_EVENT, analysis_card(), publisher="150")
    await h.bus.publish(STRATEGY_LIVE_EVENT, strategy_card("pending"), publisher="400")
    cycle_id = await feed_622_style(h, sequence=None)

    # (nq seal 2026-08-25: room model -- the unnumbered tick must PUBLISH a
    # decision snapshot carrying its fallback-chain identity, not "open" one.)
    assert h.atoms[451]._ticks_seen == 1, "التِكّة غير المرقّمة لم تنشر قرارًا"
    assert aggregated and aggregated[-1]["cycle_id"] == cycle_id
    assert aggregated[-1]["decision_id"] == "dec:" + cycle_id


@pytest.mark.asyncio
async def test_measure_3_and_4_chain_reaches_scoring_with_same_cycle_id():
    """السلسلة كاملة حتى decision.scored.state، وهوية الدورة واحدة من القسم
    المغذّي حتى 453 (المقياس ٤ — التوحّد الذي ذكرته الورقة مكسبًا قائمًا بذاته)."""
    h = Harness(); await h.start()
    scored: list[dict] = []
    aggregated: list[dict] = []
    h.bus.subscribe("decision.scored.state", scored.append, subscriber="probe")
    h.bus.subscribe("decision.aggregated.state", aggregated.append, subscriber="probe")

    await h.bus.publish(ANALYSIS_LIVE_EVENT, analysis_card(), publisher="150")
    await h.bus.publish(STRATEGY_LIVE_EVENT, strategy_card("pending"), publisher="400")
    cycle_id = await feed_622_style(h, sequence="2002")

    assert scored, "السلسلة لم تصل decision.scored.state"
    assert scored[-1]["cycle_id"] == cycle_id == aggregated[-1]["cycle_id"]
    # هوية المحوّل القانوني نفسها — القسم والقرار والدرجة على معرّف واحد
    assert scored[-1]["cycle_id"] == cycle_id


@pytest.mark.asyncio
async def test_measure_5_no_new_bus_errors_across_the_chain():
    """صفر أخطاء جديدة في الناقل عبر السلسلة كاملة (بديل قراءة السجلّ هنا)."""
    h = Harness(); await h.start()
    await h.bus.publish(ANALYSIS_LIVE_EVENT, analysis_card(), publisher="150")
    await feed_622_style(h, sequence="3003")
    await feed_622_style(h, sequence=None, ts=1_800_000_001.0)
    stats = h.bus.stats()
    assert not stats.get("error"), stats.get("error")
    assert not stats.get("timeout"), stats.get("timeout")
    # (nq seal 2026-08-25: EventBus 1.18.0 mailboxes announce overflow via
    # stats()["dropped"] -- a silent drop would be a new bus failure too.)
    assert not stats.get("dropped"), stats.get("dropped")


def test_determinism_same_tick_same_cycle_id():
    """شرط البناء ٢ قبل أن يبدأ: هوية الدورة حتميّة — نفس التِكّة مرّتين =
    نفس المعرّف، بمصدر مرقّم وغير مرقّم معًا."""
    base = {"account_id": "A", "broker": "BR", "symbol": "NQ",
            "bid": 100.0, "ask": 100.2, "price": 100.1,
            "timestamp": 5.0, "exchange_timestamp": 5.0}
    numbered = as_validated_tick({**base, "sequence": "9"})
    numbered_again = as_validated_tick({**base, "sequence": "9"})
    bare = as_validated_tick(base)
    bare_again = as_validated_tick(base)
    assert numbered["cycle_id"] == numbered_again["cycle_id"]
    assert bare["cycle_id"] == bare_again["cycle_id"]


@pytest.mark.asyncio
async def test_613_now_forwards_sequence_identity():
    """المسار (ب): 613 لا يُسقط sequence بعد اليوم — يمرّرها كما أرسلها 622."""
    h = Harness(); await h.start()
    forwarded: list[dict] = []
    h.bus.subscribe("market.tick", forwarded.append, subscriber="probe")
    await h.bus.publish("feed.ctrader.tick", {
        "account_id": "A", "broker": "BR", "symbol": "NQ", "provider": "CTRADER",
        "bid": 1.0, "ask": 1.2, "price": 1.1, "volume": 3,
        "timestamp": 7.0, "exchange_timestamp": 7.0, "sequence": "424242"},
        publisher="test_622")
    # (nq seal 2026-08-25: async bus -- drain before reading deliveries.)
    assert await h.bus.drain(timeout_s=10.0)
    assert forwarded and forwarded[-1].get("sequence") == "424242"
