"""اختبار التمويل — market.funding ⇒ sense.funding.state.

يفحص التصنيف: لونغات مزدحمة (>0.010)، شورتات مزدحمة (<−0.003)، محايد،
واشتقاق funding_pct من funding_rate عند غيابه."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("fund", os.path.join(HERE, "..", "atom.py"))
fund = importlib.util.module_from_spec(spec); spec.loader.exec_module(fund)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=172, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


def _last(bus):
    return [p for e, p in bus.out if e == "sense.funding.state"][-1]


async def main():
    bus = Bus(); atom = fund.Atom()
    await atom.initialize(bus.ctx({"max_age_s": 60}))
    await atom.start()

    # تمويل مرتفع ⇒ لونغات مزدحمة، واللونغ يدفع.
    await atom._on_funding({"symbol": "BTC_USDT", "provider": "MEXC",
                            "funding_rate": 0.0002, "funding_pct": 0.02})
    s = _last(bus)
    assert s["bias"] == "longs_crowded" and s["pays"] == "longs_pay_shorts", s

    # تمويل سالب ⇒ شورتات مزدحمة، والشورت يدفع.
    await atom._on_funding({"symbol": "BTC_USDT", "provider": "MEXC",
                            "funding_rate": -0.0001, "funding_pct": -0.01})
    s2 = _last(bus)
    assert s2["bias"] == "shorts_crowded" and s2["pays"] == "shorts_pay_longs", s2

    # قرب المحايد ⇒ محايد.
    await atom._on_funding({"symbol": "BTC_USDT", "provider": "MEXC", "funding_pct": 0.004})
    assert _last(bus)["bias"] == "neutral"

    # اشتقاق funding_pct من المعدل الخام عند غياب funding_pct.
    await atom._on_funding({"symbol": "ETH_USDT", "provider": "MEXC", "funding_rate": 0.00015})
    s3 = _last(bus)
    assert abs(s3["funding_pct"] - 0.015) < 1e-9 and s3["bias"] == "longs_crowded", s3

    h = await atom.health_check()
    assert h.details["updates"] == 4 and h.state.value == "healthy"
    print("OK 172 — funding: تصنيف الازدحام على العتبات المقيسة، واشتقاق٪ من المعدل")


if __name__ == "__main__":
    asyncio.run(main())
