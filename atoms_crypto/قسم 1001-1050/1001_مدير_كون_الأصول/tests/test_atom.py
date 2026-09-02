# -*- coding: utf-8 -*-
"""اختبارات 1001 — مدير كون الأصول (كريبتو، مرحلة A: اكتشاف/تصنيف فقط،
بلا حواس ولا استراتيجية ولا مخاطر). لا شبكة حية أبداً — atom._fetch مُموَّه
بكل اختبار؛ لا يُستدعى atom.start() كي لا تُتاح فرصة لحلقة الاستطلاع
الخلفية أن تلمس الشبكة الحقيقية قبل أن يضع الاختبار تمويهه.
"""
import asyncio
import sys
import tempfile
from pathlib import Path as _Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from core.contracts.atom import AtomContext  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom1001", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom1001"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_SNAPSHOT = _mod.EVENT_SNAPSHOT
EVENT_MEMBERSHIP = _mod.EVENT_MEMBERSHIP
EVENT_REJECTED = _mod.EVENT_REJECTED


class _NullLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def critical(self, *a, **k): pass


class FakeEventBus:
    def __init__(self):
        self.published = []

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.published.append((name, payload))


def _ticker(symbol, amount24, bid=100.0, ask=100.1, high=105.0, low=95.0):
    return {"symbol": symbol, "bid1": bid, "ask1": ask, "lastPrice": 100.0,
            "amount24": amount24, "high24Price": high, "lower24Price": low,
            "volume24": 1000.0, "timestamp": 1000}


def _detail(symbol, base=None):
    return {"symbol": symbol, "quoteCoin": "USDT", "settleCoin": "USDT",
            "priceUnit": 0.1, "baseCoin": base or symbol.replace("_USDT", "")}


