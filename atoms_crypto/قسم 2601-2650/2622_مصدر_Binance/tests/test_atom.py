"""اختبار جسر بايننس — بلا شبكة. يُرقّع `_get` بردودٍ ثابتة."""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("binance", os.path.join(HERE, "..", "atom.py"))
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self): self.out = []
    def ctx(self, cfg):
        async def pub(e, p): self.out.append((e, p))
        return AtomContext(atom_id=622, config=cfg, logger=Log(), publish=pub, subscribe=lambda *a, **k: None)

    def of(self, event): return [p for e, p in self.out if e == event]


async def main():
    bus = Bus(); atom = mod.Atom()
    await atom.initialize(bus.ctx({
        "symbols": ["BTC_USDT", "PEPE_USDT", "GHOST_USDT"],
        "symbol_map": {"PEPE_USDT": "1000PEPEUSDT"},
        "poll_s": 10, "max_age_s": 60}))
    assert atom._binance_symbol("BTC_USDT") == "BTCUSDT", "تحويلٌ افتراضيّ بلا شرطة سفلية"
    assert atom._binance_symbol("PEPE_USDT") == "1000PEPEUSDT", "تحويلٌ مُخصَّص عبر symbol_map"
    atom._running = True   # حالة التشغيل بلا إطلاق حلقة الشبكة الخلفية (start())

    # ── premiumIndex: تمويل + علاوة من mark/index ──────────────────────
    def fake_ok(url):
        if "premiumIndex" in url and "BTCUSDT" in url:
            return {"symbol": "BTCUSDT", "markPrice": "80100.0", "indexPrice": "80000.0",
                    "lastFundingRate": "0.0001"}
        if "openInterest" in url and "BTCUSDT" in url:
            return {"symbol": "BTCUSDT", "openInterest": "108819.6"}
        raise RuntimeError("HTTP 400: symbol not found")
    mod._get = fake_ok
    await atom._poll_premium("BTC_USDT")
    await atom._poll_oi("BTC_USDT")
    prem = bus.of("feed.binance.premium")[-1]
    assert prem["symbol"] == "BTC_USDT" and prem["provider"] == "BINANCE"
    assert abs(prem["funding_pct"] - 0.01) < 1e-9, prem            # 0.0001×100
    assert abs(prem["premium_bps"] - 12.5) < 1e-9, prem            # (80100-80000)/80000×1e4
    oi = bus.of("feed.binance.oi")[-1]
    assert oi["symbol"] == "BTC_USDT" and oi["oi"] == 108819.6

    # ── رمزٌ غير مُدرَج (GHOST) ⇒ يُسجَّل فشله ولا يُسقط الذرّة ──────────
    await atom._poll_premium("GHOST_USDT")
    await atom._poll_oi("GHOST_USDT")
    assert "GHOST_USDT" in atom._failed, "الفشل يُسجَّل بلا استثناءٍ يُسقط الحلقة"
    h = await atom.health_check()
    assert h.state.value == "healthy", "نجاح رمزٍ واحد كافٍ للصحّة رغم فشل آخر"
    assert h.details["failed"]["GHOST_USDT"]

    # ── نجاحٌ لاحق لنفس الرمز يمسح فشله السابق ──────────────────────────
    def fake_recovered(url):
        if "GHOST" in url and "premiumIndex" in url:
            return {"markPrice": "1.0", "indexPrice": "1.0", "lastFundingRate": "0.0"}
        return fake_ok(url)
    mod._get = fake_recovered
    await atom._poll_premium("GHOST_USDT")
    assert "GHOST_USDT" not in atom._failed, "النجاح يمسح الفشل المسجَّل سابقًا لنفس الرمز"

    print("OK 622 — جسر بايننس: تحويل الرموز، صيغة العلاوة/التمويل، عزل فشل رمزٍ واحد")

    # ── تتبُّع كون 1001 الحيّ ────────────────────────────────────────────
    await atom._on_membership({"symbols": ["BTC_USDT", "NEWCOIN_USDT"]})
    assert atom._symbols == ["BTC_USDT", "NEWCOIN_USDT"], "الدورة التالية تقرأ القائمة الجديدة تلقائيًّا"
    await atom._on_membership({"symbols": []})
    assert atom._symbols == ["BTC_USDT", "NEWCOIN_USDT"], "حمولةٌ فارغة لا تُفرِغ القائمة الحالية"
    print("OK 622 — يتبع كون 1001 الحيّ بلا إعادة تشغيل")


if __name__ == "__main__":
    asyncio.run(main())
