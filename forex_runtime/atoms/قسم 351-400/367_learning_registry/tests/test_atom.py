import pytest
from tests.learning_test_support import artifact, make_atom


@pytest.mark.asyncio
async def test_registry_verifies_persists_and_runs_shadow_inference():
    module, atom, bus = await make_atom(367, {"mode": "shadow",
                                               "rollback_cooldown_seconds": 0})
    model = artifact()
    await atom._on_selected(model)
    assert not bus.payloads(module.EVENT_ACTIVE)
    await atom._on_persisted({"model_name": module.MODEL_NAME,
                              "version": model["model_version"]})
    assert bus.payloads(module.EVENT_ACTIVE)[-1]["mode"] == "shadow"
    await atom._on_tick({"account_id": "A", "broker": "B", "symbol": "NQ",
                           "timeframe": "1m", "open": 100, "high": 102,
                           "low": 99, "close": 101, "volume": 10})
    evidence = bus.payloads(module.EVENT_EVIDENCE)[-1]
    assert evidence["state"] == "SHADOW"
    assert evidence["influence_weight"] == 0.0
    assert abs(evidence["p_buy"] + evidence["p_sell"] + evidence["p_neutral"] - 1) < 1e-9


@pytest.mark.asyncio
async def test_registry_rejects_tampered_artifact():
    module, atom, bus = await make_atom(367, {"mode": "shadow",
                                               "rollback_cooldown_seconds": 0})
    model = artifact()
    model["weights"][0][0] += 1
    await atom._on_selected(model)
    assert not bus.payloads(module.EVENT_PERSIST)
    assert (await atom.health_check()).details["invalid"] == 1


@pytest.mark.asyncio
async def test_load_response_not_found_does_not_crash():
    """Item 14/27 of the 27-atom review ("crash reading payload['data'] if
    the model is not found"): 706's response omits "data" entirely when
    found=False -- {"request_id":..., "model_name":..., "found": False}.
    _on_load_response was already guarded (`or not payload.get("found")`
    short-circuits before any data access) but had zero coverage on this
    path. Locks the guarantee in rather than leaving it unverified."""
    module, atom, bus = await make_atom(367, {"mode": "shadow",
                                               "rollback_cooldown_seconds": 0})
    await atom._on_load_response({"request_id": "registry-load",
                                  "model_name": module.MODEL_NAME, "found": False})
    assert atom._active is None
    assert not bus.payloads(module.EVENT_ACTIVE)