async def _new(tmp_dir, core_target=1, outer_target=1, entry_resilience_days=0,
               **cfg_extra):
    bus = FakeEventBus()
    atom = Atom()
    cfg = {
        "core_target_count": core_target, "outer_target_count": outer_target,
        "entry_resilience_days": entry_resilience_days,
        "overrides_path": str(_Path(tmp_dir) / "overrides.json"),
        "membership_state_path": str(_Path(tmp_dir) / "membership.json"),
        "open_positions_path": str(_Path(tmp_dir) / "open_positions.json"),
        **cfg_extra,
    }
    await atom.initialize(AtomContext(atom_id=1001, config=cfg, logger=_NullLogger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    # عمداً بلا atom.start(): حلقة الاستطلاع الخلفية تلمس الشبكة الحقيقية،
    # و_scan_once لا تفحص self._running أصلاً فلا حاجة لها بالاختبار.
    return atom, bus


def _last(bus, event):
    rows = [p for n, p in bus.published if n == event]
    return rows[-1] if rows else None


async def test_ring_field_actually_set_core_vs_outer(tmp_path):
    print("\n--- test_ring_field_actually_set_core_vs_outer ---")
    atom, bus = await _new(tmp_path, core_target=1, outer_target=1)
    tickers = [
        _ticker("BTC_USDT", amount24=100_000_000.0),   # الأعلى سيولة -> core
        _ticker("ETH_USDT", amount24=50_000_000.0),    # الثاني -> outer
    ]
    details = [_detail("BTC_USDT"), _detail("ETH_USDT")]
    atom._fetch = lambda: (tickers, details)
    await atom._scan_once()
    snapshot = _last(bus, EVENT_SNAPSHOT)
    assert snapshot is not None and not snapshot.get("error"), snapshot
    core_rows = {row["symbol"]: row for row in snapshot["core"]}
    outer_rows = {row["symbol"]: row for row in snapshot["outer"]}
    # قبل الإصلاح: _normalize لا تضع "ring" إطلاقاً، فـ_rotate تقرأ
    # row.get("ring") or "outer" -- تصير "outer" دائماً حتى لصفّ BTC القادم
    # من شريحة core نفسها، فتبقى قائمة core المنشورة فارغة أبداً.
    assert "BTC_USDT" in core_rows, ("BTC لم يدخل core رغم كونه الأعلى"
                                     " سيولة: %r" % snapshot)
    assert core_rows["BTC_USDT"]["ring"] == "core", core_rows["BTC_USDT"]
    assert "ETH_USDT" in outer_rows, snapshot
    assert outer_rows["ETH_USDT"]["ring"] == "outer", outer_rows["ETH_USDT"]
    membership = _last(bus, EVENT_MEMBERSHIP)
    assert any(row["symbol"] == "BTC_USDT" for row in membership["core"]), membership
    print("OK — BTC (الأعلى سيولة) دخل core فعلاً بعلامة ring صحيحة؛ ETH دخل outer")


async def test_non_crypto_and_low_liquidity_rejected(tmp_path):
    print("\n--- test_non_crypto_and_low_liquidity_rejected ---")
    atom, bus = await _new(tmp_path, core_target=5, outer_target=5)
    tickers = [
        _ticker("BTC_USDT", amount24=100_000_000.0),
        _ticker("XAUUSDT", amount24=200_000_000.0),   # يحمل ماركر غير-كريبتو (XAU)
        _ticker("DUST_USDT", amount24=1_000.0),        # سيولة تحت حد outer
    ]
    details = [_detail("BTC_USDT"), _detail("XAUUSDT", base="XAU"), _detail("DUST_USDT")]
    atom._fetch = lambda: (tickers, details)
    await atom._scan_once()
    snapshot = _last(bus, EVENT_SNAPSHOT)
    accepted = {row["symbol"] for row in snapshot["core"] + snapshot["outer"]}
    rejected = {row["symbol"]: row["reasons"] for row in snapshot["rejected"]}
    assert "BTC_USDT" in accepted, snapshot
    assert "XAUUSDT" not in accepted and "NON_CRYPTO" in rejected.get("XAUUSDT", []), rejected
    assert "DUST_USDT" not in accepted and "LIQUIDITY_BELOW_OUTER" in rejected.get("DUST_USDT", []), rejected
    print("OK — غير الكريبتو (ماركر الرمز) وضعيف السيولة يُرفضان بسببهما الصريح")


async def test_classification_uses_concept_plate_not_fragile_symbol_markers(tmp_path):
    """v1.2.0: عيّنتان حقيقيتان مأخوذتان حرفياً من MEXC الحيّة (تحقّق
    ٢٠٢٦-٠٨-٢٧ ضد كل الـ1024 عقد USDT/USDT فعلياً، لا افتراضاً):
    HMSTR_USDT (عملة حقيقية) كانت تُرفض بالخطأ لأن "MSTR" ماركر رمز
    قديم يطابقها كسلسلة فرعية؛ وMETASTOCK_USDT (سهم مُرمَّز، META) كان
    يمرّ بالخطأ كـNATIVE_CRYPTO لأن "META" لم يكن بقائمة الماركرات
    أصلاً — من أصل ~390 سهماً/سلعة/عملة مُرمَّزة كانت تفلت بصمت."""
    print("\n--- test_classification_uses_concept_plate_not_fragile_symbol_markers ---")
    atom, bus = await _new(tmp_path, core_target=5, outer_target=5)
    tickers = [
        _ticker("BTC_USDT", amount24=100_000_000.0),
        _ticker("HMSTR_USDT", amount24=100_000_000.0),
        _ticker("METASTOCK_USDT", amount24=100_000_000.0),
        _ticker("SPY_USDT", amount24=100_000_000.0),
    ]
    details = [
        _detail("BTC_USDT"),
        {**_detail("HMSTR_USDT"), "conceptPlate": ["mc-trade-zone-GameFi"]},
        {**_detail("METASTOCK_USDT", base="METASTOCK"),
         "conceptPlate": ["mc-trade-zone-tradfi", "mc-trade-zone-Stock"]},
        {**_detail("SPY_USDT", base="SPY"),
         "conceptPlate": ["mc-trade-zone-tradfi", "mc-trade-zone-ETF"]},
    ]
    atom._fetch = lambda: (tickers, details)
    await atom._scan_once()
    snapshot = _last(bus, EVENT_SNAPSHOT)
    accepted = {row["symbol"] for row in snapshot["core"] + snapshot["outer"]}
    rejected = {row["symbol"]: row["reasons"] for row in snapshot["rejected"]}
    assert "BTC_USDT" in accepted, snapshot
    assert "HMSTR_USDT" in accepted, ("عملة حقيقية رُفضت بخطأ ماركر"
                                      " الرمز الفرعي (MSTR بـHMSTR): %r" % rejected)
    assert "METASTOCK_USDT" not in accepted, ("سهم مُرمَّز (META) دخل"
                                              " الكون كأنه عملة: %r" % accepted)
    assert "NON_CRYPTO" in rejected.get("METASTOCK_USDT", []), rejected
    assert "SPY_USDT" not in accepted and "NON_CRYPTO" in rejected.get("SPY_USDT", []), rejected
    print("OK — HMSTR (عملة حقيقية) دخلت رغم تطابق نصّي قديم؛ METASTOCK/SPY (أسهم/ETF مُرمَّزة) رُفضا فعلاً")


async def main():
    tests = [test_ring_field_actually_set_core_vs_outer,
             test_non_crypto_and_low_liquidity_rejected,
             test_classification_uses_concept_plate_not_fragile_symbol_markers]
    failed = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        for test in tests:
            try:
                await test(tmp_dir)
            except AssertionError as e:
                failed.append((test.__name__, str(e)))
                print(f"FAILED: {test.__name__}: {e}")
            except Exception as e:
                failed.append((test.__name__, repr(e)))
                print(f"ERROR: {test.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}")
        sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
