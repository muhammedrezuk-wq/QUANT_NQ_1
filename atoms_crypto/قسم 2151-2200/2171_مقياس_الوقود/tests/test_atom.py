"""اختبار مقياس الوقود — market.oi/funding/premium/candle ⇒ sense.fuel.state.

يفحص: أوّل قراءة baseline، ثم FUEL BUILDING into decline مع لونغاتٍ مزدحمة
(تمويل مرتفع)، ثم FUEL SPENT عند تفريغ OI، وأنّ الطرف المزدحم يتبع العلاوة."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("fuel", os.path.join(HERE, "..", "atom.py"))
fuel = importlib.util.module_from_spec(spec); spec.loader.exec_module(fuel)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=171, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def _last(bus):
    return [p for e, p in bus.out if e == "sense.fuel.state"][-1]


async def main():
    bus = Bus(); atom = fuel.Atom()
    await atom.initialize(bus.ctx({"window_s": 1800, "max_age_s": 60}))
    await atom.start()

    # تمويل مرتفع ⇒ لونغات مزدحمة ثابتة طوال الاختبار.
    await atom._on_funding({"symbol": "BTC_USDT", "provider": "MEXC",
                            "funding_rate": 0.0002, "funding_pct": 0.02})

    # أوّل OI بعد سعرٍ ⇒ baseline (المرجع يُخزَّن، لا وقود بعد).
    await atom._on_candle({"symbol": "BTC_USDT", "provider": "MEXC", "close": 100.0})
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC", "oi": 1_000_000.0})
    assert _last(bus)["fuel"] == "baseline", _last(bus)

    # OI +0.2% مقابل سعر −0.2% ⇒ FUEL BUILDING into decline [longs_crowded].
    await atom._on_candle({"symbol": "BTC_USDT", "provider": "MEXC", "close": 99.8})
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC", "oi": 1_002_000.0})
    s = _last(bus)
    assert s["fuel"] == "building_decline", s
    assert s["crowd"] == "longs_crowded" and s["risk"] == "down_cascade"
    assert s["oi_pct"] > 0.15 and s["px_pct"] < -0.1
    assert "FUEL BUILDING into decline [longs_crowded]" in s["label"]

    # تفريغ OI −0.6% مقابل المرجع ⇒ FUEL SPENT.
    await atom._on_candle({"symbol": "BTC_USDT", "provider": "MEXC", "close": 99.9})
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC", "oi": 994_000.0})
    s2 = _last(bus)
    assert s2["fuel"] == "spent", s2

    # العلاوة العميقة السالبة ⇒ شورتات مزدحمة (رمزٌ آخر، تمويل غائب).
    await atom._on_premium({"symbol": "ETH_USDT", "provider": "MEXC", "premium_bps": -9.0})
    await atom._on_candle({"symbol": "ETH_USDT", "provider": "MEXC", "close": 50.0})
    await atom._on_oi({"symbol": "ETH_USDT", "provider": "MEXC", "oi": 500_000.0})    # baseline
    await atom._on_candle({"symbol": "ETH_USDT", "provider": "MEXC", "close": 50.1})   # سعر +0.2%
    await atom._on_oi({"symbol": "ETH_USDT", "provider": "MEXC", "oi": 501_000.0})     # OI +0.2%
    s3 = _last(bus)
    assert s3["fuel"] == "building_rise" and s3["crowd"] == "shorts_crowded", s3
    assert s3["risk"] == "up_squeeze"

    h = await atom.health_check()
    assert h.state.value == "healthy"
    print("OK 171 — fuel gauge: baseline ثم BUILDING (decline/rise) وSPENT، والطرف المزدحم مقيس")


if __name__ == "__main__":
    asyncio.run(main())
