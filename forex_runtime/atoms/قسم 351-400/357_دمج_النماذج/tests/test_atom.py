import pytest
from shared.probability_contract import BASE_MODEL_IDS, EQUAL_MODEL_WEIGHT
from tests.learning_test_support import make_atom, manifest_config


@pytest.mark.asyncio
async def test_weighted_merge_uses_only_ready_weight():
    module, atom, bus = await make_atom(357, manifest_config(357))
    for index, model_id in enumerate(BASE_MODEL_IDS):
        await atom._on_model({
            "account_id": "A", "broker": "B", "symbol": "NQ",
            "cycle_id": "tick-1", "period_start": "t1", "sequence": 10,
            "id": model_id, "model_id": model_id,
            "direction": 100 if index < 4 else -100 if index < 6 else 0,
            "strength": 80, "confidence": 80, "probability": .7,
            "current_depth": 90, "required_depth": 60,
            "weight": EQUAL_MODEL_WEIGHT, "weight_applied": EQUAL_MODEL_WEIGHT,
            "ready": True,
        })
    merged = bus.payloads(module.EVENT_OUT)[-1]
    assert merged["ready"] is True
    assert merged["active_weight"] == pytest.approx(100.0, abs=1e-4)
    assert merged["direction"] > 0
    assert merged["weight"] == 0


@pytest.mark.asyncio
async def test_incomplete_cycles_are_bounded_not_a_leak():
    """v2.1.0 (ported from atom 359's own fix for the identical shape):
    a cycle missing even one of the seven models never completes and
    never gets popped from self._cycles by _merge -- before this fix
    there was no other eviction path, so self._cycles grew without bound
    under live tick-period cycles. Feed far more distinct INCOMPLETE
    cycles than the cap and confirm the oldest ones get evicted, counted,
    never silently."""
    module, atom, bus = await make_atom(357, manifest_config(357))
    cap = module._MAX_OPEN_CYCLES
    total = cap + 50
    for i in range(total):
        # عمداً موديل واحد فقط لكل دورة -- لا تكتمل أبداً، فلا تُطرَح
        # طبيعياً عبر _merge؛ الحدّ وحده يمنع النمو.
        await atom._on_model({
            "account_id": "A", "broker": "B", "symbol": "NQ",
            "cycle_id": f"tick-{i}", "id": BASE_MODEL_IDS[0],
            "model_id": BASE_MODEL_IDS[0], "direction": 0, "strength": 0,
            "confidence": 0, "probability": 0, "current_depth": 0,
            "weight": EQUAL_MODEL_WEIGHT, "weight_applied": EQUAL_MODEL_WEIGHT,
            "ready": True,
        })
    assert len(atom._cycles) <= cap, ("النمو تجاوز الحدّ -- التسريب لم"
                                      " يُصلَح: %d" % len(atom._cycles))
    assert atom._evicted == total - cap, atom._evicted
    # الأحدث بقيت (الأقدم طُرحت) -- ترتيب الطرح صحيح، لا عشوائي.
    assert f"tick-{total - 1}" in atom._cycles
    assert "tick-0" not in atom._cycles
