import asyncio
import importlib.util
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location(
    "_t262", Path(__file__).resolve().parents[1] / "atom.py")
m = importlib.util.module_from_spec(spec)
sys.modules["_t262"] = m
spec.loader.exec_module(m)


class L:
    def __getattr__(self, n): return lambda *a, **k: None


class B:
    def __init__(self): self.e = []
    def subscribe(self, *a): pass
    async def publish(self, n, p): self.e.append((n, p))


async def _new(cfg=None):
    b = B(); a = m.Atom()
    await a.initialize(m.AtomContext(262, cfg or {"weight": 20.0, "required_levels": 3.0},
                                     L(), b.publish, b.subscribe))
    await a.start()
    return a, b


def _depth(qb=160.0, qa=40.0, levels=5, symbol="NQ"):
    # أفضل مستوى يحمل الحجمين المختبَرين، والبقيّة حشوٌ صحيح الترتيب.
    bids = [[63999.75, qb], [63999.50, 20.0], [63999.25, 15.0],
            [63999.00, 10.0], [63998.75, 5.0]]
    asks = [[64000.25, qa], [64000.50, 20.0], [64000.75, 15.0],
            [64001.00, 10.0], [64001.25, 5.0]]
    return {"symbol": symbol, "account_id": "A", "broker": "BR", "provider": "CTRADER",
            "bids": bids, "asks": asks, "levels": levels, "timestamp": 1000.0}


async def test_buy_pressure_micro_toward_ask():
    a, b = await _new()
    await a._on_depth(_depth(qb=160.0, qa=40.0))     # طلبٌ ثقيل ⇒ العادل نحو العرض
    out = [p for n, p in b.e if n == m.EVENT_OUT]
    assert out, "لم تُنشر البطاقة"
    c = out[-1]
    # micro = (63999.75·40 + 64000.25·160)/200 = 64000.15 ⇒ موضعه +60% من نصف السبريد
    assert c["metadata"]["microprice"] == 64000.15, c["metadata"]["microprice"]
    assert c["direction"] == 60.0 and c["signal"] == "buy"
    assert c["strength"] == 60.0 and c["confidence"] == 50.0
    assert c["ready"] is True and c["state"] == "READY"
    assert c["metadata"]["method"] == "microprice_stoikov"
    print("OK — ضغط الشراء يرفع العادل نحو العرض: اتجاه +60% من الدفتر وحده")


async def test_sell_pressure_and_neutral():
    a, b = await _new()
    await a._on_depth(_depth(qb=40.0, qa=160.0))     # عرضٌ ثقيل ⇒ العادل نحو الطلب
    c = [p for n, p in b.e if n == m.EVENT_OUT][-1]
    assert c["direction"] == -60.0 and c["signal"] == "sell"
    await a._on_depth(_depth(qb=100.0, qa=100.0))    # تماثل ⇒ العادل = المنتصف
    c2 = [p for n, p in b.e if n == m.EVENT_OUT][-1]
    assert c2["direction"] == 0.0 and c2["signal"] == "neutral"
    assert c2["metadata"]["microprice"] == c2["metadata"]["mid"]
    print("OK — بيع -60% ومحايد 0 (العادل=المنتصف عند التماثل)")


async def test_not_ready_below_required_levels():
    a, b = await _new({"weight": 20.0, "required_levels": 8.0})
    await a._on_depth(_depth(levels=3))
    c = [p for n, p in b.e if n == m.EVENT_OUT][-1]
    assert c["ready"] is False and c["state"] == "NOT_READY"
    assert c["weight_applied"] == 0.0
    print("OK — أقل من المستويات المطلوبة: NOT_READY والوزن لا يُطبَّق")


async def test_reject_crossed_and_empty():
    a, b = await _new()
    await a._on_depth({"symbol": "NQ", "account_id": "A", "broker": "BR",
                       "bids": [[64001.0, 10.0]], "asks": [[64000.0, 10.0]], "levels": 1})
    await a._on_depth({"symbol": "NQ", "bids": [], "asks": [], "levels": 0})
    out = [p for n, p in b.e if n == m.EVENT_OUT]
    assert not out, "الدفتر المتقاطع/الفارغ لا يُنشر"
    h = await a.health_check()
    assert h.details["rejected"] == 2 and h.details["received"] == 2
    print("OK — الدفتر المتقاطع والفارغ مرفوضان معدودان، بلا نشر مخترع")


async def main():
    await test_buy_pressure_micro_toward_ask()
    await test_sell_pressure_and_neutral()
    await test_not_ready_below_required_levels()
    await test_reject_crossed_and_empty()
    print("262 microprice analyzer tests passed")


if __name__ == "__main__":
    asyncio.run(main())
