"""اختبار حجم/المتوسط — market.candle (5m) ⇒ sense.volume_ma.state."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("vma", os.path.join(HERE, "..", "atom.py"))
vma = importlib.util.module_from_spec(spec); spec.loader.exec_module(vma)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=157, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def candle(vol, close=100.0):
    return {"provider": "MEXC", "symbol": "BTC_USDT", "timeframe": "5m",
            "open": close, "high": close + 1, "low": close - 1, "close": close,
            "volume": vol, "period_start": 0, "closed": True}


def last(bus):
    return [p for e, p in bus.out if e == "sense.volume_ma.state"][-1]


async def main():
    bus = Bus(); atom = vma.Atom()
    await atom.initialize(bus.ctx({"timeframe": "5m", "ma_length": 20,
                                   "breakout_mult": 2.0, "climax_mult": 2.5,
                                   "fade_mult": 0.7, "max_age_s": 600}))
    await atom.start()
    # الشمعة الأولى: لا متوسط بعد ⇒ إحماء.
    await atom._on_candle(candle(100.0))
    assert last(bus)["ratio"] is None and last(bus)["signal"] == "warming"
    # نُكمل 20 شمعة بحجم 100 (الأولى منها احتُسبت) لملء نافذة المتوسط.
    for _ in range(19):
        await atom._on_candle(candle(100.0))
    # الشمعة الحادية والعشرون بحجم 300: المتوسط=100 (السابقات) ⇒ النسبة 3.0 ⇒ ذروة.
    await atom._on_candle(candle(300.0))
    s = last(bus)
    assert s["ma"] == 100.0 and s["ratio"] == 3.0, "النسبة = الجارية ÷ متوسّط السابقات"
    assert s["signal"] == "climax", "‏×3 ≥ عتبة الذروة 2.5"
    # حجمٌ ذابل بعدها ⇒ fade (المتوسط ارتفع لدخول 300 النافذة).
    await atom._on_candle(candle(50.0))
    assert last(bus)["signal"] == "fade", "‏<×0.7 ذبول"
    h = await atom.health_check()
    assert h.details["updates"] == 22
    print("OK 157 — volume-ma: الجارية مستثناة من متوسّطها، والتصنيف الثلاثي يعمل")


if __name__ == "__main__":
    asyncio.run(main())
