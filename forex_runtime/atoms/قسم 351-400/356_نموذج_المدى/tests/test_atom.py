import json
import pytest
from tests.learning_test_support import make_atom, manifest_config, validated_tick

@pytest.mark.asyncio
async def test_live_tick_contract_and_snapshot():
    module, atom, bus = await make_atom(356, manifest_config(356))
    for i in range(70):
        price = 100.0 + (0.03 if i % 2 else -0.03)
        await atom._on_tick(validated_tick(i, price=price))
    rows = bus.payloads("probability.range.state")
    assert len(rows) == 70
    last = rows[-1]
    assert last["timeframe"] == "tick"
    assert last["analysis_mode"] == "live_tick"
    for key in ("direction", "strength", "confidence", "weight", "weight_applied",
                "current_depth", "required_depth", "confidence_threshold",
                "strength_threshold", "ratio", "ready", "analysis_state"):
        assert key in last
    assert 0 <= last["confidence"] <= 100
    assert 0 <= last["current_depth"] <= 100
    json.dumps(await atom.snapshot(), allow_nan=False)
