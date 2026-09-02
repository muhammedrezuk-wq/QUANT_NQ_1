# -*- coding: utf-8 -*-
"""محرّك التجارب — Experiment Store.

كل تشغيل (باك تست / Paper / Live) ينتج تجربة واحدة.
التجربة تحمل كل شيء:
  - معرّف فريد (run_id)
  - نسخة الكود (code_version)
  - الإعدادات كاملة
  - مصدر البيانات + جودتها
  - الاستراتيجية + معاملاتها
  - النتيجة الكاملة
  - حالة المحرك

يمكن مقارنة تجربتين بالضبط.
يمكن إعادة تجربة بالضبط.
يمكن فتح تجربة قديمة.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class ExperimentConfig:
    """إعدادات التجربة — كل ما يلزم لإعادة إنتاجها."""
    symbol: str = ""
    timeframe: str = "M1"
    date_from: str = ""
    date_to: str = ""
    initial_capital: float = 100_000.0
    lot_size: float = 0.01
    commission_per_lot: float = 0.0
    slippage_pips: float = 0.0
    max_open_trades: int = 1
    mode: str = "backtest"  # backtest | paper | live
    # مصدر البيانات
    data_source: str = ""
    data_file: str = ""
    data_provenance: dict[str, Any] = field(default_factory=dict)
    data_quality: str = ""
    data_points: int = 0
    # الاستراتيجية
    strategy: str = ""
    strategy_params: dict[str, Any] = field(default_factory=dict)
    # المؤشرات (إن استخدم مختبر)
    indicators: list[str] = field(default_factory=list)
    indicator_params: dict[str, Any] = field(default_factory=dict)
    # نسخة الكود
    code_version: str = ""
    core_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentResult:
    """نتيجة التجربة."""
    # مقاييس
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    net_pnl: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    final_equity: float = 0.0
    return_pct: float = 0.0
    # consensus (من المختبر)
    consensus: str = ""
    probability_buy: float = 0.0
    probability_sell: float = 0.0
    # تفاصيل
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    indicator_signals: list[dict[str, Any]] = field(default_factory=list)
    clock_report: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Experiment:
    """تجربة كاملة — الوحدة الأساسية للتشغيل والمقارنة."""
    run_id: str = ""
    created_at: float = 0.0
    finished_at: float = 0.0
    status: str = "created"  # created | running | completed | failed | stopped
    config: ExperimentConfig = field(default_factory=ExperimentConfig)
    result: ExperimentResult = field(default_factory=ExperimentResult)
    error: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def duration_s(self) -> float:
        if self.finished_at and self.created_at:
            return self.finished_at - self.created_at
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 3),
            "status": self.status,
            "config": self.config.to_dict(),
            "result": self.result.to_dict(),
            "error": self.error,
            "tags": self.tags,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Experiment:
        exp = cls()
        exp.run_id = data.get("run_id", "")
        exp.created_at = data.get("created_at", 0)
        exp.finished_at = data.get("finished_at", 0)
        exp.status = data.get("status", "")
        exp.error = data.get("error", "")
        exp.tags = data.get("tags", [])
        exp.notes = data.get("notes", "")
        # config
        cfg = data.get("config", {})
        exp.config = ExperimentConfig(
            symbol=cfg.get("symbol", ""),
            timeframe=cfg.get("timeframe", "M1"),
            date_from=cfg.get("date_from", ""),
            date_to=cfg.get("date_to", ""),
            initial_capital=cfg.get("initial_capital", 100_000),
            lot_size=cfg.get("lot_size", 0.01),
            mode=cfg.get("mode", "backtest"),
            data_source=cfg.get("data_source", ""),
            strategy=cfg.get("strategy", ""),
            strategy_params=cfg.get("strategy_params", {}),
            indicators=cfg.get("indicators", []),
            code_version=cfg.get("code_version", ""),
        )
        # result
        res = data.get("result", {})
        exp.result = ExperimentResult(
            total_trades=res.get("total_trades", 0),
            winning_trades=res.get("winning_trades", 0),
            losing_trades=res.get("losing_trades", 0),
            win_rate=res.get("win_rate", 0),
            net_pnl=res.get("net_pnl", 0),
            max_drawdown=res.get("max_drawdown", 0),
            sharpe_ratio=res.get("sharpe_ratio", 0),
            final_equity=res.get("final_equity", 0),
            consensus=res.get("consensus", ""),
            probability_buy=res.get("probability_buy", 0),
            probability_sell=res.get("probability_sell", 0),
        )
        return exp


class ExperimentStore:
    """مخزن التجارب — حفظ + قراءة + مقارنة."""

    def __init__(self, store_dir: str | Path | None = None):
        if store_dir is None:
            store_dir = Path(__file__).parent.parent / "var" / "experiments"
        self._dir = Path(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Experiment] = {}

    def create(self, config: ExperimentConfig) -> Experiment:
        """إنشاء تجربة جديدة."""
        exp = Experiment(
            run_id=f"RUN-{uuid.uuid4().hex[:8].upper()}",
            created_at=time.time(),
            status="created",
            config=config,
        )
        self._cache[exp.run_id] = exp
        self._save(exp)
        return exp

    def get(self, run_id: str) -> Experiment | None:
        """قراءة تجربة."""
        if run_id in self._cache:
            return self._cache[run_id]
        path = self._dir / f"{run_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        exp = Experiment.from_dict(data)
        self._cache[run_id] = exp
        return exp

    def update(self, exp: Experiment) -> None:
        """تحديث تجربة."""
        self._cache[exp.run_id] = exp
        self._save(exp)

    def complete(self, exp: Experiment, result: ExperimentResult,
                 clock_report: dict | None = None) -> None:
        """إكمال تجربة بنتيجة."""
        exp.result = result
        if clock_report:
            exp.result.clock_report = clock_report
        exp.finished_at = time.time()
        exp.status = "completed"
        self.update(exp)

    def fail(self, exp: Experiment, error: str) -> None:
        """فشل تجربة."""
        exp.error = error
        exp.finished_at = time.time()
        exp.status = "failed"
        self.update(exp)

    def list_all(self, limit: int = 50) -> list[dict[str, Any]]:
        """قائمة التجارب — الأحدث أولاً."""
        files = sorted(self._dir.glob("RUN-*.json"), reverse=True)
        results = []
        for f in files[:limit]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append({
                    "run_id": data.get("run_id"),
                    "status": data.get("status"),
                    "symbol": data.get("config", {}).get("symbol"),
                    "strategy": data.get("config", {}).get("strategy"),
                    "mode": data.get("config", {}).get("mode"),
                    "net_pnl": data.get("result", {}).get("net_pnl", 0),
                    "win_rate": data.get("result", {}).get("win_rate", 0),
                    "total_trades": data.get("result", {}).get("total_trades", 0),
                    "created_at": data.get("created_at"),
                    "duration_s": data.get("duration_s", 0),
                })
            except Exception:
                continue
        return results

    def compare(self, run_ids: list[str]) -> dict[str, Any]:
        """مقارنة تجربتين أو أكثر."""
        experiments = []
        for rid in run_ids:
            exp = self.get(rid)
            if exp:
                experiments.append(exp)
        if len(experiments) < 2:
            return {"error": "لازم تجربتين أو أكثر للمقارنة"}

        comparison: dict[str, Any] = {
            "experiments": len(experiments),
            "metrics": {},
            "winner": {},
        }

        # جمع المقاييس
        metric_names = [
            "net_pnl", "win_rate", "total_trades", "max_drawdown",
            "sharpe_ratio", "profit_factor", "return_pct",
        ]
        for metric in metric_names:
            values = []
            for exp in experiments:
                val = getattr(exp.result, metric, 0)
                values.append({"run_id": exp.run_id, "value": val})
            comparison["metrics"][metric] = values

        # تحديد الفائز (أعلى PnL)
        best = max(experiments, key=lambda e: e.result.net_pnl)
        comparison["winner"] = {
            "run_id": best.run_id,
            "net_pnl": best.result.net_pnl,
            "win_rate": best.result.win_rate,
            "strategy": best.config.strategy,
        }

        return comparison

    def delete(self, run_id: str) -> bool:
        """حذف تجربة."""
        path = self._dir / f"{run_id}.json"
        if path.exists():
            path.unlink()
            self._cache.pop(run_id, None)
            return True
        return False

    def _save(self, exp: Experiment) -> None:
        """حفظ لملف."""
        path = self._dir / f"{exp.run_id}.json"
        path.write_text(
            json.dumps(exp.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
