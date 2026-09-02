"""اختبار مستويات الأمس — market.candle ⇒ sense.prior_day.state.

يتحقّق من: تجميع اليوم، تجمّد الأمس عند دوران اليوم، وحكم النظام (فوق/تحت/داخل)."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("pd", os.path.join(HERE, "..", "atom.py"))
pd = importlib.util.module_from_spec(spec); spec.loader.exec_module(pd)

DAY = 86400.0


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=153, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def candle(day, start_off, h, l, c):
    return {"symbol": "BTC_USDT", "provider": "MEXC", "timeframe": "5m",
            "high": h, "low": l, "close": c, "period_start": day * DAY + start_off}


async def main():
    bus = Bus(); atom = pd.Atom()
    await atom.initialize(bus.ctx({"max_age_s": 10}))
    await atom.start()
    # يوم ٠: قمة 80500، قاع 79800، آخر إغلاق 80200 ⇒ تصير مراجع الأمس
    await atom._on_candle(candle(0, 0, 80100, 79800, 80000))
    await atom._on_candle(candle(0, 300, 80500, 80050, 80300))
    await atom._on_candle(candle(0, 600, 80400, 80100, 80200))
    assert not atom._prior, "لا مرجع أمس قبل دوران اليوم"
    # يوم ١، أوّل شمعة فوق قمة الأمس ⇒ نظام صاعد مؤكّد
    await atom._on_candle(candle(1, 0, 80700, 80600, 80650))
    s = [p for e, p in bus.out if e == "sense.prior_day.state"][-1]
    assert s["prior_ready"] and s["pdh"] == 80500 and s["pdl"] == 79800 and s["pdc"] == 80200
    assert s["regime"] == "up_confirmed", "فوق PDH = صاعد مؤكّد"
    # هبوط تحت PDL ⇒ هابط مؤكّد
    await atom._on_candle(candle(1, 300, 79700, 79500, 79600))
    s2 = [p for e, p in bus.out if e == "sense.prior_day.state"][-1]
    assert s2["regime"] == "down_confirmed"
    # داخل النطاق ⇒ يوم داخليّ
    await atom._on_candle(candle(1, 600, 80100, 80000, 80050))
    s3 = [p for e, p in bus.out if e == "sense.prior_day.state"][-1]
    assert s3["regime"] == "inside"
    h = await atom.health_check()
    assert h.details["with_prior"] == 1
    print("OK 153 — مستويات الأمس: الأمس يتجمّد عند الدوران، والحكم يتبع الموقع")


if __name__ == "__main__":
    asyncio.run(main())
