from __future__ import annotations

import sys

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)
CRYPTO_ROOT = RegistryAtomRoot(ROOT, scope="crypto")



def load_atom(folder: str, module_name: str):
    path = CRYPTO_ROOT / folder / "atom.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


@pytest.mark.asyncio
async def test_universe_filters_and_feed_preserves_full_ticker_vocabulary() -> None:
    universe_module = load_atom("1001_مدير_كون_الأصول", "phase_a_universe")
    feed_module = load_atom("1002_تغذية_السوق", "phase_a_feed")

    universe = universe_module.Atom()
    universe._core_min_usd = 50_000_000
    universe._outer_min_usd = 10_000_000
    universe._max_spread_ticks = 3
    universe._max_range_pct = 20
    universe._manual_overrides = {}

    good = {
        "symbol": "BTC_USDT", "bid": 100.0, "ask": 100.1, "last_price": 100.0,
        "amount24_usd": 60_000_000, "high24": 105.0, "low24": 95.0,
        "daily_range_pct": 10.0, "price_tick_size": 0.1, "spread_ticks": 1.0,
        "asset_class": "NATIVE_CRYPTO", "asset_class_source": "test",
        "market_segment": "futures_usdt", "quote_asset": "USDT", "settle_asset": "USDT",
    }
    assert universe._evaluate(good) == []

    bad = dict(good, symbol="XAU_USDT", amount24_usd=1_000_000,
               asset_class="NON_CRYPTO", market_segment="other",
               quote_asset="USDC", settle_asset="USDC")
    reasons = universe._evaluate(bad)
    assert "LIQUIDITY_BELOW_OUTER" in reasons
    assert "NOT_FUTURES_USDT" in reasons
    assert "NON_CRYPTO" in reasons

    output: list[tuple[str, dict]] = []
    feed = feed_module.Atom()

    class Context:
        logger = Logger()

        async def publish(self, name, payload):
            output.append((name, payload))

    feed._context = Context()
    feed._running = True
    await feed._on_ticker_batch({
        "universe_version": "U-test-1",
        "membership": {"universe_version": "U-test-1", "core": [good], "outer": []},
        "rows": [good],
    })
    tick = next(payload for name, payload in output if name == "crypto.feed.tick")
    assert tick["symbol"] == "BTC_USDT"
    assert tick["ring"] == "core"
    assert tick["amount24_usd"] == 60_000_000
    assert "price_tick_size" in tick and "daily_range_pct" in tick


@pytest.mark.asyncio
async def test_rotation_requires_resilience_and_protects_open_position() -> None:
    universe_module = load_atom("1001_مدير_كون_الأصول", "phase_a_rotation")
    universe = universe_module.Atom()
    universe._entry_resilience_s = 100.0
    universe._exit_failure_s = 100.0
    universe._membership_state = {}
    universe._manual_overrides = {}
    universe._open_positions = lambda: {"BTC_USDT"}
    row = {
        "symbol": "BTC_USDT", "ring": "core", "amount24_usd": 60_000_000,
        "spread_ticks": 1.0, "asset_class": "NATIVE_CRYPTO",
        "daily_range_pct": 5.0,
    }
    rotation, core, outer = universe._rotate([row], [], [], 1000.0)
    assert not core and rotation["enter_candidates"] == ["BTC_USDT"]
    universe._membership_state["BTC_USDT"] = {
        "status": "CORE_ACTIVE", "ring": "core", "qualified_since": 1.0,
    }
    rotation, core, outer = universe._rotate([], [], [], 1000.0)
    assert rotation["protected_open_positions"] == ["BTC_USDT"]
    assert universe._membership_state["BTC_USDT"]["status"] == "HELD_OPEN_POSITION"
