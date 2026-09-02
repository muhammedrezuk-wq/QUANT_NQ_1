"""اختبار العلاوة — market.premium ⇒ sense.premium.state.

يفحص التصنيف (ساخنة/محايدة/ذعر) في النظام السالب، والاتجاه (recovering)،
والقيادة (المؤشر يقود = index) من عيّنتين متتاليتين."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("prem", os.path.join(HERE, "..", "atom.py"))
prem = importlib.util.module_from_spec(spec); spec.loader.exec_module(prem)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=173, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def _last(bus):
    return [p for e, p in bus.out if e == "sense.premium.state"][-1]


async def main():
    bus = Bus(); atom = prem.Atom()
    await atom.initialize(bus.ctx({"max_age_s": 60}))
    await atom.start()

    # علاوة عميقة −8 ⇒ ذعر/شورتات مزدحمة.
    await atom._on_premium({"symbol": "BTC_USDT", "provider": "MEXC",
                            "premium_bps": -8.0, "fair_price": 79986.0, "index_price": 80050.0})
    s = _last(bus)
    assert s["tier"] == "panic" and s["crowd"] == "shorts_crowded", s

    # علاوة تتعافى إلى −2 والمؤشر يصعد أكثر من العادل ⇒ ساخنة، recovering، index يقود.
    await atom._on_premium({"symbol": "BTC_USDT", "provider": "MEXC",
                            "premium_bps": -2.0, "fair_price": 80020.0, "index_price": 80120.0})
    s2 = _last(bus)
    assert s2["tier"] == "hot" and s2["trend"] == "recovering", s2
    assert s2["leader"] == "index", "المؤشر صعد أكثر ⇒ السبوت يقود"

    # علاوة محايدة −5.
    await atom._on_premium({"symbol": "ETH_USDT", "provider": "MEXC",
                            "premium_bps": -5.0, "fair_price": 3000.0, "index_price": 3001.5})
    assert _last(bus)["tier"] == "neutral"

    h = await atom.health_check()
    assert h.details["updates"] == 3 and h.state.value == "healthy"
    print("OK 173 — premium: تصنيف النظام السالب، الاتجاه، وقيادة المؤشر من عيّنتين")


if __name__ == "__main__":
    asyncio.run(main())
