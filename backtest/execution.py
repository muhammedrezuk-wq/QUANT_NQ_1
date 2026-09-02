# -*- coding: utf-8 -*-
"""Execution Adapter — طبقة التنفيذ الموحّدة.

نفس المنطق. نفس القرار. نفس المخاطرة.
الفرق الوحيد: HOW the order is executed.

BACKTEST  → محاكاة داخلية (ملء فوري بالسعر الحالي)
PAPER     → تسجيل بدون أموال حقيقية + مراقبة السعر الحي
LIVE      → أمر حقيقي عبر الوسيط

الاستراتيجية لا تعرف في أي وضع تعمل.
القرار لا يعرف في أي وضع يُنفَّذ.
فقط ExecutionAdapter يعرف.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class ExecutionMode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass
class Order:
    """أمر — العقد الموحد."""
    id: str = ""
    symbol: str = ""
    side: str = ""           # BUY | SELL
    size: float = 0.0
    price: float = 0.0       # 0 = market order
    order_type: str = "market"  # market | limit | stop
    stop_loss: float | None = None
    take_profit: float | None = None
    status: str = OrderStatus.PENDING
    fill_price: float = 0.0
    fill_time: float = 0.0
    fill_size: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    reject_reason: str = ""
    mode: str = ""
    created_at: float = 0.0
    strategy: str = ""
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "symbol": self.symbol, "side": self.side,
            "size": self.size, "price": self.price,
            "order_type": self.order_type,
            "stop_loss": self.stop_loss, "take_profit": self.take_profit,
            "status": self.status, "fill_price": self.fill_price,
            "fill_time": self.fill_time, "fill_size": self.fill_size,
            "commission": self.commission, "slippage": self.slippage,
            "reject_reason": self.reject_reason, "mode": self.mode,
            "strategy": self.strategy, "run_id": self.run_id,
        }


class ExecutionAdapter(Protocol):
    """الواجهة — كل adapter ينفذها."""

    def submit(self, order: Order) -> Order: ...
    def cancel(self, order_id: str) -> bool: ...
    def get_positions(self) -> list[dict]: ...
    def get_balance(self) -> float: ...


class BacktestExecutor:
    """تنفيذ محاكاة — ملء فوري بالسعر + سلبج + عمولة."""

    def __init__(self, slippage_pips: float = 0.0,
                 commission_per_unit: float = 0.0,
                 initial_balance: float = 100_000.0):
        self._slippage = slippage_pips * 0.0001
        self._commission = commission_per_unit
        self._balance = initial_balance
        self._initial = initial_balance
        self._positions: list[dict] = []
        self._orders: list[Order] = []
        self._order_counter = 0

    @property
    def mode(self) -> str:
        return ExecutionMode.BACKTEST

    def submit(self, order: Order) -> Order:
        self._order_counter += 1
        order.id = order.id or f"BT-{self._order_counter:06d}"
        order.created_at = time.time()
        order.mode = ExecutionMode.BACKTEST

        # محاكاة الملء
        fill_price = order.price
        if order.side == "BUY":
            fill_price += self._slippage
        else:
            fill_price -= self._slippage

        order.fill_price = fill_price
        order.fill_time = time.time()
        order.fill_size = order.size
        order.commission = self._commission * order.size
        order.slippage = abs(fill_price - order.price)
        order.status = OrderStatus.FILLED

        self._balance -= order.commission
        self._positions.append({
            "id": order.id, "symbol": order.symbol, "side": order.side,
            "size": order.size, "entry_price": fill_price,
            "entry_time": order.fill_time,
            "stop_loss": order.stop_loss, "take_profit": order.take_profit,
        })
        self._orders.append(order)
        return order

    def close_position(self, position_id: str, close_price: float) -> dict | None:
        """إغلاق مركز — يرجع الربح/الخسارة."""
        pos = None
        for p in self._positions:
            if p["id"] == position_id:
                pos = p
                break
        if pos is None:
            return None

        if pos["side"] == "BUY":
            pnl = (close_price - pos["entry_price"]) * pos["size"]
        else:
            pnl = (pos["entry_price"] - close_price) * pos["size"]

        self._balance += pnl
        self._positions.remove(pos)
        return {"position_id": position_id, "pnl": pnl, "balance": self._balance}

    def cancel(self, order_id: str) -> bool:
        return False  # الباك تست يملأ فورياً

    def get_positions(self) -> list[dict]:
        return list(self._positions)

    def get_balance(self) -> float:
        return self._balance

    def get_report(self) -> dict:
        return {
            "mode": "backtest",
            "initial_balance": self._initial,
            "current_balance": self._balance,
            "pnl": self._balance - self._initial,
            "return_pct": (self._balance - self._initial) / self._initial * 100,
            "total_orders": len(self._orders),
            "open_positions": len(self._positions),
        }


class PaperExecutor:
    """تنفيذ ورقي — يسجّل الأمر بدون أموال حقيقية.

    يراقب السعر الحي ويتحقق لو كان الأمر سيمتلئ.
    لا يُرسل شيئاً للوسيط.
    """

    def __init__(self, initial_balance: float = 100_000.0):
        self._balance = initial_balance
        self._initial = initial_balance
        self._positions: list[dict] = []
        self._orders: list[Order] = []
        self._order_counter = 0

    @property
    def mode(self) -> str:
        return ExecutionMode.PAPER

    def submit(self, order: Order) -> Order:
        self._order_counter += 1
        order.id = order.id or f"PP-{self._order_counter:06d}"
        order.created_at = time.time()
        order.mode = ExecutionMode.PAPER
        order.status = OrderStatus.SUBMITTED

        # Paper: نفترض ملء فوري للسعر الحالي (سيُصحَّح بالسعر الحي لاحقاً)
        order.fill_price = order.price
        order.fill_time = time.time()
        order.fill_size = order.size
        order.status = OrderStatus.FILLED

        self._positions.append({
            "id": order.id, "symbol": order.symbol, "side": order.side,
            "size": order.size, "entry_price": order.price,
            "entry_time": order.fill_time,
        })
        self._orders.append(order)
        return order

    def cancel(self, order_id: str) -> bool:
        for o in self._orders:
            if o.id == order_id and o.status == OrderStatus.SUBMITTED:
                o.status = OrderStatus.CANCELLED
                return True
        return False

    def get_positions(self) -> list[dict]:
        return list(self._positions)

    def get_balance(self) -> float:
        return self._balance

    def get_report(self) -> dict:
        return {
            "mode": "paper",
            "initial_balance": self._initial,
            "current_balance": self._balance,
            "total_orders": len(self._orders),
            "open_positions": len(self._positions),
        }


class LiveExecutor:
    """تنفيذ حقيقي — يرسل أمر للوسيط.

    هذا ما يُستخدم في الإنتاج.
    يتطلب اتصال حقيقي بالوسيط.
    لا يُنفَّذ إلا بعد إغلاق بوابة Paper.
    """

    def __init__(self):
        self._connected = False
        self._orders: list[Order] = []

    @property
    def mode(self) -> str:
        return ExecutionMode.LIVE

    def connect(self) -> bool:
        """اتصال بالوسيط — يُستدعى قبل أي أمر حقيقي."""
        # FIXME: ربط حقيقي بـ cTrader/MT5
        self._connected = False  # مقصود — لا يُفعَّل إلا يدوياً
        return self._connected

    def submit(self, order: Order) -> Order:
        if not self._connected:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "LIVE_NOT_CONNECTED — بوابة الإنتاج غير مفتوحة"
            return order

        order.id = order.id or f"LV-{uuid.uuid4().hex[:6].upper()}"
        order.created_at = time.time()
        order.mode = ExecutionMode.LIVE
        order.status = OrderStatus.REJECTED
        order.reject_reason = "LIVE_NOT_IMPLEMENTED — التنفيذ الحي يحتاج ربط وسيط فعلي"
        return order

    def cancel(self, order_id: str) -> bool:
        return False

    def get_positions(self) -> list[dict]:
        return []

    def get_balance(self) -> float:
        return 0.0


def create_executor(mode: str, **kwargs) -> BacktestExecutor | PaperExecutor | LiveExecutor:
    """إنشاء منفّذ بالوضع."""
    if mode == ExecutionMode.BACKTEST:
        return BacktestExecutor(**kwargs)
    elif mode == ExecutionMode.PAPER:
        return PaperExecutor(**kwargs)
    elif mode == ExecutionMode.LIVE:
        return LiveExecutor()
    else:
        raise ValueError(f"وضع تنفيذ غير معروف: {mode}")
