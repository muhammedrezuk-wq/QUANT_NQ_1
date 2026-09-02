"""اختبار وسيط المدى — market.candle (5m) ⇒ sense.median_range.state."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mr", os.path.join(HERE, "..", "atom.py"))
mr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mr)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=156, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def candle(rng, close=100.0):
    return {"provider": "MEXC", "symbol": "BTC_USDT", "timeframe": "5m",
            "open": close, "high": close + rng, "low": close, "close": close,
            "volume": 1000.0, "period_start": 0, "closed": True}


async def main():
    bus = Bus(); atom = mr.Atom()
    await atom.initialize(bus.ctx({"timeframe": "5m", "window": 12, "max_age_s": 600}))
    await atom.start()
    # اثنتا عشرة شمعة بمديات 1..12 ⇒ الوسيط = sorted[12//2] = العنصر 7 (لا 6.5).
    for r in range(1, 13):
        await atom._on_candle(candle(float(r)))
    s = [p for e, p in bus.out if e == "sense.median_range.state"][-1]
    assert s["bars"] == 12
    assert s["median_range"] == 7.0, "وسيط المصدر: العنصر الأوسط الأعلى لا متوسّط الوسطين"
    assert s["median_bps"] == round(7.0 / 100.0 * 1e4, 3), "‏بالنقطة الأساس = الوسيط ÷ السعر ×10⁴"
    # إطار مختلف يُرشَّح (لا يدخل النافذة).
    n_before = s["bars"]
    await atom._on_candle({**candle(99.0), "timeframe": "1m"})
    s2 = [p for e, p in bus.out if e == "sense.median_range.state"][-1]
    assert s2["bars"] == n_before, "شمعة 1م مرشّحة — لا تلمس نافذة 5د"
    h = await atom.health_check()
    assert h.details["updates"] == 12
    print("OK 156 — median-range: وسيط المصدر بالضبط، والترشيح بالإطار قائم")


if __name__ == "__main__":
    asyncio.run(main())
