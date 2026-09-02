# -*- coding: utf-8 -*-
"""نماذج بيانات محرك البكتست."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class Tick:
    """تيك واحد من السوق."""
    symbol: str
    timestamp: float  # epoch seconds
    bid: float
    ask: float
    volume: float = 0.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) * 0.5

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_pips(self) -> float:
        """السبريد بالبيبات (لأزواج الفوركس القياسية)."""
        # EURUSD-type: pip = 0.0001; JPY/gold: pip = 0.01
        if self.symbol and ("JPY" in self.symbol.upper()):
            return self.spread * 100
        return self.spread * 10000


@dataclass(slots=True)
class Candle:
    """شمعة OHLCV مبنية من التيكات."""
    symbol: str
    timestamp: float  # فتح الشمعة (epoch seconds)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    tick_count: int = 0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open


@dataclass(slots=True)
class Trade:
    """صفقة واحدة (فتح + إغلاق)."""
    id: int
    symbol: str
    side: Side
    entry_price: float
    entry_time: float
    size: float = 1.0
    stop_loss: float | None = None
    take_profit: float | None = None
    exit_price: float | None = None
    exit_time: float | None = None
    status: TradeStatus = TradeStatus.OPEN
    pnl: float = 0.0
    commission: float = 0.0
    reason: str = ""

    @property
    def is_open(self) -> bool:
        return self.status == TradeStatus.OPEN

    @property
    def net_pnl(self) -> float:
        return self.pnl - self.commission

    @property
    def duration_s(self) -> float:
        if self.exit_time and self.entry_time:
            return self.exit_time - self.entry_time
        return 0.0

    def close(self, price: float, timestamp: float, reason: str = "") -> None:
        self.exit_price = price
        self.exit_time = timestamp
        self.status = TradeStatus.CLOSED
        self.reason = reason
        if self.side == Side.BUY:
            self.pnl = (price - self.entry_price) * self.size
        else:
            self.pnl = (self.entry_price - price) * self.size


@dataclass(slots=True)
class EquityPoint:
    """نقطة على منحنى حقوق الملكية."""
    timestamp: float
    equity: float
    drawdown: float = 0.0
    drawdown_pct: float = 0.0


@dataclass
class BacktestConfig:
    """إعدادات جولة البكتست."""
    symbol: str = "EURUSD"
    initial_capital: float = 100_000.0
    lot_size: float = 0.01
    commission_per_lot: float = 0.0
    slippage_pips: float = 0.0
    max_open_trades: int = 1
    strategy_name: str = "sample_ma_crossover"
    strategy_params: dict[str, Any] = field(default_factory=dict)
    # WebSocket
    ws_host: str = "127.0.0.1"
    ws_port: int = 8765
    # فترة البكتست
    start_time: float | None = None
    end_time: float | None = None
    # مصدر البيانات
    data_file: str | None = None  # مسار ملف بيانات تاريخية
    allow_synthetic: bool = False  # حرج ٣: اصطناعي صريح فقط — لا سقوط تلقائي


@dataclass
class BacktestResult:
    """نتائج جولة البكتست الكاملة."""
    run_id: str = ""
    config: BacktestConfig = field(default_factory=BacktestConfig)
    started_at: float = 0.0
    finished_at: float = 0.0
    # مقاييس
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    total_commission: float = 0.0
    net_pnl: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_trade_duration_s: float = 0.0
    final_equity: float = 0.0
    return_pct: float = 0.0
    # بيانات تفصيلية
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    candles: list[Candle] = field(default_factory=list)
    ticks_count: int = 0
    duration_s: float = 0.0
    status: str = "idle"  # idle, running, completed, failed
    error: str = ""
    # حرج ٣ — مصدر البيانات (إلزامي)
    data_source: str = ""  # "file:path" | "ws:host:port" | "synthetic:explicit" | ""

    def to_dict(self) -> dict[str, Any]:
        """تحويل لـ JSON."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 3),
            "ticks_count": self.ticks_count,
            "data_source": self.data_source,  # حرج ٣
            "config": {
                "symbol": self.config.symbol,
                "initial_capital": self.config.initial_capital,
                "lot_size": self.config.lot_size,
                "strategy_name": self.config.strategy_name,
                "strategy_params": self.config.strategy_params,
            },
            "metrics": {
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "win_rate": round(self.win_rate, 4),
                "total_pnl": round(self.total_pnl, 2),
                "net_pnl": round(self.net_pnl, 2),
                "total_commission": round(self.total_commission, 2),
                "avg_win": round(self.avg_win, 2),
                "avg_loss": round(self.avg_loss, 2),
                "profit_factor": round(self.profit_factor, 4),
                "max_drawdown": round(self.max_drawdown, 2),
                "max_drawdown_pct": round(self.max_drawdown_pct, 4),
                "sharpe_ratio": round(self.sharpe_ratio, 4),
                "sortino_ratio": round(self.sortino_ratio, 4),
                "calmar_ratio": round(self.calmar_ratio, 4),
                "max_consecutive_wins": self.max_consecutive_wins,
                "max_consecutive_losses": self.max_consecutive_losses,
                "avg_trade_duration_s": round(self.avg_trade_duration_s, 1),
                "final_equity": round(self.final_equity, 2),
                "return_pct": round(self.return_pct, 4),
            },
            "trades": [
                {
                    "id": t.id, "symbol": t.symbol, "side": t.side.value,
                    "entry_price": t.entry_price, "entry_time": t.entry_time,
                    "exit_price": t.exit_price, "exit_time": t.exit_time,
                    "size": t.size, "pnl": round(t.pnl, 2),
                    "net_pnl": round(t.net_pnl, 2),
                    "commission": round(t.commission, 4),
                    "reason": t.reason, "status": t.status.value,
                    "duration_s": round(t.duration_s, 1),
                }
                for t in self.trades
            ],
            "equity_curve": [
                {"t": round(ep.timestamp, 3), "e": round(ep.equity, 2),
                 "dd": round(ep.drawdown, 2), "dd_pct": round(ep.drawdown_pct, 6)}
                for ep in self.equity_curve[-5000:]  # حدّ 5000 نقطة للعرض
            ],
        }
