import pytest
from tests.learning_test_support import make_atom


@pytest.mark.asyncio
async def test_outcome_preserves_model_evidence_and_snapshot():
    module, atom, bus = await make_atom(360)
    evidence = {"model_version": "v1", "direction": "buy",
                "feature_vector": [0.1] * 10}
    await atom._on_decision({"account_id": "A", "symbol": "NQ",
                             "cycle_id": "c1", "direction": "buy",
                             "model_evidence": evidence})
    await atom._on_outcome({"account_id": "A", "symbol": "NQ",
                            "cycle_id": "c1", "profit": 10})
    record = bus.payloads(module.EVENT_OUT)[-1]
    assert record["outcome"] == "buy"
    assert record["model_evidence"] == evidence
    assert record["training_eligible"] is True
    snapshot = await atom.snapshot()
    _, restored, _ = await make_atom(360)
    await restored.restore(snapshot)
    assert (await restored.snapshot())["records"] == 1


@pytest.mark.asyncio
async def test_unmatched_outcome_is_audited_not_trained():
    module, atom, bus = await make_atom(360)
    await atom._on_outcome({"account_id": "A", "symbol": "NQ",
                            "ticket": 7, "side": "buy", "profit": 2})
    record = bus.payloads(module.EVENT_OUT)[-1]
    assert record["decision_missing"] is True
    assert record["training_eligible"] is False
