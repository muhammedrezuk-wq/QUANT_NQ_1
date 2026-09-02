"""اختبار عدسة الدقيقة — market.candle 1د ⇒ sense.min1_lens.state."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("lens", os.path.join(HERE, "..", "atom.py"))
lens = importlib.util.module_from_spec(spec); spec.loader.exec_module(lens)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=161, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def _c(start, o, h, l, c, v):
    return {"provider": "MEXC", "symbol": "BTC_USDT", "timeframe": "1m",
            "open": o, "high": h, "low": l, "close": c, "volume": v,
            "period_start": start, "closed": True}


async def main():
    bus = Bus(); atom = lens.Atom()
    await atom.initialize(bus.ctx({"timeframe": "1m", "display": 15, "vol_ma": 20, "max_age_s": 180}))
    await atom.start()

    # خمس شموع 1د: أربع بحجم عاديّ ثمّ مسمار حجم ×~2.8 صاعد.
    candles = [
        (60.0, 100.0, 101.0, 99.0, 100.5, 10.0),   # + عاديّ
        (120.0, 100.5, 101.0, 100.0, 100.2, 10.0),  # − عاديّ
        (180.0, 100.2, 101.0, 100.0, 100.8, 10.0),  # +
        (240.0, 100.8, 101.0, 100.0, 100.6, 10.0),  # −
        (300.0, 100.6, 102.0, 100.0, 101.5, 50.0),  # + مسمار حجم
    ]
    for row in candles:
        await atom._on_candle(_c(*row))

    s = [p for e, p in bus.out if e == "sense.min1_lens.state"][-1]
    assert s["count"] == 5, s["count"]
    assert s["last_price"] == 101.5
    assert abs(s["vma"] - 18.0) < 1e-6, s["vma"]           # (10+10+10+10+50)/5
    assert s["bars"][-1]["dir"] == "+" and s["bars"][1]["dir"] == "-"
    assert s["bars"][-1]["vol_x"] > 1.5, "مسمار الحجم أعلى من متوسّطه"
    assert s["bars"][0]["vol_x"] < 1.0, "الشمعة العاديّة دون المتوسّط"
    # فرزٌ زمنيّ محفوظ: آخر شمعة هي الأحدث period_start.
    assert s["bars"][-1]["period_start"] == 300.0

    # لا عتبات حكم: الحالة عيونٌ صرفة (لا رتبة/رخصة/اتجاه محكوم).
    assert "grade" not in s and "license" not in s

    h = await atom.health_check()
    assert h.details["updates"] == 5
    print("OK 161 — 1m lens: آخر 15 شمعة باتجاهها وحجمها النسبيّ؛ عيونٌ لا قرار")


if __name__ == "__main__":
    asyncio.run(main())
