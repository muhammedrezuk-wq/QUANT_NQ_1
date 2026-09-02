import asyncio
import importlib.util
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location(
    "_t264", Path(__file__).resolve().parents[1] / "atom.py")
m = importlib.util.module_from_spec(spec)
sys.modules["_t264"] = m
spec.loader.exec_module(m)


class L:
    def __getattr__(self, n): return lambda *a, **k: None


class B:
    def __init__(self): self.e = []
    def subscribe(self, *a): pass
    async def publish(self, n, p): self.e.append((n, p))


async def _new(cfg=None):
    b = B(); a = m.Atom()
    await a.initialize(m.AtomContext(
        264, cfg or {"weight": 20.0, "required_levels": 3.0, "impact_qty": 50.0},
        L(), b.publish, b.subscribe))
    await a.start()
    return a, b


def _book(bids, asks, symbol="NQ", levels=5):
    return {"symbol": symbol, "account_id": "A", "broker": "BR", "provider": "CTRADER",
            "bids": bids, "asks": asks, "levels": levels, "timestamp": 1000.0}


def _last(b):
    return [p for n, p in b.e if n == m.EVENT_OUT][-1]


THIN = lambda p, step, n, sz: [[round(p + step * i, 2), sz] for i in range(n)]


async def test_thin_asks_bullish():
    a, b = await _new()
    # عرضٌ رقيق (20/مستوى) مقابل طلبٍ كثيف (100) ⇒ اختراق الشراء أغلى ⇒ صعوديّ
    asks = THIN(100.10, 0.10, 5, 20.0)
    bids = THIN(100.00, -0.10, 5, 100.0)
    await a._on_depth(_book(bids, asks))
    c = _last(b)
    md = c["metadata"]
    assert md["method"] == "book_impact_kyle"
    assert md["impact_up_bps"] > md["impact_down_bps"], md      # عرضٌ رقيق ⇒ انزلاق شراء أعلى
    assert c["direction"] > 0 and c["signal"] == "buy" and c["state"] == "READY"
    assert md["filled_up"] is True and md["filled_down"] is True
    print("OK — عرضٌ رقيق: انزلاق الشراء أعلى ⇒ صعوديّ (يوافق 261)")


async def test_thin_bids_bearish():
    a, b = await _new()
    bids = THIN(100.00, -0.10, 5, 20.0)      # طلبٌ رقيق
    asks = THIN(100.10, 0.10, 5, 100.0)      # عرضٌ كثيف
    await a._on_depth(_book(bids, asks))
    c = _last(b)
    assert c["metadata"]["impact_down_bps"] > c["metadata"]["impact_up_bps"]
    assert c["direction"] < 0 and c["signal"] == "sell"
    print("OK — طلبٌ رقيق: انزلاق البيع أعلى ⇒ هبوطيّ")


async def test_insufficient_depth_no_fabrication():
    a, b = await _new()
    # جانب العرض لا يكفي لتنفيذ 50 ⇒ insufficient_data بلا اتجاه مخترع
    await a._on_depth(_book(THIN(100.00, -0.10, 5, 100.0), [[100.10, 10.0]]))
    c = _last(b)
    assert c["status"] == "insufficient_data" and c["state"] == "NOT_READY"
    assert c.get("direction") is None and c["metadata"]["filled_up"] is False
    print("OK — عمقٌ لا يكفي التنفيذ: insufficient_data صادق بلا اتجاه")


async def test_reject_crossed():
    a, b = await _new()
    await a._on_depth(_book([[100.5, 10.0]], [[100.0, 10.0]], levels=1))
    assert not [p for n, p in b.e if n == m.EVENT_OUT], "متقاطع لا يُنشر"
    h = await a.health_check()
    assert h.details["rejected"] == 1
    print("OK — الدفتر المتقاطع مرفوض")


async def main():
    await test_thin_asks_bullish()
    await test_thin_bids_bearish()
    await test_insufficient_depth_no_fabrication()
    await test_reject_crossed()
    print("264 book price-impact tests passed")


if __name__ == "__main__":
    asyncio.run(main())
