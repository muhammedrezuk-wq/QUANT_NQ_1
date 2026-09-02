"""اختبار بروفايل الحجم — market.candle (5m) ⇒ sense.volume_profile.state."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("vp", os.path.join(HERE, "..", "atom.py"))
vp = importlib.util.module_from_spec(spec); spec.loader.exec_module(vp)

DAY = 86400
BASE = 1735689600  # 2025-01-01 00:00 UTC


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=155, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def candle(ts, low, high, close, vol):
    return {"provider": "MEXC", "symbol": "BTC_USDT", "timeframe": "5m",
            "open": close, "high": high, "low": low, "close": close,
            "volume": vol, "period_start": ts, "closed": True}


async def main():
    bus = Bus(); atom = vp.Atom()
    await atom.initialize(bus.ctx({"timeframe": "5m", "bin_frac": 0.0003, "value_area_pct": 0.70, "max_age_s": 600}))
    await atom.start()
    # جلسة واحدة: حجمٌ مكتنِز حول السعر 100 ⇒ POC هناك، والسعر داخل القيمة.
    await atom._on_candle(candle(BASE + 0, 99.0, 101.0, 100.0, 1000.0))
    await atom._on_candle(candle(BASE + 300, 99.5, 100.5, 100.0, 3000.0))  # الأثقل ضيّقًا حول 100
    await atom._on_candle(candle(BASE + 600, 95.0, 105.0, 100.0, 100.0))   # عريضة رقيقة
    s = [p for e, p in bus.out if e == "sense.volume_profile.state"][-1]
    assert s["bars"] == 3, "شموع الجلسة الثلاث تراكمت"
    assert 99.0 <= s["poc"] <= 101.0, "POC عند عنقود الحجم حول 100"
    assert s["val"] <= s["poc"] <= s["vah"], "POC داخل منطقة القيمة"
    assert s["val"] <= 100.0 <= s["vah"] and s["zone"] == "inside_value", "السعر داخل القيمة"
    # يوم UTC جديد ⇒ تصفير البروفايل (مرساة الجلسة).
    await atom._on_candle(candle(BASE + DAY, 200.0, 202.0, 201.0, 500.0))
    s2 = [p for e, p in bus.out if e == "sense.volume_profile.state"][-1]
    assert s2["bars"] == 1, "اليوم الجديد يبدأ جلسةً نظيفة"
    assert 199.0 <= s2["poc"] <= 203.0, "POC اليوم الثاني عند شمعته وحدها"
    h = await atom.health_check()
    assert h.details["updates"] == 4
    print("OK 155 — volume-profile: POC عند عنقود الحجم، والجلسة تُصفَّر مع يوم UTC")


if __name__ == "__main__":
    asyncio.run(main())
