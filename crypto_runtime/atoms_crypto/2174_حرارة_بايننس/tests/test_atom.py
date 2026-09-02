"""اختبار حرارة بايننس — feed.binance.oi/premium ⇒ sense.binance_heat.state.

يفحص الحالة الهيكليّة: DEGRADED/AWAITING_BINANCE_BRIDGE قبل أيّ حدث، ثم
التمويل من حدث العلاوة وفرقَ OI٪ على النافذة من حدثَي OI، وصحّة HEALTHY بعده."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("bheat", os.path.join(HERE, "..", "atom.py"))
bheat = importlib.util.module_from_spec(spec); spec.loader.exec_module(bheat)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=174, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def _last(bus):
    return [p for e, p in bus.out if e == "sense.binance_heat.state"][-1]


async def main():
    bus = Bus(); atom = bheat.Atom()
    await atom.initialize(bus.ctx({"window_s": 1800, "max_age_s": 300, "oi_flat_pct": 0.05}))
    await atom.start()

    # الحالة الهيكليّة: بلا جسرٍ بعد ⇒ DEGRADED برسالة AWAITING_BINANCE_BRIDGE.
    h0 = await atom.health_check()
    assert h0.state.value == "degraded" and h0.message == "AWAITING_BINANCE_BRIDGE", h0
    assert not bus.out

    # حدث العلاوة يحمل التمويل (lastFundingRate من premiumIndex).
    await atom._on_premium({"symbol": "BTCUSDT", "provider": "BINANCE",
                            "funding_rate": 0.0001})
    s = _last(bus)
    assert abs(s["funding_pct"] - 0.01) < 1e-9 and s["oi_pct_30min"] is None, s

    # أوّل OI مرجعٌ، والثاني +0.6% ⇒ تراكمٌ عالميّ.
    await atom._on_oi({"symbol": "BTCUSDT", "provider": "BINANCE", "oi": 107_000.0})
    await atom._on_oi({"symbol": "BTCUSDT", "provider": "BINANCE", "oi": 107_642.0})
    s2 = _last(bus)
    assert s2["oi_pct_30min"] is not None and s2["oi_pct_30min"] > 0.05
    assert s2["oi_flow"] == "accumulating" and s2["funding_pct"] is not None, s2

    h1 = await atom.health_check()
    assert h1.state.value == "healthy", h1
    print("OK 174 — binance heat: هيكليّة AWAITING_BINANCE_BRIDGE ثم تمويل+فرق OI عند وصول الجسر")


if __name__ == "__main__":
    asyncio.run(main())
