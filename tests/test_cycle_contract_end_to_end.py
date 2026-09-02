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


UNIT_EVENTS = dict(zip(
    ("trend", "momentum", "volatility", "volume", "spread",
     "candle", "gap", "session", "time", "velocity", "acceleration",
     "volume_quality", "noise", "correlation", "relative_strength"),
    ("analysis.trend.state", "analysis.momentum.state", "analysis.volatility.state",
     "analysis.volume.state", "analysis.spread.state", "analysis.candle.state",
     "analysis.gap.state", "analysis.session.state", "analysis.time.state",
     "analysis.velocity.state", "analysis.acceleration.state",
     "analysis.volume_quality.state", "analysis.noise.state",
     "analysis.correlation.state", "analysis.relative_strength.state")))

STRATEGY_LIVE = "strategy.section.live"


class Logger:
    def __getattr__(self, _name: str) -> Any:
        return lambda *args, **kwargs: None


def load(atom_id: int):
    folder = next((ATOM_ROOT).glob(f"{atom_id}_*"))
    sys.path.insert(0, str(folder))
    try:
        spec = importlib.util.spec_from_file_location(f"cycle_contract_{atom_id}", folder / "atom.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def _approve_merge_parameters(tmp_dir: Path) -> None:
    """اعتماد معاملات محرك الدمج بالطريق القانوني (قرار مالك بمصدر OWNER) —
    بوابة الحوكمة صادقة: بلا اعتماد تبقى بطاقة 166 provisional خارج القرار."""
    import os
    import shared.parameter_registry as pr
    db = tmp_dir / "params.db"
    os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = str(db)
    store = pr.ParameterRegistry()
    for row in store.all():
        if row["status"] != pr.STATUS_APPROVED:
            store.approve(row["name"], value=float(row["value"]),
                          source=pr.SOURCE_OWNER, approved_by="owner",
                          command_id=f"cycle-contract-{row['name']}", approved_at=1.0)
    pr.refresh_gate()


@pytest.mark.asyncio
async def test_one_cycle_keeps_identity_through_scoring(tmp_path: Path) -> None:
    _approve_merge_parameters(tmp_path)
    """دورة واحدة تحفظ هويتها من التِكّة حتى الدرجة — على الذرّات الحقيقيّة.

    أعيدت كتابتها على مسار التِكّة ٢٠٢٦-٠٨-٢٣ (كانت من عهد الشموع ثم عُلّمت
    skip مؤقّتًا). السلسلة كاملة حقيقيّة: ١٥ محلّلًا ← ١٥٠ ← ١٦٦ (بطاقة القسم)
    ← ٤٥١ (تفتح الدورة من التِكّة) ← ٤٥٢ ← ٤٥٣ — والهوية واحدة من المحوّل
    القانوني حتى decision.scored.state (مقياس ٤ من ورقة X).
    """
    bus = EventBus()
    scored: list[dict] = []
    aggregated: list[dict] = []
    modules = {atom_id: load(atom_id) for atom_id in (150, 166, 451, 452, 453)}
    configs = {
        150: {"timeout_seconds": 5.0, "live_flush_timeout_s": 1.0},
        166: {"section_weight": 100.0 / 6.0, "agree_threshold": 0.5,
              "live_stale_after_s": 5.0, "fast_weight": 55.0, "slow_weight": 45.0},
        # (nq seal 2026-08-25: 451 v3.0.0 room model -- require_same_cycle no
        # longer exists; config takes only expected_families.)
        451: {"expected_families": ["150", "400"]},
        452: {"low_quality_factor": 0.5, "min_confidence": 0.0},
        453: {"directional_weight": 1.0, "context_weight": 0.0556,
              "min_participation": 0.2, "directional_sources": ["400"]},
    }
    atoms: dict[int, Any] = {}
    for atom_id, module in modules.items():
        atom = module.Atom()
        atoms[atom_id] = atom
        await atom.initialize(AtomContext(atom_id, configs[atom_id], Logger(),
            lambda name, payload, aid=atom_id: bus.publish(name, payload, publisher=str(aid)),
            lambda name, handler, aid=atom_id: bus.subscribe(name, handler, subscriber=str(aid))))
        await atom.start()
    bus.subscribe("decision.scored.state", scored.append, subscriber="proof")
    bus.subscribe("decision.aggregated.state", aggregated.append, subscriber="proof")

    ts = 1_800_000_000.0
    # ١) المحلّلات الخمسة عشر تنشر حالتها الحيّة على نفس التِكّة
    for unit, event in UNIT_EVENTS.items():
        await bus.publish(event, {
            "account_id": "A", "broker": "BR", "symbol": "NQ",
            "analyzer_id": unit, "sequence": 1,
            "analysis_state": "DECISION_READY", "ready": True,
            "weight": 100.0 / 15.0, "confidence": 70.0,
            "current_depth": 80.0, "required_depth": 60.0,
            "confidence_threshold": 50.0,
            "direction": 60.0, "score": 60.0, "quality": "good",
            "source_timestamp": ts, "timestamp": ts}, publisher=unit)
    # (nq seal 2026-08-25: EventBus 1.18.0 enqueues -- drain() waits for the
    # 150 -> 166 -> section-card cascade before the next stage publishes.)
    assert await bus.drain(timeout_s=10.0)
    # ٢) بطاقة القسم الوصفيّة (عائلة 400) قبل التِكّة — العقد الحيّ
    await bus.publish(STRATEGY_LIVE, {
        "account_id": "A", "broker": "BR", "symbol": "NQ", "section_id": "400",
        "status": "ok", "signal": "buy", "score": 80, "confidence": 90,
        "quality": "good", "state": "READY", "ready": True,
        "unified": {"state": "READY", "direction": 1.0, "strength": 80.0,
                    "weight": 16.6667, "weight_effect": 16.6667}}, publisher="400")
    assert await bus.drain(timeout_s=10.0)
    # ٣) التِكّة الصالحة تنشر لقطة القرار من غرفة الأدلّة الطازجة
    # (nq seal 2026-08-25: 451 v3.0.0 room model -- the tick PUBLISHES a
    # snapshot; "complete" now means every expected family has FRESH evidence.)
    tick = {"account_id": "A", "broker": "BR", "symbol": "NQ",
            "timeframe": "tick", "sequence": "1",
            "timestamp": ts, "price": 20050.0}
    await bus.publish("market.tick.validated", tick, publisher="112")
    assert await bus.drain(timeout_s=10.0)

    # ٤) المعرّف واحد من المصدر المشترك حتى الدرجة (§٣ + مقياس ٤)
    cycle_id = as_validated_tick(tick)["cycle_id"]
    assert aggregated, "لم تُنشر دورة قرار مجمّعة"
    assert aggregated[-1]["cycle_id"] == cycle_id
    # (nq seal 2026-08-25: decision_id is born at 451 as dec:<cycle_id>.)
    assert aggregated[-1]["decision_id"] == "dec:" + cycle_id
    assert aggregated[-1]["complete"] is True
    assert aggregated[-1]["cycle_status"] == "complete"
    assert scored, "السلسلة لم تصل decision.scored.state"
    assert scored[-1]["cycle_id"] == cycle_id
    # ٥) الدليل التحليلي حاضر مرّة واحدة بعائلة 150 (بناء ٤ — طريق واحد)
    families = {str(row.get("source", "")).split(":", 1)[0].removesuffix("-live")
                for row in aggregated[-1]["evidence"]}
    assert "150" in families and "166" not in families
