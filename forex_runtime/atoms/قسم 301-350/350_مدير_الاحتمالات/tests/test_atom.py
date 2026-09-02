import pytest
from shared.tick_contract import as_validated_tick
from tests.learning_test_support import make_atom, manifest_config, validated_tick


async def _deliver_all(module, atom, canonical):
    for model_id in module.EXPECTED_ORDER:
        payload = {
            "account_id": canonical["account_id"], "broker": canonical["broker"],
            "symbol": canonical["symbol"], "cycle_id": canonical["cycle_id"],
            "period_start": canonical["period_start"], "timeframe": "tick",
            "id": model_id, "model_id": model_id,
            "direction": 70, "strength": 80, "confidence": 80,
            "current_depth": 90, "ready": True,
        }
        await atom._on_unit(payload, model_id)


@pytest.mark.asyncio
async def test_units_open_their_own_cycle_and_complete():
    module, atom, bus = await make_atom(350, manifest_config(350))
    tick = validated_tick(1, price=100.0)
    canonical = as_validated_tick(tick)
    # إقفال 350: لا مشترك بالتِكّة — أول وحدة تفتح دورتها بنفسها
    await _deliver_all(module, atom, canonical)
    card = bus.payloads(module.EVENT_OUT)[-1]
    assert card["complete"] is True
    assert card["ready"] is True
    assert card["weight"] == pytest.approx(100 / 6, abs=1e-5)
    assert card["weight_applied"] == card["weight"]
    assert bus.payloads(module.EVENT_LIVE)
    assert not atom._cycles, "الدورة أُغلقت بعد الاكتمال"


@pytest.mark.asyncio
async def test_readiness_pct_gradual_on_card():
    module, atom, bus = await make_atom(350, manifest_config(350))
    tick = validated_tick(1, price=100.0)
    canonical = as_validated_tick(tick)
    await _deliver_all(module, atom, canonical)
    card = bus.payloads(module.EVENT_LIVE)[-1]
    assert card["readiness_pct"] == 100.0, card["readiness_pct"]
    # v3.2.0: اللوحة مُقنَّنة — الدفعة تنشر مرّة وتُعلَّم dirty، ونبضة
    # الثانية تفرّغ المؤجَّل، فلا لوحة لكل وحدة بعد اليوم.
    await atom._on_time({"official_time": 2.0})
    h = await atom.health_check()
    assert h.details["panel_emitted"] >= 1
    assert not atom._panel_dirty, "النبضة فرّغت كل المؤجَّل"


@pytest.mark.asyncio
async def test_units_panel_self_declared():
    module, atom, bus = await make_atom(350, manifest_config(350))
    tick = validated_tick(1, price=100.0)
    canonical = as_validated_tick(tick)
    await _deliver_all(module, atom, canonical)
    # v3.2.0: اللوحة مُقنَّنة — نبضة الثانية تفرّغ المؤجَّل بالقوة.
    await atom._on_time({"official_time": 2.0})
    panels = bus.payloads(module.EVENT_PANEL)
    assert panels, "لوحة الوحدات لم تُنشر"
    p1 = panels[-1]
    assert p1["present"] == 9 and not p1["missing"]
    hurst = next(r for r in p1["units"] if r["id"] == "hurst")
    assert hurst["present"] is True and hurst["deliveries"] == 1
    assert hurst["confidence"] == 80


@pytest.mark.asyncio
async def test_health_counts_visible():
    module, atom, bus = await make_atom(350, manifest_config(350))
    h0 = await atom.health_check()
    assert h0.details["opened"] == 0
    tick = validated_tick(1, price=100.0)
    canonical = as_validated_tick(tick)
    await _deliver_all(module, atom, canonical)
    h1 = await atom.health_check()
    assert h1.details["opened"] == 1 and h1.details["forwarded"] == 1
    assert h1.details["units_tracked"] == 9
