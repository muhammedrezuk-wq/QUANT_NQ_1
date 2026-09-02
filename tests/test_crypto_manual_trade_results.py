from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from governance import server
from governance.control_adapter import build_control_event_publisher


ROOT = Path(__file__).resolve().parents[1]
ATOM_PATH = ROOT / "atoms_crypto" / "قسم 2251-2300" / "2275_محرك_المخاطر" / "atom.py"


def _load_atom() -> ModuleType:
    spec = importlib.util.spec_from_file_location("crypto_atom_2275_manual_results", ATOM_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Context:
    def __init__(self) -> None:
        self.config = {
            "reference_equity_usd": 300.0,
            "risk_pct_per_trade": 0.5,
            "daily_loss_halt_pct": 2.0,
            "max_consecutive_losses": 3,
        }
        self.subscriptions: dict[str, Any] = {}
        self.events: list[tuple[str, dict]] = []

    def subscribe(self, name: str, callback: Any) -> None:
        self.subscriptions[name] = callback

    async def publish(self, name: str, payload: dict) -> None:
        self.events.append((name, payload))


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    async def publish(self, name: str, payload: dict, *, publisher: str) -> None:
        self.events.append((name, payload, publisher))


def _new_atom() -> tuple[Any, _Context]:
    module = _load_atom()
    atom = module.Atom()
    context = _Context()
    asyncio.run(atom.initialize(context))
    asyncio.run(atom.start())
    return atom, context


def test_manual_loss_results_change_2275_and_halt_new_recommendations() -> None:
    atom, context = _new_atom()
    for trade_id in ("deal-001", "deal-002", "deal-003"):
        asyncio.run(atom._on_trade({"trade_id": trade_id, "pnl_usd": -1.0}))

    assert atom._daily["pnl_usd"] == -3.0
    assert atom._daily["consecutive_losses"] == 3
    assert atom._daily_halted() is True

    asyncio.run(atom._on_candidate({"symbol": "BTC_USDT", "grade": "A"}))
    assert context.events[-1][0] == "crypto.decision.sized_entry.state"
    assert context.events[-1][1]["approved"] is False
    assert context.events[-1][1]["reason"] == "DAILY_RISK_HALTED"


def test_daily_loss_pct_halts_even_before_three_losses() -> None:
    atom, _ = _new_atom()
    asyncio.run(atom._on_trade({"trade_id": "deal-limit", "pnl_usd": -6.0}))
    assert atom._daily["consecutive_losses"] == 1
    assert atom._daily_halted() is True


def test_trade_id_deduplication_survives_snapshot_and_zero_is_not_a_loss() -> None:
    atom, _ = _new_atom()
    asyncio.run(atom._on_trade({"trade_id": "deal-flat", "pnl_usd": 0.0}))
    asyncio.run(atom._on_trade({"trade_id": "deal-loss", "pnl_usd": -2.5}))
    asyncio.run(atom._on_trade({"trade_id": "deal-loss", "pnl_usd": -2.5}))
    assert atom._daily["pnl_usd"] == -2.5
    assert atom._daily["consecutive_losses"] == 1
    assert atom._duplicate_trade_results == 1

    state = asyncio.run(atom.snapshot())
    restored, _ = _new_atom()
    asyncio.run(restored.restore(state))
    asyncio.run(restored._on_trade({"trade_id": "deal-loss", "pnl_usd": -2.5}))
    assert restored._daily["pnl_usd"] == -2.5
    assert restored._daily["consecutive_losses"] == 1
    assert restored._duplicate_trade_results == 2


@pytest.mark.parametrize("body", [
    {"trade_id": "x", "symbol": "BTC_USDT", "pnl_usd": -1.0},
    {"trade_id": "deal-x", "symbol": "BTC-USDT", "pnl_usd": -1.0},
    {"trade_id": "deal-x", "symbol": "BTC_USDT", "pnl_usd": True},
    {"trade_id": "deal-x", "symbol": "BTC_USDT", "pnl_usd": float("inf")},
    {"trade_id": "deal-x", "symbol": "BTC_USDT", "pnl_usd": -1.0, "surprise": 1},
])
def test_manual_result_contract_rejects_malformed_payload(body: dict) -> None:
    with pytest.raises(ValueError):
        server._manual_trade_payload(body)


def test_server_requires_two_steps_audits_delivers_and_rejects_duplicate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(server, "MARKET", "crypto")
    monkeypatch.setattr(server, "MANUAL_TRADE_RESULTS_DB", tmp_path / "manual-results.db")
    server._PENDING_MANUAL_TRADE_RESULTS.clear()
    calls: list[tuple[str, str, dict]] = []

    def fake_core_request(path: str, *, method: str = "GET", body: bytes | None = None, **_: Any):
        calls.append((path, method, json.loads((body or b"{}").decode("utf-8"))))
        return 200, b'{"accepted":true}'

    monkeypatch.setattr(server, "core_request", fake_core_request)
    payload = {
        "trade_id": "mexc-deal-9001", "symbol": "BTC_USDT",
        "pnl_usd": -4.25, "note": "net after fees", "operator": "ASMAR",
    }

    first_status, first = server.manual_trade_result(payload)
    assert first_status == 200 and first["stage"] == "confirm"
    assert calls == []

    second_status, second = server.manual_trade_result({**payload, "confirm": first["token"]})
    assert second_status == 200 and second["stage"] == "delivered"
    assert len(calls) == 1
    assert calls[0][0:2] == ("/api/events", "POST")
    assert calls[0][2]["name"] == "platform.trade_event"
    assert calls[0][2]["payload"]["pnl_usd"] == -4.25
    assert calls[0][2]["payload"]["trade_id"] == "mexc-deal-9001"
    assert isinstance(calls[0][2]["payload"]["pnl_usd"], float)

    duplicate_status, duplicate = server.manual_trade_result(payload)
    assert duplicate_status == 409
    assert duplicate["error"] == "DUPLICATE_TRADE_ID"
    assert len(calls) == 1

    history = server.manual_trade_results()
    assert history["available"] is True
    assert history["results"][0]["trade_id"] == "mexc-deal-9001"
    assert history["results"][0]["delivery_status"] == "DELIVERED"


def test_uncertain_retry_cannot_change_payload_for_same_trade_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(server, "MARKET", "crypto")
    monkeypatch.setattr(server, "MANUAL_TRADE_RESULTS_DB", tmp_path / "uncertain.db")
    server._PENDING_MANUAL_TRADE_RESULTS.clear()
    monkeypatch.setattr(server, "core_request", lambda *_args, **_kwargs: (503, b"offline"))
    payload = {"trade_id": "same-id", "symbol": "BTC_USDT", "pnl_usd": -2.0}
    _, prepared = server.manual_trade_result(payload)
    status, result = server.manual_trade_result({**payload, "confirm": prepared["token"]})
    assert status == 502 and result["error"] == "CORE_DELIVERY_UNCONFIRMED"

    status, result = server.manual_trade_result({**payload, "pnl_usd": 20.0})
    assert status == 409 and result["error"] == "TRADE_ID_PAYLOAD_MISMATCH"


def test_dashboard_confirmation_reaches_2275_and_halts_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    atom, context = _new_atom()
    monkeypatch.setattr(server, "MARKET", "crypto")
    monkeypatch.setattr(server, "MANUAL_TRADE_RESULTS_DB", tmp_path / "e2e-results.db")
    server._PENDING_MANUAL_TRADE_RESULTS.clear()

    def deliver_to_atom(_path: str, *, body: bytes | None = None, **_: Any):
        envelope = json.loads((body or b"{}").decode("utf-8"))
        assert envelope["name"] == "platform.trade_event"
        asyncio.run(atom._on_trade(envelope["payload"]))
        return 200, b'{"accepted":true}'

    monkeypatch.setattr(server, "core_request", deliver_to_atom)
    for index in range(3):
        payload = {
            "trade_id": f"e2e-deal-{index}", "symbol": "BTC_USDT",
            "pnl_usd": -1.0, "operator": "ASMAR",
        }
        status, prepared = server.manual_trade_result(payload)
        assert status == 200 and prepared["stage"] == "confirm"
        status, delivered = server.manual_trade_result({**payload, "confirm": prepared["token"]})
        assert status == 200 and delivered["stage"] == "delivered"

    duplicate, _ = server.manual_trade_result({
        "trade_id": "e2e-deal-0", "symbol": "BTC_USDT", "pnl_usd": -1.0,
    })
    assert duplicate == 409
    assert atom._daily["consecutive_losses"] == 3
    asyncio.run(atom._on_candidate({"symbol": "BTC_USDT", "grade": "A"}))
    assert context.events[-1][1]["reason"] == "DAILY_RISK_HALTED"


def test_manual_trade_event_is_crypto_only_at_outer_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _Bus()
    crypto = build_control_event_publisher("crypto", bus)
    asyncio.run(crypto("platform.trade_event", {"trade_id": "one", "pnl_usd": -1.0}))
    assert bus.events[-1][0] == "platform.trade_event"

    forex = build_control_event_publisher("forex", bus)
    with pytest.raises(PermissionError):
        asyncio.run(forex("platform.trade_event", {"trade_id": "one", "pnl_usd": -1.0}))

    monkeypatch.setattr(server, "MARKET", "forex")
    status, _ = server.manual_trade_result({
        "trade_id": "one", "symbol": "BTC_USDT", "pnl_usd": -1.0,
    })
    assert status == 404
