"""اختبار الأطر العليا — market.candle (4h + 15m) ⇒ sense.htf.state."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("htf", os.path.join(HERE, "..", "atom.py"))
htf = importlib.util.module_from_spec(spec); spec.loader.exec_module(htf)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=159, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def _c(symbol, tf, start, o, h, l, c):
    return {"provider": "MEXC", "symbol": symbol, "timeframe": tf,
            "open": o, "high": h, "low": l, "close": c, "period_start": start, "closed": True}


async def main():
    bus = Bus(); atom = htf.Atom()
    await atom.initialize(bus.ctx({"timeframe_map": "4h", "timeframe_struct": "15m",
                                   "range_bars": 84, "slope_bars": 4, "struct_bars": 8,
                                   "extreme_high_pct": 85, "extreme_low_pct": 15, "max_age_s": 1200}))
    await atom.start()

    # خريطة 4H: خمس شموع بمدى صريح [100..200] وإغلاق أخير 150 ⇒ موضع 50٪ (لا تطرّف).
    for i, close in enumerate((140.0, 145.0, 148.0, 150.0, 150.0)):
        await atom._on_candle(_c("BTC_USDT", "4h", 14400.0 * i, close - 1, 200.0, 100.0, close))

    # بنية 15د صاعدة: 16 شمعة، الثماني الأخيرة أعلى قممًا وقيعانًا من الثماني قبلها.
    for i in range(16):
        base = 100.0 + i          # قمم وقيعان تتصاعد رتيبًا ⇒ HH + HL
        await atom._on_candle(_c("BTC_USDT", "15m", 900.0 * i, base, base + 1, base - 1, base + 0.5))

    s = [p for e, p in bus.out if e == "sense.htf.state"][-1]
    assert s["structure"] == "UP", s["structure"]
    assert s["ready"] is True
    assert 45.0 <= s["position_pct"] <= 55.0, s["position_pct"]   # لا تطرّف
    assert s["extreme"] is False and s["extreme_side"] is None
    assert s["grade_long"] == "A", "توافق البنية الصاعدة مع لونغ خارج التطرّف ⇒ A"
    assert s["grade_short"] == "B", "تعارض البنية الصاعدة مع شورت ⇒ B"
    assert s["htf_bias"] == "up" and s["slope_dir"] == "up"
    assert s["range_low"] == 100.0 and s["range_high"] == 200.0

    # ادفع السعر إلى قمّة المدى (≥85٪) بشمعة 4H جديدة ⇒ تطرّف علوي يُنزل رتبة اللونغ.
    await atom._on_candle(_c("BTC_USDT", "4h", 14400.0 * 5, 189.0, 195.0, 185.0, 190.0))
    s2 = [p for e, p in bus.out if e == "sense.htf.state"][-1]
    assert s2["position_pct"] >= 85.0, s2["position_pct"]
    assert s2["extreme"] is True and s2["extreme_side"] == "high"
    assert s2["grade_long"] == "B", "السعر في تطرّف مدى 4H ⇒ B رغم بنية 15د الصاعدة"

    h = await atom.health_check()
    assert h.details["updates"] >= 21
    print("OK 159 — HTF: البنية الصاعدة ترفع رتبة اللونغ، والتطرّف يخفضها؛ لا بوّابة منع")


if __name__ == "__main__":
    asyncio.run(main())
