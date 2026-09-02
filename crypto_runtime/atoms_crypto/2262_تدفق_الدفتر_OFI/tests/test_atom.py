"""اختبار OFI — تغيّر أحجام أفضل مستوى ⇒ micro.ofi.state."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ofi", os.path.join(HERE, "..", "atom.py"))
ofi = importlib.util.module_from_spec(spec); spec.loader.exec_module(ofi)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=262, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


async def main():
    bus = Bus(); atom = ofi.Atom()
    await atom.initialize(bus.ctx({"window_s": 30, "max_age_s": 10}))
    await atom.start()
    # اللقطة الأولى: لا فرق بعد (تُخزَّن مرجعًا).
    await atom._on_depth({"symbol": "BTC_USDT", "bids": [[100.0, 10.0]], "asks": [[100.1, 10.0]]})
    assert not [p for e, p in bus.out if e == "micro.ofi.state"]
    # حجم الطلب زاد عند نفس السعر (+5) والعرض ثبت ⇒ OFI = +5 (ضغط شراء).
    await atom._on_depth({"symbol": "BTC_USDT", "bids": [[100.0, 15.0]], "asks": [[100.1, 10.0]]})
    s = [p for e, p in bus.out if e == "micro.ofi.state"][-1]
    assert s["instant"] == 5.0 and s["ofi"] == 5.0, s
    # سعر الطلب ارتفع ⇒ +الحجم الجديد؛ يبقى الاتجاه موجبًا.
    await atom._on_depth({"symbol": "BTC_USDT", "bids": [[100.05, 8.0]], "asks": [[100.1, 10.0]]})
    s2 = [p for e, p in bus.out if e == "micro.ofi.state"][-1]
    assert s2["ofi"] > 5.0
    print("OK 262 — OFI: نموّ الطلب موجب، وصعود سعر الطلب يراكم الضغط")


if __name__ == "__main__":
    asyncio.run(main())
