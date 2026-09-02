import pytest
from tests.learning_test_support import make_atom


@pytest.mark.asyncio
async def test_dataset_deduplicates_and_restores():
    module, atom, bus = await make_atom(361)
    record = {"decision_id": "d", "outcome_event_id": "o", "account_id": "A",
              "broker": "B", "symbol": "NQ", "direction": "buy",
              "outcome": "buy", "realized_pnl": 1, "training_eligible": True}
    await atom._on_record(record)
    await atom._on_record(record)
    assert len(bus.payloads(module.EVENT_OUT)) == 1
    assert (await atom.health_check()).details["duplicates"] == 1
    snapshot = await atom.snapshot()
    _, restored, restored_bus = await make_atom(361)
    await restored.restore(snapshot)
    await restored._on_record(record)
    assert not restored_bus.payloads(module.EVENT_OUT)
