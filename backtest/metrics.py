# -*- coding: utf-8 -*-
"""حاسبة المقاييس — تحسب كل مقاييس الأداء من قائمة الصفقات.

المقاييس:
  - إجمالي الربح/الخسارة (PnL)
  - نسبة الفوز (Win Rate)
  - عامل الربح (Profit Factor)
  - أقصى تراجع (Max Drawdown) — قيمته ونسبته
  - نسبة شارب (Sharpe Ratio)
  - نسبة سورتينو (Sortino Ratio)
  - نسبة كالمار (Calmar Ratio)
  - أكثر عدد صفقات رابحة/خاسرة متتالية
  - متوسط مدة الصفقة
"""
from __future__ import annotations

import math
from typing import Sequence

from backtest.models import EquityPoint, Trade


def compute_metrics(
    trades: Sequence[Trade],
    equity_curve: Sequence[EquityPoint],
    initial_capital: float,
    risk_free_rate: float = 0.0,
) -> dict:
    """حساب كل المقاييس من الصفقات ومنحنى حقوق الملكية."""
    result: dict = {}
    if not trades:
        return _empty_metrics(initial_capital)

    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        return _empty_metrics(initial_capital)

    # ═══ الربح/الخسارة ═══
    pnls = [t.net_pnl for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    result["total_trades"] = len(closed)
    result["winning_trades"] = len(wins)
    result["losing_trades"] = len(losses)
    result["total_pnl"] = sum(pnls)
    result["total_commission"] = sum(t.commission for t in closed)
    result["net_pnl"] = result["total_pnl"]
    result["win_rate"] = len(wins) / len(closed) if closed else 0.0
    result["avg_win"] = sum(wins) / len(wins) if wins else 0.0
    result["avg_loss"] = sum(losses) / len(losses) if losses else 0.0

    # ═══ عامل الربح ═══
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    result["profit_factor"] = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    # ═══ سلسلة الفوز/الخسارة المتتالية ═══
    max_consec_wins = 0
    max_consec_losses = 0
    curr_wins = 0
    curr_losses = 0
    for p in pnls:
        if p > 0:
            curr_wins += 1
            curr_losses = 0
            max_consec_wins = max(max_consec_wins, curr_wins)
        elif p < 0:
            curr_losses += 1
            curr_wins = 0
            max_consec_losses = max(max_consec_losses, curr_losses)
        else:
            curr_wins = 0
            curr_losses = 0
    result["max_consecutive_wins"] = max_consec_wins
    result["max_consecutive_losses"] = max_consec_losses

    # ═══ متوسط مدة الصفقة ═══
    durations = [t.duration_s for t in closed if t.duration_s > 0]
    result["avg_trade_duration_s"] = (sum(durations) / len(durations)) if durations else 0.0

    # ═══ منحنى حقوق الملكية والتراجع ═══
    if equity_curve:
        peak = equity_curve[0].equity
        max_dd = 0.0
        max_dd_pct = 0.0
        for ep in equity_curve:
            if ep.equity > peak:
                peak = ep.equity
            dd = peak - ep.equity
            dd_pct = dd / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
        result["max_drawdown"] = max_dd
        result["max_drawdown_pct"] = max_dd_pct
        result["final_equity"] = equity_curve[-1].equity
    else:
        result["max_drawdown"] = 0.0
        result["max_drawdown_pct"] = 0.0
        result["final_equity"] = initial_capital

    result["return_pct"] = ((result["final_equity"] - initial_capital) / initial_capital) if initial_capital > 0 else 0.0

    # ═══ Sharpe / Sortino / Calmar ═══
    if len(pnls) >= 2:
        mean_pnl = sum(pnls) / len(pnls)
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
        # Sharpe — مبسّط لكل صفقة (يعتبر كل صفقة فترة)
        result["sharpe_ratio"] = (mean_pnl / std) if std > 0 else 0.0
        # Sortino — يحسب الانحراف السلبي فقط
        downside = [p for p in pnls if p < 0]
        if downside:
            down_variance = sum(p ** 2 for p in downside) / len(downside)
            down_std = math.sqrt(down_variance) if down_variance > 0 else 0.0
            result["sortino_ratio"] = (mean_pnl / down_std) if down_std > 0 else 0.0
        else:
            result["sortino_ratio"] = 0.0
    else:
        result["sharpe_ratio"] = 0.0
        result["sortino_ratio"] = 0.0

    # Calmar = Return / Max Drawdown
    if result["max_drawdown"] > 0:
        result["calmar_ratio"] = result["net_pnl"] / result["max_drawdown"]
    else:
        result["calmar_ratio"] = 0.0

    return result


def _empty_metrics(initial_capital: float) -> dict:
    """مقاييس فارغة (لا صفقات)."""
    return {
        "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
        "total_pnl": 0.0, "total_commission": 0.0, "net_pnl": 0.0,
        "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        "profit_factor": 0.0, "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
        "sharpe_ratio": 0.0, "sortino_ratio": 0.0, "calmar_ratio": 0.0,
        "max_consecutive_wins": 0, "max_consecutive_losses": 0,
        "avg_trade_duration_s": 0.0, "final_equity": initial_capital,
        "return_pct": 0.0,
    }


def build_equity_curve(
    trades: Sequence[Trade],
    initial_capital: float,
    equity_sample_interval: int = 100,
) -> list[EquityPoint]:
    """بناء منحنى حقوق الملكية من الصفقات المغلقة.

    يأخذ عيّنة كل equity_sample_interval صفقة لتقليل الحجم.
    """
    closed = sorted(
        [t for t in trades if t.exit_price is not None],
        key=lambda t: t.exit_time or 0
    )
    if not closed:
        return [EquityPoint(timestamp=0, equity=initial_capital)]

    curve: list[EquityPoint] = [EquityPoint(timestamp=closed[0].entry_time, equity=initial_capital)]
    equity = initial_capital
    peak = initial_capital

    for i, trade in enumerate(closed):
        equity += trade.net_pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        dd_pct = dd / peak if peak > 0 else 0.0
        if i % equity_sample_interval == 0 or i == len(closed) - 1:
            curve.append(EquityPoint(
                timestamp=trade.exit_time or 0,
                equity=equity,
                drawdown=dd,
                drawdown_pct=dd_pct,
            ))
    return curve
