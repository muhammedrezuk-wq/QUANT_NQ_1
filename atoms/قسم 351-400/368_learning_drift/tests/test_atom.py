import pytest
from tests.learning_test_support import make_atom


@pytest.mark.asyncio
async def test_rolling_drift_requests_one_rollback_per_cooldown():
    module, atom, bus = await make_atom(368, {
        "min_samples": 2, "min_accuracy": 0.6, "window_size": 3,
        "rollback_cooldown_seconds": 3600})
    await atom._on_active({"active": True, "model_version": "v"})
    miss = {"model_version": "v", "model_direction": "buy",
            "outcome": "sell", "training_eligible": True}
    await atom._on_outcome(miss)
    await atom._on_outcome(miss)
    await atom._on_outcome(miss)
    assert len(bus.payloads(module.EVENT_ROLLBACK)) == 1
    assert bus.payloads(module.EVENT_STATE)[-1]["status"] == "DRIFT"
    snapshot = await atom.snapshot()
    _, restored, _ = await make_atom(368, {
        "min_samples": 2, "min_accuracy": 0.6, "window_size": 3,
        "rollback_cooldown_seconds": 3600})
    await restored.restore(snapshot)
    assert (await restored.health_check()).details["updates"] == 3
