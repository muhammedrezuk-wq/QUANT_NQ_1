"""اختبار أثر السعر — صفقات ⇒ micro.price_impact.state (λ + هشاشة)."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("pi", os.path.join(HERE, "..", "atom.py"))
pi = importlib.util.module_from_spec(spec); spec.loader.exec_module(pi)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=264, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


async def main():
    bus = Bus(); atom = pi.Atom()
    await atom.initialize(bus.ctx({"window_s": 60, "min_flow": 1e-9, "max_age_s": 15}))
    await atom.start()
    # صفقة واحدة: لا نافذة كافية بعد.
    await atom._on_trade({"symbol": "BTC_USDT", "price": 100.0, "size": 5.0, "side": "BUY"})
    assert not [p for e, p in bus.out if e == "micro.price_impact.state"]
    # شراءٌ صافٍ +10 حرّك السعر +0.2 ⇒ λ = 0.2/10 = 0.02.
    await atom._on_trade({"symbol": "BTC_USDT", "price": 100.2, "size": 5.0, "side": "BUY"})
    s = [p for e, p in bus.out if e == "micro.price_impact.state"][-1]
    assert abs(s["kyle_lambda"] - 0.02) < 1e-9, s
    assert s["net_flow"] == 10.0 and s["fragility"] is not None and s["fragility"] > 0
    print("OK 264 — أثر السعر: λ=Δسعر/صافي التدفّق، والهشاشة موجبة")


if __name__ == "__main__":
    asyncio.run(main())
