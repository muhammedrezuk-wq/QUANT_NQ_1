"""اختبار مستويات الجلسة — market.candle ⇒ sense.session_levels.state.

يتحقّق من: قمة/قاع الجلسة، قاع الارتداد بعد القمة، وتصفيره عند قمة جديدة."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("sl", os.path.join(HERE, "..", "atom.py"))
sl = importlib.util.module_from_spec(spec); spec.loader.exec_module(sl)

DAY = 86400.0


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=152, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def candle(day, off, h, l):
    return {"symbol": "BTC_USDT", "provider": "MEXC", "timeframe": "5m",
            "high": h, "low": l, "period_start": day * DAY + off}


async def main():
    bus = Bus(); atom = sl.Atom()
    await atom.initialize(bus.ctx({"max_age_s": 10}))
    await atom.start()
    await atom._on_candle(candle(0, 0, 100, 90))       # القمة 100
    await atom._on_candle(candle(0, 300, 105, 95))     # قمة جديدة 105 (تصفّر الارتداد)
    await atom._on_candle(candle(0, 600, 103, 92))     # بعد القمة ⇒ قاع ارتداد 92
    await atom._on_candle(candle(0, 900, 104, 96))     # قاع 96 لا ينزل الارتداد
    s = [p for e, p in bus.out if e == "sense.session_levels.state"][-1]
    assert s["session_high"] == 105 and s["session_low"] == 90
    assert s["pullback_low"] == 92, "أدنى قاعٍ بعد شمعة القمة"
    # قمة جديدة ⇒ يُصفَّر قاع الارتداد
    await atom._on_candle(candle(0, 1200, 110, 101))
    s2 = [p for e, p in bus.out if e == "sense.session_levels.state"][-1]
    assert s2["session_high"] == 110 and s2["pullback_low"] is None
    # يومٌ جديد ⇒ تصفير كامل
    await atom._on_candle(candle(1, 0, 50, 40))
    s3 = [p for e, p in bus.out if e == "sense.session_levels.state"][-1]
    assert s3["session_high"] == 50 and s3["session_low"] == 40
    print("OK 152 — مستويات الجلسة: القمة/القاع والارتداد يتتبّعان ويُصفَّران صحيحًا")


if __name__ == "__main__":
    asyncio.run(main())
