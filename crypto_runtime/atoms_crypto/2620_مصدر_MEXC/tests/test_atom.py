"""اختبار ذرّة مصدر MEXC — تحويل رسائل MEXC الحقيقية إلى أحداث market.*"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os

from core.contracts.atom import AtomContext, HealthState

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mexc_atom", os.path.join(HERE, "..", "atom.py"))
mexc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mexc)


class FakeLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def critical(self, *a, **k): pass


class FakeBus:
    def __init__(self):
        self.published = []

    def ctx(self, config):
        async def publish(event, payload):
            self.published.append((event, payload))
        return AtomContext(atom_id=620, config=config, logger=FakeLogger(),
                           publish=publish, subscribe=lambda *a, **k: None)


class FakeWS:
    def __init__(self):
        self.sent = []
    async def send(self, msg):
        self.sent.append(json.loads(msg))


CFG = {"market_type": "futures", "symbols": ["BTC_USDT", "ETH_USDT"],
       "depth_levels": 3, "max_symbols_per_conn": 40, "ping_interval_s": 15,
       "max_age_s": 30, "subscribe_ticker": True, "subscribe_depth": True,
       "subscribe_deal": True}


async def _make():
    bus = FakeBus()
    atom = mexc.Atom()
    await atom.initialize(bus.ctx(dict(CFG)))
    return atom, bus


async def test_subscribe_futures_sends_correct_channels():
    atom, _ = await _make()
    ws = FakeWS()
    await atom._subscribe_futures(ws, ["BTC_USDT"])
    methods = [(m["method"], m["param"]["symbol"]) for m in ws.sent]
    assert ("sub.ticker", "BTC_USDT") in methods
    assert ("sub.deal", "BTC_USDT") in methods
    assert ("sub.depth", "BTC_USDT") in methods
    depth = next(m for m in ws.sent if m["method"] == "sub.depth")
    assert depth["param"]["compress"] is False, "العمق يُطلب بلا ضغط (JSON صريح)"
    print("OK — الاشتراك يرسل القنوات الثلاث بالرمز والصيغة الصحيحة")


async def test_ticker_becomes_market_tick():
    atom, bus = await _make()
    await atom._handle_futures({"channel": "push.ticker", "symbol": "BTC_USDT",
        "data": {"lastPrice": 78850.1, "bid1": 78850.0, "ask1": 78850.2, "volume24": 1234}})
    ticks = [p for e, p in bus.published if e == "market.tick"]
    assert ticks, "يجب أن يُنشَر market.tick"
    t = ticks[-1]
    assert t["symbol"] == "BTC_USDT" and t["bid"] == 78850.0 and t["ask"] == 78850.2 and t["price"] == 78850.1
    assert t["provider"] == "MEXC" and t["market"] == "futures"
    print("OK — push.ticker ⇒ market.tick بالأسعار الصحيحة")


async def test_depth_becomes_market_depth_truncated():
    atom, bus = await _make()
    await atom._handle_futures({"channel": "push.depth", "symbol": "ETH_USDT",
        "data": {"asks": [[2452.9, 10, 2], [2453.0, 5, 1], [2453.1, 3, 1], [2453.2, 9, 4]],
                 "bids": [[2452.8, 7, 3], [2452.7, 2, 1]], "version": 99}})
    depths = [p for e, p in bus.published if e == "market.depth"]
    assert depths, "يجب أن يُنشَر market.depth"
    d = depths[-1]
    assert len(d["asks"]) == 3, "مقصوص إلى depth_levels=3"
    assert d["asks"][0] == [2452.9, 10] and d["bids"][0] == [2452.8, 7]
    assert d["symbol"] == "ETH_USDT" and d["version"] == 99
    print("OK — push.depth ⇒ market.depth (مستويات مقصوصة [سعر,حجم])")


async def test_deal_list_becomes_market_trades_with_side():
    atom, bus = await _make()
    await atom._handle_futures({"channel": "push.deal", "symbol": "SOL_USDT",
        "data": [{"p": 97.21, "v": 1.5, "T": 1, "t": 1700000000000},
                 {"p": 97.20, "v": 0.3, "T": 2, "t": 1700000000500}]})
    trades = [p for e, p in bus.published if e == "market.trade"]
    assert len(trades) == 2, "صفقتان من مصفوفة push.deal"
    assert trades[0]["side"] == "BUY" and trades[0]["price"] == 97.21
    assert trades[1]["side"] == "SELL" and trades[1]["size"] == 0.3
    print("OK — push.deal (مصفوفة) ⇒ market.trade لكل صفقة بجهة المعتدي")


async def test_health_reflects_real_receipt():
    atom, _ = await _make()
    h = await atom.health_check()
    assert h.state == HealthState.UNHEALTHY  # لم يبدأ بعد
    atom._running = True
    h = await atom.health_check()
    assert h.message in ("NO_LIVE_CONNECTION", "AWAITING_FIRST_MESSAGE")
    atom._connected = 1
    await atom._handle_futures({"channel": "push.ticker", "symbol": "BTC_USDT",
                               "data": {"lastPrice": 1, "bid1": 1, "ask1": 1}})
    h = await atom.health_check()
    assert h.state == HealthState.HEALTHY and atom._counts["tick"] == 1
    print("OK — الصحّة تقيس الاستلام الفعليّ لا حياة المهمّة")


async def test_spot_mode_flags_protobuf():
    bus = FakeBus()
    atom = mexc.Atom()
    cfg = dict(CFG); cfg["market_type"] = "spot"
    await atom.initialize(bus.ctx(cfg))
    ws = FakeWS()
    await atom._subscribe_spot(ws, ["BTC_USDT"])
    assert ws.sent and ws.sent[0]["method"] == "SUBSCRIPTION"
    assert any("BTCUSDT" in p for p in ws.sent[0]["params"]), "الرمز بلا شرطة سفلية للسبوت"
    atom._running = True; atom._market = "spot"
    h = await atom.health_check()
    assert h.message == "SPOT_PROTOBUF_DECODE_REQUIRED"
    print("OK — سبوت: يشترك بالصيغة الصحيحة ويُعلن صراحةً حاجة protobuf")


async def test_membership_restarts_only_on_real_change():
    atom, _ = await _make()
    calls = []
    async def fake_stop(): calls.append("stop")
    async def fake_start(): calls.append("start")
    atom.stop, atom.start = fake_stop, fake_start   # يعزل الشبكة عن الاختبار

    # لم تُقلع بعد ⇒ تحديث القائمة بلا إعادة تشغيل (لا شيء يُعاد تشغيله).
    atom._running = False
    await atom._on_membership({"symbols": ["BTC_USDT", "ETH_USDT", "SOL_USDT"]})
    assert atom._symbols == ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
    assert calls == []

    # تعمل + مجموعة مطابقة (ولو بترتيبٍ مختلف) ⇒ لا إعادة تشغيل (لا تغيّر فعليّ).
    atom._running = True
    await atom._on_membership({"symbols": ["SOL_USDT", "ETH_USDT", "BTC_USDT"]})
    assert calls == [], "نفس المجموعة بترتيبٍ مختلف لا تستحقّ إعادة اتصال"

    # تعمل + مجموعةٌ مختلفة فعلًا ⇒ إعادة تشغيلٍ كاملة (stop ثم start).
    await atom._on_membership({"symbols": ["BTC_USDT", "XRP_USDT"]})
    assert atom._symbols == ["BTC_USDT", "XRP_USDT"]
    assert calls == ["stop", "start"]

    # حمولةٌ فارغة/فاسدة ⇒ تُتجاهَل بأمان (لا تُفرِغ القائمة الحالية).
    calls.clear()
    await atom._on_membership({"symbols": []})
    await atom._on_membership({})
    assert atom._symbols == ["BTC_USDT", "XRP_USDT"] and calls == []
    print("OK — تتبُّع كون 1001 الحيّ: تحديثٌ بلا اتصالٍ زائد، وإعادة اتصالٍ فقط عند تغيّرٍ حقيقيّ")


async def main():
    for test in (test_subscribe_futures_sends_correct_channels,
                 test_ticker_becomes_market_tick,
                 test_depth_becomes_market_depth_truncated,
                 test_deal_list_becomes_market_trades_with_side,
                 test_health_reflects_real_receipt,
                 test_spot_mode_flags_protobuf,
                 test_membership_restarts_only_on_real_change):
        await test()
    print("\n✅ كل الاختبارات نجحت")


if __name__ == "__main__":
    asyncio.run(main())
