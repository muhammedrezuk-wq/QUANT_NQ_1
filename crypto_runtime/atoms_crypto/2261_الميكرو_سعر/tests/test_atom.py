"""اختبار الميكرو-سعر — market.depth ⇒ micro.microprice.state."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mp", os.path.join(HERE, "..", "atom.py"))
mp = importlib.util.module_from_spec(spec); spec.loader.exec_module(mp)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=261, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


async def main():
    bus = Bus(); atom = mp.Atom()
    await atom.initialize(bus.ctx({"max_age_s": 10, "min_size": 0.0}))
    await atom.start()
    # طلبٌ أثقل من العرض ⇒ ميلٌ نحو العرض (ضغط شراء)، micro > mid.
    await atom._on_depth({"symbol": "BTC_USDT", "provider": "MEXC",
                          "bids": [[100.0, 30.0]], "asks": [[100.2, 10.0]]})
    s = [p for e, p in bus.out if e == "micro.microprice.state"][-1]
    mid = (100.0 + 100.2) / 2
    assert s["mid"] == mid
    assert s["microprice"] > mid, "ضغط الشراء يرفع العادل فوق المنتصف"
    assert s["imbalance"] > 0 and s["tilt_bps"] > 0
    # التماثل ⇒ العادل = المنتصف
    await atom._on_depth({"symbol": "ETH_USDT", "provider": "MEXC",
                          "bids": [[10.0, 5.0]], "asks": [[10.1, 5.0]]})
    s2 = [p for e, p in bus.out if e == "micro.microprice.state"][-1]
    assert abs(s2["microprice"] - s2["mid"]) < 1e-9
    h = await atom.health_check()
    assert h.details["updates"] == 2
    print("OK 261 — micro-price: الميل يتبع اختلال الأحجام، والتماثل=المنتصف")


if __name__ == "__main__":
    asyncio.run(main())
