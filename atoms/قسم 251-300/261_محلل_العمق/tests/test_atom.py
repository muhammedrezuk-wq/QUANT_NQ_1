import asyncio
import importlib.util
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location(
    "_t261", Path(__file__).resolve().parents[1] / "atom.py")
m = importlib.util.module_from_spec(spec)
sys.modules["_t261"] = m
spec.loader.exec_module(m)


class L:
    def __getattr__(self, n): return lambda *a, **k: None


class B:
    def __init__(self): self.e = []
    def subscribe(self, *a): pass
    async def publish(self, n, p): self.e.append((n, p))


async def _new(cfg=None):
    b = B(); a = m.Atom()
    await a.initialize(m.AtomContext(261, cfg or {"weight": 20.0, "required_levels": 3.0},
                                     L(), b.publish, b.subscribe))
    await a.start()
    return a, b


def _depth(imbalance=0.6, levels=5, symbol="NQ"):
    return {"symbol": symbol, "account_id": "A", "broker": "BR",
            "provider": "CTRADER", "levels": levels, "imbalance": imbalance,
            "bid_volume": 160.0, "ask_volume": 40.0, "mid": 64000.0,
            "spread": 0.5, "best_bid": 63999.75, "best_ask": 64000.25,
            "timestamp": 1000.0}


async def test_card_from_snapshot_honest():
    a, b = await _new()
    await a._on_depth(_depth(imbalance=0.6, levels=5))
    out = [p for n, p in b.e if n == m.EVENT_OUT]
    assert out, "لم تُنشر البطاقة"
    c = out[-1]
    assert c["direction"] == 60.0 and c["signal"] == "buy", c["direction"]
    assert c["strength"] == 60.0 and c["confidence"] == 50.0
    assert c["ready"] is True and c["state"] == "READY"
    assert c["metadata"]["levels"] == 5 and c["metadata"]["bid_volume"] == 160.0
    print("OK — بطاقة من لقطة العمق: اتجاه 60% شراء وكل رقم من اللقطة نفسها")


async def test_sell_side_and_neutral():
    a, b = await _new()
    await a._on_depth(_depth(imbalance=-0.45, levels=4))
    c = [p for n, p in b.e if n == m.EVENT_OUT][-1]
    assert c["direction"] == -45.0 and c["signal"] == "sell"
    await a._on_depth(_depth(imbalance=0.0, levels=4))
    c2 = [p for n, p in b.e if n == m.EVENT_OUT][-1]
    assert c2["direction"] == 0.0 and c2["signal"] == "neutral"
    print("OK — بيع -45% ومحايد 0 كلاهما صادق")


async def test_not_ready_below_required_levels():
    a, b = await _new({"weight": 20.0, "required_levels": 8.0})
    await a._on_depth(_depth(levels=3))
    c = [p for n, p in b.e if n == m.EVENT_OUT][-1]
    assert c["ready"] is False and c["state"] == "NOT_READY"
    assert c["weight_applied"] == 0.0
    print("OK — أقل من المستويات المطلوبة: NOT_READY والوزن لا يُطبَّق")


async def test_health_and_rejects():
    a, b = await _new()
    h0 = await a.health_check()
    assert h0.state.value == "DEGRADED" or "NO_DEPTH" in h0.message
    await a._on_depth({"symbol": "", "levels": 0})
    h = await a.health_check()
    assert h.details["rejected"] == 1 and h.details["received"] == 1
    print("OK — الصحة: بلا لقطات DEGRADED، والمرفوض معدود")


async def main():
    await test_card_from_snapshot_honest()
    await test_sell_side_and_neutral()
    await test_not_ready_below_required_levels()
    await test_health_and_rejects()
    print("261 depth analyzer tests passed")


if __name__ == "__main__":
    asyncio.run(main())
