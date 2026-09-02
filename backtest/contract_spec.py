# -*- coding: utf-8 -*-
"""مواصفات العقود — Contract Specifications.

المرحلة 10 من ورقة الإغلاق.

كل أصل له مواصفاته الخاصة:
  - tick_size: أصغر حركة سعرية
  - tick_value: قيمة التيك بالدولار
  - contract_size: حجم العقد
  - pip_size: حجم النقطة (للأزواج)
  - pip_value: قيمة النقطة بالدولار

ممنوع استخدام قيم ثابتة (0.0001) لكل الرموز.
Product Gate المالي يجب أن يفشل إذا كانت بيانات التكلفة مفقودة.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContractSpec:
    """مواصفات عقد واحد."""
    symbol: str
    tick_size: float          # أصغر حركة سعرية
    tick_value: float         # قيمة التيك بالدولار (لعقد واحد)
    contract_size: float      # حجم العقد (وحدات الأصل)
    pip_size: float = 0.0     # حجم النقطة (0 = غير مطبق)
    pip_value: float = 0.0    # قيمة النقطة بالدولار
    min_lot: float = 0.01     # أصغر حجم
    max_lot: float = 100.0    # أكبر حجم
    lot_step: float = 0.01    # درجة الحجم
    commission_per_lot: float = 0.0  # عمولة لكل.lot
    swap_long: float = 0.0    # swap للونج (سنوي، %)
    swap_short: float = 0.0   # swap للشورت (سنوي، %)
    spread_default: float = 0.0  # سبريد افتراضي (بالتيكات)

    def validate(self) -> None:
        """فحص صحة المواصفات."""
        if self.tick_size <= 0:
            raise ValueError(f"INVALID_TICK_SIZE: {self.symbol} tick_size={self.tick_size}")
        if self.tick_value <= 0:
            raise ValueError(f"INVALID_TICK_VALUE: {self.symbol} tick_value={self.tick_value}")
        if self.contract_size <= 0:
            raise ValueError(f"INVALID_CONTRACT_SIZE: {self.symbol} contract_size={self.contract_size}")

    def pnl(self, entry_price: float, exit_price: float, lots: float,
            direction: str = "long") -> float:
        """احسب PnL بالعقود."""
        price_diff = (exit_price - entry_price) if direction == "long" else (entry_price - exit_price)
        ticks = price_diff / self.tick_size
        return ticks * self.tick_value * lots

    def commission(self, lots: float) -> float:
        """احسب العمولة."""
        return self.commission_per_lot * lots

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "tick_size": self.tick_size,
            "tick_value": self.tick_value,
            "contract_size": self.contract_size,
            "pip_size": self.pip_size,
            "pip_value": self.pip_value,
            "min_lot": self.min_lot,
            "max_lot": self.max_lot,
            "lot_step": self.lot_step,
            "commission_per_lot": self.commission_per_lot,
        }


# ═══════════════════════════════════════════════════════════════════
# مواصفات الأصول المعروفة
# ═══════════════════════════════════════════════════════════════════

# NQ100 / USTEC — NQ futures equivalent
# Tick = 0.25 index points, $5 per tick (per contract)
NQ100 = ContractSpec(
    symbol="NQ100",
    tick_size=0.25,
    tick_value=5.0,
    contract_size=20.0,        # $20 per index point
    pip_size=1.0,
    pip_value=20.0,
    min_lot=0.01,
    max_lot=50.0,
    lot_step=0.01,
    commission_per_lot=1.50,   # $1.50 per lot round turn
    spread_default=4,          # 4 ticks
)

# EUR/USD — standard forex lot
# Pip = 0.0001, $10 per pip (standard lot 100,000)
EURUSD = ContractSpec(
    symbol="EURUSD",
    tick_size=0.00001,
    tick_value=0.10,           # $0.10 per tick per standard lot
    contract_size=100_000.0,
    pip_size=0.0001,
    pip_value=10.0,
    min_lot=0.01,
    max_lot=100.0,
    lot_step=0.01,
    commission_per_lot=3.50,   # $3.50 per lot round turn
    spread_default=1,          # 1 pip
)

# BTC/USD — crypto
# Tick = $0.01, $0.01 per tick per 1 BTC contract
BTCUSD = ContractSpec(
    symbol="BTCUSD",
    tick_size=0.01,
    tick_value=0.01,           # $0.01 per tick per 1 BTC
    contract_size=1.0,
    pip_size=1.0,
    pip_value=1.0,
    min_lot=0.001,
    max_lot=10.0,
    lot_step=0.001,
    commission_per_lot=0.0,    # no commission (crypto spread-based)
    spread_default=50,         # $50 default spread
)

# XAU/USD — Gold
# Tick = $0.01, $0.01 per tick per 1 oz
XAUUSD = ContractSpec(
    symbol="XAUUSD",
    tick_size=0.01,
    tick_value=0.01,
    contract_size=100.0,       # 100 oz per contract
    pip_size=0.1,
    pip_value=0.10,
    min_lot=0.01,
    max_lot=50.0,
    lot_step=0.01,
    commission_per_lot=3.0,
    spread_default=30,         # 30 ticks = $0.30
)


# ═══════════════════════════════════════════════════════════════════
# Registry — سجل المواصفات
# ═══════════════════════════════════════════════════════════════════

_REGISTRY: dict[str, ContractSpec] = {
    "NQ100": NQ100,
    "USTEC": NQ100,
    "NAS100": NQ100,
    "US100": NQ100,
    "EURUSD": EURUSD,
    "EUR/USD": EURUSD,
    "BTCUSD": BTCUSD,
    "BTCUSDT": BTCUSD,
    "BTC-USDT": BTCUSD,
    "XAUUSD": XAUUSD,
    "GOLD": XAUUSD,
}


def get_contract_spec(symbol: str) -> ContractSpec | None:
    """أعد مواصفات العقد لرمز معيّن.

    None = غير معروف — Product Gate يجب أن يرفض.
    """
    return _REGISTRY.get(symbol.upper())


def require_contract_spec(symbol: str) -> ContractSpec:
    """أعد المواصفات أو ارمِ ValueError."""
    spec = get_contract_spec(symbol)
    if spec is None:
        raise ValueError(
            f"CONTRACT_SPEC_MISSING: {symbol} — "
            f"لا توجد مواصفات عقد. "
            f"Product Gate FAIL: لا يمكن حساب التكاليف بدون مواصفات."
        )
    spec.validate()
    return spec


def register_contract_spec(spec: ContractSpec) -> None:
    """سجّل مواصفات عقد جديدة."""
    spec.validate()
    _REGISTRY[spec.symbol.upper()] = spec


def list_contract_specs() -> list[dict[str, Any]]:
    """كل المواصفات المسجلة."""
    seen = set()
    result = []
    for key, spec in _REGISTRY.items():
        if id(spec) not in seen:
            seen.add(id(spec))
            result.append(spec.to_dict())
    return result
