"""اختبار التصفيات — feed.binance.liquidation ⇒ sense.liquidations.state (خريطة كثافة).

تصفياتٌ وهميّة تُغذّى عبر المعالِج (لا مصدر بعد)، ويُتحقَّق أنّ الصحّة تنتظر
جسر بايننس حتى أوّل حدث."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("liq", os.path.join(HERE, "..", "atom.py"))
liq = importlib.util.module_from_spec(spec); spec.loader.exec_module(liq)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=267, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


async def main():
    bus = Bus(); atom = liq.Atom()
    await atom.initialize(bus.ctx({"bin_bps": 10, "window_s": 3600, "top_zones": 5, "max_age_s": 600}))
    await atom.start()
    # قبل أوّل حدث: هيكليّة ⇒ DEGRADED/AWAITING_BINANCE_BRIDGE.
    h0 = await atom.health_check()
    assert h0.state.value == "degraded" and h0.message == "AWAITING_BINANCE_BRIDGE", h0

    # تصفياتُ بيعٍ قسريّ مكثّفة عند 80000 (تصفية مراكز طويلة)، وشراءٌ قسريّ متفرّق عند 80500.
    for _ in range(3):
        await atom._on_liquidation({"symbol": "BTC_USDT", "provider": "BINANCE",
                                    "price": 80000.0, "side": "SELL", "size": 5.0})
    await atom._on_liquidation({"symbol": "BTC_USDT", "provider": "BINANCE",
                                "price": 80500.0, "side": "BUY", "size": 2.0})
    s = [p for e, p in bus.out if e == "sense.liquidations.state"][-1]
    assert s["role"] == "WITNESS"
    assert abs(s["sell_liquidations"] - 15.0) < 1e-9 and abs(s["buy_liquidations"] - 2.0) < 1e-9
    assert s["zone_count"] == 2, s                       # منطقتان منفصلتان لا مندمجتان
    # أكثف منطقة = تجمّع الـ80000 (إجمالي 15) قرب مركز المنطقة.
    assert s["hottest_zone"]["total"] == 15.0 and abs(s["hottest_zone"]["price"] - 80000.0) < 100.0, s

    # بعد الأحداث: الصحّة صحيحة والعدّاد يُحصي كلّ حدث.
    h = await atom.health_check()
    assert h.state.value == "healthy" and h.details["events"] == 4, h
    print("OK 267 — التصفيات: الخريطةُ تتراكم بمناطق منفصلة، والصحّة تنتظر جسر بايننس حتى أوّل حدث")


if __name__ == "__main__":
    asyncio.run(main())
