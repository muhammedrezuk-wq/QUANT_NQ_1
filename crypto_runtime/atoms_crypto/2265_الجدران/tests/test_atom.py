"""اختبار الجدران — market.depth ⇒ sense.walls.state (نسبةٌ وأكبر الجدران)."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("walls", os.path.join(HERE, "..", "atom.py"))
walls = importlib.util.module_from_spec(spec); spec.loader.exec_module(walls)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=265, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


async def main():
    bus = Bus(); atom = walls.Atom()
    await atom.initialize(bus.ctx({"levels": 20, "top_n": 3, "near_bps": 25, "max_age_s": 10}))
    await atom.start()
    # طلبٌ أثقل من العرض ⇒ نسبة > 1 وميلٌ موجب؛ أكبر جدار طلبٍ 50 عند 99.8.
    await atom._on_depth({"symbol": "BTC_USDT", "provider": "MEXC",
                          "bids": [[100.0, 10.0], [99.9, 5.0], [99.8, 50.0]],
                          "asks": [[100.1, 4.0], [100.2, 3.0], [100.3, 8.0]]})
    s = [p for e, p in bus.out if e == "sense.walls.state"][-1]
    assert s["role"] == "WITNESS"
    assert abs(s["bid_sum"] - 65.0) < 1e-9 and abs(s["ask_sum"] - 15.0) < 1e-9
    assert s["ratio"] > 1 and s["imbalance"] > 0
    assert s["bid_walls"][0] == [99.8, 50.0], s["bid_walls"]      # أكبر جدار طلبٍ يُلتقط
    assert s["ask_walls"][0] == [100.3, 8.0], s["ask_walls"]
    assert len(s["bid_walls"]) == 3 and len(s["ask_walls"]) == 3
    # جهةٌ فارغة ⇒ لا نشر (يلزم وجود الجهتين).
    n = len(bus.out)
    await atom._on_depth({"symbol": "ETH_USDT", "bids": [], "asks": [[10.0, 1.0]]})
    assert len(bus.out) == n
    h = await atom.health_check()
    assert h.details["updates"] == 1
    print("OK 265 — الجدران: النسبةُ تتبع الأحجام، وأكبر جدارٍ يُلتقط لكلّ جهة (شاهد لا قاضٍ)")


if __name__ == "__main__":
    asyncio.run(main())
