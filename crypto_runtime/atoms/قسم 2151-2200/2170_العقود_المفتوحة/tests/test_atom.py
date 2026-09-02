"""اختبار العقود المفتوحة — market.oi + market.candle ⇒ sense.oi.state.

يفحص الرباعيّات: سعر↑OI↑ لونغات جديدة، سعر↓OI↓ تصفية لونغات، وأنّ أوّل
قراءةٍ صالحة مرجعٌ فقط (بلا نشر)."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("oi", os.path.join(HERE, "..", "atom.py"))
oi = importlib.util.module_from_spec(spec); spec.loader.exec_module(oi)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=170, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def _last(bus):
    rows = [p for e, p in bus.out if e == "sense.oi.state"]
    return rows[-1] if rows else None


async def main():
    bus = Bus(); atom = oi.Atom()
    await atom.initialize(bus.ctx({"noise_pct": 0.0, "max_age_s": 60}))
    await atom.start()

    # سعرٌ أوّليّ ثم أوّل OI ⇒ مرجعٌ فقط، لا نشر.
    await atom._on_candle({"symbol": "BTC_USDT", "provider": "MEXC", "close": 100.0})
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC", "oi": 1000.0})
    assert _last(bus) is None, "أوّل قراءة صالحة مرجعٌ فقط"

    # السعر صعد والـOI صعد ⇒ لونغات جديدة (حركة صادقة).
    await atom._on_candle({"symbol": "BTC_USDT", "provider": "MEXC", "close": 101.0})
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC", "oi": 1100.0})
    s = _last(bus)
    assert s["quadrant"] == "new_longs" and s["honesty"] == "honest", s
    assert s["d_oi_pct"] > 0 and s["d_price_pct"] > 0

    # السعر هبط والـOI هبط ⇒ تصفية لونغات (شلّال).
    await atom._on_candle({"symbol": "BTC_USDT", "provider": "MEXC", "close": 100.0})
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC", "oi": 1050.0})
    s2 = _last(bus)
    assert s2["quadrant"] == "long_liquidation" and s2["honesty"] == "cascade", s2

    # السعر صعد والـOI هبط ⇒ تغطية شورتات (صعود هشّ).
    await atom._on_candle({"symbol": "BTC_USDT", "provider": "MEXC", "close": 101.0})
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC", "oi": 1040.0})
    s3 = _last(bus)
    assert s3["quadrant"] == "short_covering" and s3["honesty"] == "fragile", s3

    h = await atom.health_check()
    assert h.details["updates"] == 3 and h.state.value == "healthy"
    print("OK 170 — OI quadrants: الرباعيّات الأربع تُصنَّف، والقراءة الأولى مرجعٌ فقط")


if __name__ == "__main__":
    asyncio.run(main())
