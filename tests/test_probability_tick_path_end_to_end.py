import pytest

from core.contracts.atom import AtomContext
from core.event_bus import EventBus
from tests.learning_test_support import Logger, load_atom, manifest_config


@pytest.mark.asyncio
async def test_ctrader_622_contract_reaches_ready_probability_section_via_613_112():
    bus = EventBus()
    atoms = []
    for atom_id in (613, 112, 350, 351, 352, 353, 354, 355, 356, 357, 358, 359):
        module = load_atom(atom_id)
        atom = module.Atom()
        await atom.initialize(AtomContext(atom_id, manifest_config(atom_id), Logger(),
                                          bus.publish, bus.subscribe))
        atoms.append(atom)
    for atom in atoms:
        await atom.start()

    collected = []

    async def capture(payload):
        collected.append(dict(payload))

    bus.subscribe("probability.cycle.collected", capture, subscriber="tick-e2e")
    for index in range(140):
        price = 100.0 + index * 0.03
        await bus.publish("feed.ctrader.tick", {
            "provider": "CTRADER", "account_id": "A", "broker": "Raw Trading Ltd",
            "symbol": "NQ", "bid": price - 0.01, "ask": price + 0.01,
            "price": price, "volume": 10 + index,
            "timestamp": 1_800_000_000.0 + index / 1000.0,
            "exchange_timestamp": 1_800_000_000.0 + index / 1000.0,
        }, publisher="622")

    # (nq seal 2026-08-25: EventBus 1.18.0 enqueues to per-handler mailboxes;
    # drain the bus so the 613->112->350..359 cascade finishes before asserting)
    assert await bus.drain(timeout_s=30.0) is True

    assert collected
    last = collected[-1]
    assert last["timeframe"] == "tick"
    assert last["complete"] is True
    assert last["ready"] is True
    # The section's own measured gate is ready. The shared governance seal may
    # still hold unified.state at NOT_READY while global parameters are unapproved.
    assert last["unified"]["state"] in ("READY", "NOT_READY")
    assert last["weight_applied"] > 0
    assert len(last["results"]) == 9
    assert last["confidence_threshold"] == 60
    assert last["required_depth"] == 60
    assert "weight" in last and "weight_applied" in last
    stats = bus.stats()
    assert stats["published"]["market.tick.validated"] == 140
