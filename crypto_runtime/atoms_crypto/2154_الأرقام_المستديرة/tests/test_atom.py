"""اختبار الأرقام المستديرة — market.candle ⇒ sense.round_numbers.state.

يتحقّق من: أقرب مستوى كبير/وسط/صغير، والمسافة، ودور «معزِّز فقط»."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("rn", os.path.join(HERE, "..", "atom.py"))
rn = importlib.util.module_from_spec(spec); spec.loader.exec_module(rn)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=154, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


async def main():
    bus = Bus(); atom = rn.Atom()
    await atom.initialize(bus.ctx({}))   # خطوات BTC الافتراضية 1000/500/100
    await atom.start()
    # السعر 80,300 ⇒ الكبرى بين 80,000 و81,000، الأقرب 80,000 على بُعد 300
    await atom._on_candle({"symbol": "BTC_USDT", "provider": "MEXC", "timeframe": "5m",
                           "close": 80300.0, "period_start": 0})
    s = [p for e, p in bus.out if e == "sense.round_numbers.state"][-1]
    assert s["major"]["below"] == 80000 and s["major"]["above"] == 81000
    assert s["major"]["nearest"] == 80000 and s["major"]["dist"] == 300
    assert s["mid"]["nearest"] == 80500 and s["mid"]["dist"] == 200      # 80500 أقرب من 80000
    assert s["minor"]["nearest"] == 80300 and s["minor"]["dist"] == 0    # على مئةٍ تمامًا
    assert s["role"] == "confluence_only", "لا يُتاجَر وحده"
    # 80,800 ⇒ الأقرب كبيرًا هو 81,000 (المسافة 200 < 800)
    await atom._on_candle({"symbol": "BTC_USDT", "provider": "MEXC", "timeframe": "5m",
                           "close": 80800.0, "period_start": 300})
    s2 = [p for e, p in bus.out if e == "sense.round_numbers.state"][-1]
    assert s2["major"]["nearest"] == 81000 and s2["major"]["dist"] == 200
    h = await atom.health_check()
    assert h.details["updates"] == 2
    print("OK 154 — الأرقام المستديرة: الأقرب والمسافة صحيحان، والدور معزِّز فقط")


if __name__ == "__main__":
    asyncio.run(main())
