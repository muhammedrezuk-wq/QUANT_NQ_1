"""اختبار VWAP الجلسة — market.candle ⇒ sense.vwap.state.

يتحقّق من: المتوسط الموزون بالحجم، الرخصة (فوق/تحت)، وتصفير مرساة اليوم."""
from __future__ import annotations
import asyncio, importlib.util, os, math
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("vw", os.path.join(HERE, "..", "atom.py"))
vw = importlib.util.module_from_spec(spec); spec.loader.exec_module(vw)

DAY = 86400.0


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=151, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def candle(day, off, h, l, c, v):
    return {"symbol": "BTC_USDT", "provider": "MEXC", "timeframe": "5m",
            "high": h, "low": l, "close": c, "volume": v, "period_start": day * DAY + off}


async def main():
    bus = Bus(); atom = vw.Atom()
    await atom.initialize(bus.ctx({"max_age_s": 10, "band_mult": 1.0}))
    await atom.start()
    # شمعتان بحجمين مختلفين — نتحقّق يدويًّا من المتوسط الموزون على hlc3
    await atom._on_candle(candle(0, 0, 102, 98, 100, 10))     # tp=100
    await atom._on_candle(candle(0, 300, 114, 108, 111, 30))  # tp=111
    s = [p for e, p in bus.out if e == "sense.vwap.state"][-1]
    exp = (100 * 10 + 111 * 30) / 40.0                        # =108.25
    assert abs(s["vwap"] - exp) < 1e-6, (s["vwap"], exp)
    assert s["license"] == "long", "الإغلاق 111 فوق VWAP ⇒ رخصة لونغ"
    assert s["upper"] > s["vwap"] > s["lower"]
    # يومٌ جديد ⇒ تصفير المرساة (VWAP = tp الشمعة الأولى وحدها)
    await atom._on_candle(candle(1, 0, 210, 190, 200, 5))     # tp=200
    s2 = [p for e, p in bus.out if e == "sense.vwap.state"][-1]
    assert abs(s2["vwap"] - 200.0) < 1e-6, "المرساة صُفِّرت مع اليوم"
    assert s2["sigma"] == 0.0
    print("OK 151 — VWAP: الوزن بالحجم صحيح، الرخصة تتبع الموقع، والمرساة يومية")


if __name__ == "__main__":
    asyncio.run(main())
