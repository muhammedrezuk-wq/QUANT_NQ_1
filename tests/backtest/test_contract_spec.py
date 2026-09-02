# -*- coding: utf-8 -*-
"""اختبار مواصفات العقود — المرحلة 10.

- NQ100 ≠ EURUSD ≠ BTCUSD
- لا قيم ثابتة عامة
- PnL صحيح حسب المواصفات
- Product Gate FAIL بدون مواصفات
"""
from __future__ import annotations

import sys
sys.path.insert(0, '/home/user/QUANT_NQ')

from backtest.contract_spec import (
    BTCUSD, EURUSD, NQ100, XAUUSD,
    ContractSpec, get_contract_spec,
    require_contract_spec, register_contract_spec,
    list_contract_specs,
)


def test_nq100_tick_size():
    """NQ100 tick_size = 0.25 — ليس 0.0001."""
    assert NQ100.tick_size == 0.25
    assert NQ100.tick_size != 0.0001
    print("  ✓ NQ100 tick_size = 0.25")


def test_eurusd_tick_size():
    """EURUSD tick_size = 0.00001."""
    assert EURUSD.tick_size == 0.00001
    print("  ✓ EURUSD tick_size = 0.00001")


def test_btcusd_tick_size():
    """BTCUSD tick_size = 0.01."""
    assert BTCUSD.tick_size == 0.01
    print("  ✓ BTCUSD tick_size = 0.01")


def test_different_specs_not_equal():
    """كل أصل له مواصفاته الخاصة — لا قيم ثابتة عامة."""
    assert NQ100.tick_size != EURUSD.tick_size
    assert EURUSD.tick_size != BTCUSD.tick_size
    assert NQ100.tick_value != EURUSD.tick_value
    print("  ✓ مواصفات مختلفة لكل أصل")


def test_pnl_calculation():
    """PnL = (ticks) × tick_value × lots."""
    # NQ100: دخول 15000، خروج 15100، 1 lot
    # Ticks = 100 / 0.25 = 400 ticks
    # PnL = 400 × $5 × 1 = $2000
    pnl = NQ100.pnl(15000, 15100, 1, "long")
    assert abs(pnl - 2000.0) < 0.01, f"Expected 2000, got {pnl}"
    print(f"  ✓ NQ100 PnL: 100 نقطة → ${pnl:.0f}")


def test_pnl_short():
    """PnL للشورت = عكس اللونج."""
    pnl_long = EURUSD.pnl(1.1000, 1.1100, 1, "long")
    pnl_short = EURUSD.pnl(1.1000, 1.1100, 1, "short")
    assert pnl_long > 0 and pnl_short < 0
    assert abs(pnl_long) == abs(pnl_short)
    print(f"  ✓ Short = -Long: {pnl_long:.2f} / {pnl_short:.2f}")


def test_commission():
    """Commission = commission_per_lot × lots."""
    comm = NQ100.commission(2)
    assert comm == NQ100.commission_per_lot * 2
    print(f"  ✓ Commission: 2 lots = ${comm:.2f}")


def test_unknown_symbol_returns_none():
    """رمز غير معروف = None — Product Gate FAIL."""
    assert get_contract_spec("UNKNOWN_XYZ") is None
    print("  ✓ Unknown symbol → None")


def test_require_raises_for_unknown():
    """require_contract_spec يرمي ValueError لرمز مجهول."""
    try:
        require_contract_spec("UNKNOWN_XYZ")
        assert False, "يجب أن يرمي ValueError"
    except ValueError as e:
        assert "CONTRACT_SPEC_MISSING" in str(e)
        print("  ✓ require() يرمي لرمز مجهول")


def test_aliases_resolve():
    """USTEC/NAS100/US100 → NQ100."""
    assert get_contract_spec("USTEC") is NQ100
    assert get_contract_spec("NAS100") is NQ100
    assert get_contract_spec("US100") is NQ100
    print("  ✓ Aliases: USTEC/NAS100/US100 → NQ100")


def test_register_custom_spec():
    """تسجيل مواصفات مخصصة."""
    custom = ContractSpec(
        symbol="CUSTOM_TEST",
        tick_size=0.5,
        tick_value=10.0,
        contract_size=1.0,
    )
    register_contract_spec(custom)
    fetched = get_contract_spec("CUSTOM_TEST")
    assert fetched is custom
    print("  ✓ Custom spec registered")


def test_validate_rejects_zero_tick():
    """tick_size = 0 → ValueError."""
    bad = ContractSpec(symbol="BAD", tick_size=0, tick_value=1, contract_size=1)
    try:
        bad.validate()
        assert False, "يجب أن يرمي ValueError"
    except ValueError:
        print("  ✓ tick_size=0 مرفوض")


_ALL_TESTS = [
    test_nq100_tick_size,
    test_eurusd_tick_size,
    test_btcusd_tick_size,
    test_different_specs_not_equal,
    test_pnl_calculation,
    test_pnl_short,
    test_commission,
    test_unknown_symbol_returns_none,
    test_require_raises_for_unknown,
    test_aliases_resolve,
    test_register_custom_spec,
    test_validate_rejects_zero_tick,
]


def run() -> int:
    passed = 0
    failed = 0
    for test in _ALL_TESTS:
        try:
            test()
            passed += 1
            print(f"✓ {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"✗ {test.__name__}: {exc}")
    print(f"\n{'='*50}")
    print(f"المرحلة ١٠ — Contract Spec: {passed} نجح · {failed} فشل")
    return failed


if __name__ == "__main__":
    sys.exit(run())
