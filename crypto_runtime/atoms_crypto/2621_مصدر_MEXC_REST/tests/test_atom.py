"""اختبار مصدر MEXC REST — بلا شبكة.

الشموع: نستدعي `_emit_closed` بمعطياتٍ مصنوعة (نتحقّق من الإحماء، وتخطّي
الشمعة الجارية، وعدم إعادة النشر). التموضع: نُرقّع `_get` بردودٍ ثابتة ونستدعي
`_poll_ticker` (نتحقّق من OI/التمويل/العلاوة). لا اتّصال خارجيّ إطلاقًا."""
from __future__ import annotations
import asyncio, importlib.util, os, time
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("rest", os.path.join(HERE, "..", "atom.py"))
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

SEC = 300   # Min5


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=621, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)

    def of(self, event): return [p for e, p in self.out if e == event]


async def main():
    bus = Bus(); atom = mod.Atom()
    await atom.initialize(bus.ctx({
        "symbols": ["BTC_USDT"], "timeframes": ["Min5"],
        "kline_poll_s": 10, "ticker_poll_s": 5, "depth_poll_s": 5, "depth_limit": 100,
        "warmup_bars": 2, "max_age_s": 60}))
    # لا نستدعي start() — لئلّا تنطلق حلقات الشبكة الخلفية.

    # ── الشموع ───────────────────────────────────────────────────────────
    now = time.time()
    # أربع شموع مغلقة + خامسة جارية (لم يكتمل إطارها بعد).
    times = [now - 5 * SEC, now - 4 * SEC, now - 3 * SEC, now - 2 * SEC, now - 0.5 * SEC]
    data = {"data": {
        "time":  times,
        "open":  [10, 11, 12, 13, 14], "high": [10, 11, 12, 13, 14],
        "low":   [10, 11, 12, 13, 14], "close": [10, 11, 12, 13, 14],
        "vol":   [100, 101, 102, 103, 104]}}
    await atom._emit_closed("BTC_USDT", "Min5", data)
    candles = bus.of("market.candle")
    # الإحماء=2 ⇒ الأرضية عند الشمعة قبل نافذة الإحماء ⇒ يُنشَر آخر اثنتين مغلقتين
    # فقط (12 و13)، والجارية (14) تُتخطّى.
    assert len(candles) == 2, [c["close"] for c in candles]
    assert [c["close"] for c in candles] == [12, 13]
    assert candles[0]["timeframe"] == "5m" and candles[0]["closed"] is True
    assert candles[-1]["period_start"] == now - 2 * SEC

    # استطلاعٌ ثانٍ بنفس المعطيات ⇒ لا شمعة جديدة (لا إعادة نشر).
    await atom._emit_closed("BTC_USDT", "Min5", data)
    assert len(bus.of("market.candle")) == 2, "لا يعيد نشر المغلقة"

    # ── التموضع (بترقيع _get) ────────────────────────────────────────────
    def fake_get(url):
        if "funding_rate" in url:
            return {"data": {"fundingRate": 0.0001}}
        return {"data": {"holdVol": 12345.0, "fairPrice": 100.5, "indexPrice": 100.0}}
    mod._get = fake_get
    await atom._poll_ticker()

    oi = bus.of("market.oi")[-1]
    assert oi["oi"] == 12345.0
    fund = bus.of("market.funding")[-1]
    assert fund["funding_rate"] == 0.0001 and abs(fund["funding_pct"] - 0.01) < 1e-9
    prem = bus.of("market.premium")[-1]
    assert prem["premium_bps"] == 50.0, prem          # (100.5-100)/100*1e4
    assert prem["fair_price"] == 100.5 and prem["index_price"] == 100.0

    h = await atom.health_check()
    assert h.details["candles"] == 2 and h.details["ticker_polls"] == 1
    print("OK 621 — REST: الإحماء وتخطّي الجارية وعدم الإعادة، والتموضع OI/تمويل/علاوة")

    # ── العمق (سنابشوت كامل بترقيع _get) ────────────────────────────────
    def fake_depth(url):
        if "depth" in url:
            return {"data": {"bids": [[100.0, 5, 1], [99.9, 3, 1]],
                              "asks": [[100.1, 4, 1], [100.2, 2, 1]]}}
        return fake_get(url)
    mod._get = fake_depth
    await atom._poll_depth()
    d = bus.of("market.depth")[-1]
    assert d["symbol"] == "BTC_USDT"
    assert d["bids"] == [[100.0, 5.0], [99.9, 3.0]] and d["asks"] == [[100.1, 4.0], [100.2, 2.0]]
    h2 = await atom.health_check()
    assert h2.details["depth_polls"] == 1
    print("OK 621 — العمق: سنابشوت bids/asks كامل من REST لا من دلتا WS")

    # ── تتبُّع كون 1001 الحيّ ────────────────────────────────────────────
    await atom._on_membership({"symbols": ["BTC_USDT", "NEWCOIN_USDT"]})
    assert atom._symbols == ["BTC_USDT", "NEWCOIN_USDT"], "الدورة التالية تقرأ القائمة الجديدة تلقائيًّا"
    await atom._on_membership({"symbols": []})
    assert atom._symbols == ["BTC_USDT", "NEWCOIN_USDT"], "حمولةٌ فارغة لا تُفرِغ القائمة الحالية"
    print("OK 621 — يتبع كون 1001 الحيّ بلا إعادة تشغيل (حلقة الاستطلاع تقرأ القائمة كلّ دورة)")


if __name__ == "__main__":
    asyncio.run(main())
