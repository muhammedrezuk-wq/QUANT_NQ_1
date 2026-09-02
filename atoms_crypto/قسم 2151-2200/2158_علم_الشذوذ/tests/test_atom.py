"""اختبار علم الشذوذ — market.candle (5m) ⇒ sense.abnormal.state."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ab", os.path.join(HERE, "..", "atom.py"))
ab = importlib.util.module_from_spec(spec); spec.loader.exec_module(ab)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=158, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def candle(rng, close=100.0):
    return {"provider": "MEXC", "symbol": "BTC_USDT", "timeframe": "5m",
            "open": close, "high": close + rng, "low": close, "close": close,
            "volume": 1000.0, "period_start": 0, "closed": True}


def last(bus):
    return [p for e, p in bus.out if e == "sense.abnormal.state"][-1]


async def main():
    bus = Bus(); atom = ab.Atom()
    await atom.initialize(bus.ctx({"timeframe": "5m", "window": 12, "last_n": 3,
                                   "abnormal_mult": 3.0, "min_bars": 12, "max_age_s": 600}))
    await atom.start()
    # قبل امتلاء نافذة الوسيط: لا حكم.
    await atom._on_candle(candle(10.0))
    assert last(bus)["regime"] is None and last(bus)["abnormal"] is False, "لا حكم قبل min_bars"
    # نُكمل حتى 12 شمعةً بمدى 10 ⇒ وسيط 10، وضعٌ طبيعيّ.
    for _ in range(11):
        await atom._on_candle(candle(10.0))
    s12 = last(bus)
    assert s12["bars"] == 12 and s12["regime"] == "normal" and s12["median_range"] == 10.0
    # شمعة صدمة بمدى 50 > ×3 الوسيط (30) ⇒ ABNORMAL، والنسبة 5.
    await atom._on_candle(candle(50.0))
    s = last(bus)
    assert s["regime"] == "abnormal" and s["abnormal"] is True, "max آخر 3 (50) > ×3 الوسيط (10)"
    assert s["last3_max"] == 50.0 and s["median_range"] == 10.0 and s["ratio"] == 5.0
    # ثلاث شموع هادئة تُخرج الصدمة من نافذة آخر 3 ⇒ عودةٌ للطبيعيّ.
    for _ in range(3):
        await atom._on_candle(candle(10.0))
    assert last(bus)["regime"] == "normal", "خروج الصدمة من نافذة آخر 3 يُنزل العلم"
    print("OK 158 — abnormal: يُرفع العلم بالصدمة وينزل مع خروجها، والوسيط يتّحد مع 156")


if __name__ == "__main__":
    asyncio.run(main())
