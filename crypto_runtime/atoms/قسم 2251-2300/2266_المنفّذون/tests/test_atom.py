"""اختبار المنفّذين — market.trade ⇒ sense.aggressor.state (نسبةٌ وامتصاص)."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("agg", os.path.join(HERE, "..", "atom.py"))
agg = importlib.util.module_from_spec(spec); spec.loader.exec_module(agg)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=266, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def last(bus, symbol):
    return [p for e, p in bus.out if e == "sense.aggressor.state" and p["symbol"] == symbol][-1]


async def main():
    bus = Bus(); atom = agg.Atom()
    await atom.initialize(bus.ctx({"window_trades": 100, "dominance_threshold": 0.65,
                                   "flat_bps": 2.0, "min_samples": 5, "max_age_s": 15}))
    await atom.start()

    # بيعٌ ساحق (8 من 10) والسعر ثابت ⇒ طلبٌ راقد يمتص ⇒ BID_ABSORBING.
    for _ in range(4):
        await atom._on_trade({"symbol": "BTC_USDT", "price": 100.0, "size": 2.0, "side": "SELL"})
    await atom._on_trade({"symbol": "BTC_USDT", "price": 100.0, "size": 2.0, "side": "BUY"})
    s = last(bus, "BTC_USDT")
    assert s["role"] == "TRIGGER"
    assert abs(s["buy_ratio"] - 0.2) < 1e-9 and abs(s["dominance"] - 0.8) < 1e-9
    assert abs(s["price_move_bps"]) < 1e-9
    assert s["absorption"] == "BID_ABSORBING", s

    # شراءٌ ساحق (100%) لكن مع صعود السعر ⇒ لا امتصاص (الحركة تؤكّد العدوان).
    for i in range(6):
        await atom._on_trade({"symbol": "ETH_USDT", "price": 10.0 + i * 0.5, "size": 1.0, "side": "BUY"})
    s2 = last(bus, "ETH_USDT")
    assert s2["buy_ratio"] == 1.0 and s2["price_move_bps"] > 2.0
    assert s2["absorption"] == "NONE", s2

    # شراءٌ ساحق (15 من 16) والسعر ثابت ⇒ عرضٌ راقد يمتص ⇒ ASK_ABSORBING.
    for _ in range(5):
        await atom._on_trade({"symbol": "SOL_USDT", "price": 50.0, "size": 3.0, "side": "BUY"})
    await atom._on_trade({"symbol": "SOL_USDT", "price": 50.0, "size": 1.0, "side": "SELL"})
    s3 = last(bus, "SOL_USDT")
    assert s3["buy_ratio"] > 0.65 and s3["absorption"] == "ASK_ABSORBING", s3

    h = await atom.health_check()
    assert h.details["trades"] == 17   # 5 (BTC) + 6 (ETH) + 6 (SOL) صفقة مُعالَجة
    print("OK 266 — المنفّذون: النسبةُ تتبع الحجم، والامتصاص يُكشف فقط بلا حركة سعر (زناد لا اتجاه)")


if __name__ == "__main__":
    asyncio.run(main())
