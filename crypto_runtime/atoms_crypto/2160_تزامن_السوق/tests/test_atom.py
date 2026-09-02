"""اختبار تزامن السوق — market.candle 5د لعدّة رموز ⇒ sense.cross_market.state."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("xmkt", os.path.join(HERE, "..", "atom.py"))
xmkt = importlib.util.module_from_spec(spec); spec.loader.exec_module(xmkt)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=160, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)


CFG = {"timeframe": "5m", "reference": "BTC_USDT",
       "symbols": ["BTC_USDT", "ETH_USDT", "SOL_USDT"],
       "window_bars": 5, "min_move_pct": 0.3, "follow_ratio": 0.3,
       "diverge_pct": 0.2, "max_age_s": 600}


def _c(symbol, start, o, c):
    return {"provider": "MEXC", "symbol": symbol, "timeframe": "5m",
            "open": o, "close": c, "period_start": start, "closed": True}


async def _feed(cfg_moves):
    """cfg_moves: symbol → (open0, close_last). يُغذّى شمعتان لكلّ رمز."""
    bus = Bus(); atom = xmkt.Atom()
    await atom.initialize(bus.ctx(dict(CFG))); await atom.start()
    for symbol, (o0, c1) in cfg_moves.items():
        await atom._on_candle(_c(symbol, 300.0, o0, o0))     # افتتاح النافذة
        await atom._on_candle(_c(symbol, 600.0, c1, c1))     # إغلاق النافذة
    return atom, [p for e, p in bus.out if e == "sense.cross_market.state"][-1]


async def main():
    # ① تزامن تامّ: الكلّ يصعد بمقادير متقاربة ⇒ systemic.
    _, s = await _feed({"BTC_USDT": (100.0, 101.0), "ETH_USDT": (100.0, 101.2),
                        "SOL_USDT": (100.0, 100.8)})
    assert s["regime"] == "systemic", s["regime"]
    assert s["synced_count"] == 2 and s["btc_solo"] is False
    assert s["peers"]["ETH"]["status"] == "follow"
    assert abs(s["ref_change_pct"] - 1.0) < 1e-6

    # ② تفرّد المرجع: BTC يصعد والأتباع سكون ⇒ local · btc_solo.
    _, s = await _feed({"BTC_USDT": (100.0, 101.0), "ETH_USDT": (100.0, 100.0),
                        "SOL_USDT": (100.0, 100.0)})
    assert s["regime"] == "local", s["regime"]
    assert s["btc_solo"] is True and s["synced_count"] == 0
    assert s["peers"]["ETH"]["status"] == "lag"

    # ③ انشقاق: تابعٌ يعاكس المرجع بنشاط ⇒ mixed ويُدرَج في divergence.
    _, s = await _feed({"BTC_USDT": (100.0, 101.0), "ETH_USDT": (100.0, 101.0),
                        "SOL_USDT": (100.0, 98.5)})
    assert s["regime"] == "mixed", s["regime"]
    assert "SOL" in s["divergence"] and s["peers"]["SOL"]["status"] == "diverge"
    assert s["peers"]["ETH"]["status"] == "follow"

    # ④ المرجع ساكن (دون العتبة) ⇒ quiet، لا تشخيص.
    _, s = await _feed({"BTC_USDT": (100.0, 100.1), "ETH_USDT": (100.0, 101.0),
                        "SOL_USDT": (100.0, 101.0)})
    assert s["regime"] == "quiet", s["regime"]

    print("OK 160 — cross-market: تزامن=نظاميّ · تفرّد=محليّ · معاكسة=انشقاق · سكون=هادئ")


if __name__ == "__main__":
    asyncio.run(main())
