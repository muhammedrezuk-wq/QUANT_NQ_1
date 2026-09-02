"""اختبار CVD — صفقات موقَّعة ⇒ micro.cvd.state + تباعد."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("cvd", os.path.join(HERE, "..", "atom.py"))
cvd = importlib.util.module_from_spec(spec); spec.loader.exec_module(cvd)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=263, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


async def main():
    bus = Bus(); atom = cvd.Atom()
    await atom.initialize(bus.ctx({"window_s": 300, "max_age_s": 15}))
    await atom.start()
    # شراء 3 ثم بيع 1 ⇒ CVD = +2.
    await atom._on_trade({"symbol": "BTC_USDT", "price": 100.0, "size": 3.0, "side": "BUY"})
    await atom._on_trade({"symbol": "BTC_USDT", "price": 100.1, "size": 1.0, "side": "SELL"})
    s = [p for e, p in bus.out if e == "micro.cvd.state"][-1]
    assert abs(s["cvd"] - 2.0) < 1e-9
    # السعر يعلو (100→101) لكن التدفّق سالب (بيع كثيف) ⇒ تباعد هبوطيّ (امتصاص).
    await atom._on_trade({"symbol": "ETH_USDT", "price": 10.0, "size": 1.0, "side": "BUY"})
    await atom._on_trade({"symbol": "ETH_USDT", "price": 11.0, "size": 9.0, "side": "SELL"})
    s2 = [p for e, p in bus.out if e == "micro.cvd.state" and p["symbol"] == "ETH_USDT"][-1]
    assert s2["price_delta"] > 0 and s2["window_delta"] < 0
    assert s2["divergence"] == "BEARISH", s2
    print("OK 263 — CVD: التراكم صحيح، والتباعد الهبوطيّ يُكشف (سعرٌ يعلو وتدفّقٌ يبيع)")


if __name__ == "__main__":
    asyncio.run(main())
