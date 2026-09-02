import pytest
from shared.learning_model import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, schema_hash
from tests.learning_test_support import make_atom


@pytest.mark.asyncio
async def test_feature_contract_is_strict_and_complete():
    module, atom, bus = await make_atom(362)
    evidence = {"feature_schema_version": FEATURE_SCHEMA_VERSION,
                "feature_schema_hash": schema_hash(),
                "feature_names": list(FEATURE_NAMES),
                "feature_vector": [0.1] * len(FEATURE_NAMES)}
    await atom._on_sample({"sample_id": "s", "record": {
        "outcome": "buy", "model_evidence": evidence}})
    payload = bus.payloads(module.EVENT_OUT)[-1]
    assert payload["feature_vector"] == evidence["feature_vector"]
    assert payload["feature_schema_hash"] == schema_hash()


@pytest.mark.asyncio
async def test_bad_schema_is_rejected_without_fake_zeroes():
    module, atom, bus = await make_atom(362)
    await atom._on_sample({"sample_id": "bad", "record": {"outcome": "buy"}})
    assert not bus.payloads(module.EVENT_OUT)
    assert (await atom.health_check()).details["rejected"] == 1
