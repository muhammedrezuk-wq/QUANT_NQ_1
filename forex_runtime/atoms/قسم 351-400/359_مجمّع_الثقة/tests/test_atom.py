import pytest
from shared.probability_contract import BASE_MODEL_IDS, EQUAL_MODEL_WEIGHT
from tests.learning_test_support import make_atom, manifest_config


@pytest.mark.asyncio
async def test_confidence_panel_has_depth_threshold_and_coverage():
    module, atom, bus = await make_atom(359, manifest_config(359))
    for model_id in BASE_MODEL_IDS:
        await atom._on_model({
            "account_id": "A", "broker": "B", "symbol": "NQ",
            "cycle_id": "tick-1", "period_start": "t1", "sequence": 10,
            "id": model_id, "model_id": model_id,
            "confidence": 80, "probability": .7, "current_depth": 90,
            "weight": EQUAL_MODEL_WEIGHT, "weight_applied": EQUAL_MODEL_WEIGHT,
            "ready": True,
        })
    card = bus.payloads(module.EVENT_OUT)[-1]
    assert card["ready"] is True
    assert card["coverage"] == 100
    assert card["confidence"] == 80
    assert card["confidence_threshold"] == 60
