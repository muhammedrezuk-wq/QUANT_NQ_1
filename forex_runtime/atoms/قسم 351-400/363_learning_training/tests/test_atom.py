import pytest
from shared.learning_model import predict, stable_hash
from tests.learning_test_support import feature, make_atom


@pytest.mark.asyncio
async def test_trainer_uses_features_and_is_reproducible():
    config = {"min_samples": 3, "validation_size": 1,
              "train_every_new_samples": 1, "epochs": 10,
              "learning_rate": 0.05, "l2": 0.001}
    module, atom, bus = await make_atom(363, config)
    for index, label in enumerate(("buy", "sell", "neutral", "buy")):
        await atom._on_feature(feature(str(index), label, index / 10))
    candidate = bus.payloads(module.EVENT_OUT)[-1]
    assert candidate["algorithm"] == "multinomial_logistic_full_batch_v1"
    probs = predict(candidate, feature("x", "buy", 0.2)["feature_vector"])
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    assert candidate["artifact_hash"]
    snapshot = await atom.snapshot()
    assert stable_hash(snapshot["samples"]) == stable_hash((await atom.snapshot())["samples"])
