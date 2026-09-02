# -*- coding: utf-8 -*-
"""BacktestRunner v2 — يشغّل الذرات الفعلية على بيانات تاريخية حقيقية.

المسار الكامل:
  DataStream → HistoricalClock → SyncEventBus → ذرّات حقيقية → نتيجة

ينشر الأحداث الصحيحة لكل ذرّة:
  - market.tick.validated أولًا (المسار السريع)
  - market_data.candle_closed من ذرّة ١٠٣ فقط (عدد محدود من الفريمات)
  - SYS_SECOND بـ official_time = زمن النقطة (لا ساعة جدارية)
  - platform.account.state قبل أول تيك
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import random
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from backtest.data_contract import DataPoint, DataStream, DataProvenance
from backtest.historical_clock import HistoricalClock
from backtest.sync_event_bus import SyncEventBus, create_logger
from backtest.experiment_store import Experiment, ExperimentConfig, ExperimentResult, ExperimentStore
from backtest.historical_data import to_tick_payload, to_candle_payload

log = logging.getLogger("backtest.runner")
ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════════════════
# تحميل الذرّات
# ═══════════════════════════════════════════════════════════════════════════════

def _load_atom_class(atom_dir: Path, module_name: str) -> Any:
    atom_file = atom_dir / "atom.py"
    if not atom_file.exists():
        return None
    spec = importlib.util.spec_from_file_location(module_name, str(atom_file))
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        log.debug(f"فشل تحميل {atom_dir.name}: {exc}")
        return None
    # ابحث عن class Atom(AtomBase) داخل الموديول
    for name in ("Atom", "AtomBase"):
        cls = getattr(mod, name, None)
        if cls and hasattr(cls, "initialize") and hasattr(cls, "start"):
            return cls
    # إذا لم يوجد Atom، ابحث عن أي class يرث AtomBase
    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        if isinstance(obj, type) and attr_name not in ("AtomBase", "AtomContext"):
            if hasattr(obj, "initialize") and hasattr(obj, "start"):
                return obj
    return None


def _load_manifest(atom_dir: Path) -> dict[str, Any]:
    import yaml
    manifest_file = atom_dir / "manifest.yaml"
    if not manifest_file.exists():
        return {}
    try:
        return yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def discover_atoms(atoms_dir: Path, atom_range: tuple[int, int] | None = None) -> list[dict]:
    results = []
    for section_dir in sorted(atoms_dir.iterdir()):
        if not section_dir.is_dir():
            continue
        for atom_dir in sorted(section_dir.iterdir()):
            if not atom_dir.is_dir() or not (atom_dir / "atom.py").exists():
                continue
            try:
                atom_id = int(atom_dir.name.split("_", 1)[0])
            except (ValueError, IndexError):
                continue
            if atom_range and not (atom_range[0] <= atom_id <= atom_range[1]):
                continue
            manifest = _load_manifest(atom_dir)
            results.append({
                "dir": atom_dir, "id": atom_id,
                "name": atom_dir.name.split("_", 1)[-1] if "_" in atom_dir.name else "",
                "manifest": manifest,
                "startup_mode": manifest.get("startup_mode", "auto"),
            })
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# BacktestRunner v2
# ═══════════════════════════════════════════════════════════════════════════════

class BacktestRunner:
    """المحرك — يشغّل ذرّات حقيقية مع المسار الكامل."""

    def __init__(self, atoms_dir: Path | None = None):
        self._atoms_dir = atoms_dir or (ROOT / "atoms")
        self._bus = SyncEventBus()
        self._loaded_atoms: dict[int, Any] = {}
        self._atom_meta: dict[int, dict] = {}
        self._clock: HistoricalClock | None = None
        self._stream: DataStream | None = None
        self._stage_outputs: dict[str, list[dict]] = {}
        self._decisions: list[dict] = []
        self._run_id: str = ""
        self._error: str = ""
        self._started_at: float = 0
        self._finished_at: float = 0
        # Candle builder
        self._candle_buffer: dict[str, dict] = {}
        self._current_candle_ts: float = 0
        self._candle_interval: float = 60.0  # M1 default
        self._tick_count: int = 0
        self._candle_count: int = 0
        self._news_queue: list[dict[str, Any]] = []

    @property
    def bus(self) -> SyncEventBus:
        return self._bus

    @property
    def loaded_atom_ids(self) -> list[int]:
        return sorted(self._loaded_atoms.keys())

    # ═══ تحميل الذرّات ═══

    def load_atoms(self, atom_ids: list[int] | None = None,
                   atom_range: tuple[int, int] | None = None,
                   config_overrides: dict[int, dict] | None = None) -> int:
        """تحميل ذرّات محددة. config_overrides طبقة ذاكرة فقط — لا تُكتب للمانيفست."""
        discovered = discover_atoms(self._atoms_dir, atom_range)
        count = 0
        for info in discovered:
            if atom_ids and info["id"] not in atom_ids:
                continue
            cls = _load_atom_class(info["dir"], f"bt_v2_atom_{info['id']}")
            if cls is None:
                continue
            try:
                atom = cls()
                config = dict(info["manifest"].get("config") or {})
                extra = (config_overrides or {}).get(info["id"])
                if extra:
                    config.update(extra)
                ctx = _make_context(info["id"], config, self._bus)
                loop = asyncio.new_event_loop()
                loop.run_until_complete(atom.initialize(ctx))
                loop.run_until_complete(atom.start())
                loop.close()
                self._loaded_atoms[info["id"]] = atom
                self._atom_meta[info["id"]] = info
                count += 1
            except Exception as exc:
                log.debug(f"فشل تهيئة ذرّة {info['id']}: {exc}")
        return count

    def load_full_pipeline(self) -> int:
        """تحميل المسار الكامل من كل قسم."""
        key_atoms = [
            # Analysis (151-166) — subscribes to candle_closed
            151, 152, 153, 154, 155, 156, 157, 158,
            # Structure (200-210) — swing → bos → choch → publish
            200, 201, 202, 203, 204, 205, 210,
            # Liquidity (250-264) — pool → buyside/sellside → sweep → publish
            250, 251, 252, 253, 254, 255, 260,
            # Statistics (300-306) — ticks → stats → cycle
            300, 301, 302, 303, 304, 305, 306,
            # Probability (351-359) — models → merge → confidence
            351, 352, 353, 355, 359,
            # Strategy (400-406) — ticks → strategy → cycle
            400, 401, 402, 404, 405, 406,
            # Decision (451-455) — aggregated + signal eval + score + filter + buy/sell
            451, 452, 453, 454, 455,
            # Risk (500+) — account + exposure + profit + session → unified
            500, 506, 507, 508,
            # Execution (550-626) — execution manager + paper bridge
            550, 551, 626,
        ]
        return self.load_atoms(atom_ids=key_atoms)

    # ═══ البيانات ═══

    def set_data(self, stream: DataStream) -> None:
        """تعيين بيانات تاريخية."""
        self._stream = stream
        self._clock = HistoricalClock(stream, strict=True)
        # النظام تيكات. الشموع يبنيها ١٠٣ من التيك — لا ننشر شمعة الملف كأنها تيك.
        self._is_real_candle_data = False
        tf_map = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "D1": 86400, "tick": 1}
        self._candle_interval = tf_map.get(stream.timeframe, 1)

    def set_news(self, rows: list[dict[str, Any]]) -> None:
        """صفوف جسر حقيقية تُنشر وقت التيك — بلا عنوان مخترَع."""
        def _when(row: dict[str, Any]) -> float:
            for key in ("published_at", "scheduled_at", "timestamp"):
                try:
                    if row.get(key) is not None:
                        return float(row[key])
                except (TypeError, ValueError):
                    continue
            return 0.0
        self._news_queue = sorted((r for r in rows if isinstance(r, dict)), key=_when)

    def set_data_from_points(self, points: list[DataPoint],
                              symbol: str = "EURUSD",
                              timeframe: str = "M1",
                              source: str = "backtest") -> None:
        """تعيين بيانات من نقاط."""
        provenance = DataProvenance(original_source=source, ingest_time=time.time())
        self._stream = DataStream(
            symbol=symbol, timeframe=timeframe, source=source,
            points=points, provenance=provenance,
        )
        self._clock = HistoricalClock(self._stream, strict=True)
        self._is_real_candle_data = False
        tf_map = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "tick": 1, "D1": 86400}
        self._candle_interval = tf_map.get(timeframe, 1)

    # ═══ التشغيل ═══

    def run(self) -> dict[str, Any]:
        """تشغيل الباك تست — يمرر البيانات عبر كل المراحل."""
        self._run_id = f"RUN-{uuid.uuid4().hex[:8].upper()}"
        self._started_at = time.time()
        self._stage_outputs = {
            "analysis": [], "structure": [], "liquidity": [],
            "statistics": [], "probability": [], "strategy": [],
            "decision": [], "risk": [], "execution": [],
        }
        self._decisions = []
        self._error = ""
        self._tick_count = 0
        self._candle_count = 0
        self._candle_buffer = {}

        if self._clock is None:
            self._error = "لا توجد بيانات"
            return self._build_result()
        if not self._loaded_atoms:
            self._error = "لا توجد ذرّات محمّلة"
            return self._build_result()

        # إعداد مراقبة الأحداث
        self._setup_monitors()

        def _identity(ts: float, symbol: str = "") -> None:
            self._bus.publish("platform.account.state", {
                "account_id": "backtest_001", "broker": "backtest",
                "balance": 10000.0, "equity": 10000.0,
                "free_margin": 10000.0, "margin_used": 0.0,
                "unrealized_pnl": 0.0, "realized_pnl": 0.0,
                "leverage": 100, "currency": "USD",
                "timestamp": ts,
            })
            self._bus.publish("platform.positions.state", {
                "account_id": "backtest_001", "broker": "backtest",
                "positions": [], "count": 0,
                "timestamp": ts,
            })
            if symbol:
                self._bus.publish("market.symbol_specs", {
                    "symbol": symbol,
                    "tick_size": 0.00001, "lot_size": 0.01,
                    "min_lot": 0.01, "max_lot": 100.0,
                    "contract_size": 100000,
                    "timestamp": ts,
                })

        first_ts = self._stream.first_ts if self._stream else 0.0
        first_sym = self._stream.symbol if self._stream else ""
        _identity(first_ts, first_sym)

        # تيكات أولًا. الشموع من ١٠٣ — لا ننشر شمعة الملف كأنها تيك.
        for point in self._clock:
            self._tick_count += 1
            self._flush_news(point.timestamp)
            self._bus.publish("market.tick.validated", to_tick_payload(point))

            if self._tick_count % 10 == 0:
                self._bus.publish("SYS_SECOND", {
                    "timestamp": point.timestamp,
                    "official_time": point.timestamp,
                    "now": point.timestamp,
                })

            if self._tick_count % 50 == 0:
                _identity(point.timestamp, point.symbol)

        self._finished_at = time.time()
        clock_report = self._clock.report() if self._clock else {}
        return self._build_result(clock_report=clock_report)

    def _build_and_publish_candle(self, point: DataPoint) -> None:
        """بناء شمعة من التيكات ونشرها."""
        if self._candle_interval <= 0:
            return

        candle_ts = int(point.timestamp / self._candle_interval) * self._candle_interval
        sym = point.symbol

        if sym not in self._candle_buffer or self._candle_buffer[sym]["ts"] != candle_ts:
            # شمعة جديدة — ننشر القديمة إن وجدت
            if sym in self._candle_buffer:
                self._publish_candle(self._candle_buffer[sym])
            self._candle_buffer[sym] = {
                "ts": candle_ts, "symbol": sym,
                "open": point.close, "high": point.close,
                "low": point.close, "close": point.close,
                "volume": point.volume,
            }
        else:
            buf = self._candle_buffer[sym]
            buf["high"] = max(buf["high"], point.close)
            buf["low"] = min(buf["low"], point.close)
            buf["close"] = point.close
            buf["volume"] += point.volume

    def _publish_candle(self, candle: dict) -> None:
        """نشر شمعة مغلقة."""
        self._candle_count += 1
        payload = {
            "symbol": candle["symbol"],
            "account_id": "backtest_001",
            "broker": "backtest",
            "timestamp": candle["ts"],
            "source_timestamp": candle["ts"],
            "exchange_timestamp": candle["ts"],
            "open": candle["open"],
            "high": candle["high"],
            "low": candle["low"],
            "close": candle["close"],
            "volume": candle["volume"],
            "timeframe": self._stream.timeframe if self._stream else "M1",
            "source": self._stream.source if self._stream else "backtest",
            "sequence": self._candle_count,
            "period_start": candle["ts"],
        }
        self._bus.publish("market_data.candle_closed", payload)

    def _setup_monitors(self) -> None:
        """مراقبة أحداث كل مرحلة."""
        monitors = {
            "analysis": ["analysis.trend.state", "analysis.momentum.state",
                         "analysis.volatility.state", "analysis.volume.state",
                         "analysis.spread.state", "analysis.candle.state",
                         "analysis.gap.state", "analysis.session.state",
                         "analysis.regression.state", "analysis.corr.state",
                         "analysis.divergence.state", "analysis.vol_regime.state",
                         "analysis.profile.state", "analysis.pivot.state",
                         "analysis.micro.structure.state", "analysis.dynamics.state"],
            "structure": ["market.structure.updated", "structure.cycle.collected",
                          "structure.section.live", "structure.swing.state",
                          "structure.break.state", "structure.bos.state",
                          "structure.choch.state", "structure.ob.state",
                          "structure.htf.state", "structure.range.state",
                          "structure.equal.state", "structure.order_block.state"],
            "liquidity": ["market.liquidity.updated", "liquidity.cycle.collected",
                          "liquidity.pool.state", "liquidity.buyside.state",
                          "liquidity.sellside.state", "liquidity.sweep.state",
                          "liquidity.fvg.state", "liquidity.section.live"],
            "statistics": ["stats.cycle.collected", "stats.section.live",
                           "stats.mean.state", "stats.median.state",
                           "stats.mode.state", "stats.stddev.state",
                           "stats.variance.state", "stats.percentile.state",
                           "stats.zscore.state", "stats.skew.state",
                           "stats.kurtosis.state", "stats.autocorr.state",
                           "stats.entropy.state", "stats.hurst.state",
                           "stats.cusum.state", "stats.moving_stats.state"],
            "probability": ["probability.cycle.collected", "probability.confidence.state",
                            "probability.section.live", "probability.trend.state",
                            "probability.reversal.state", "probability.breakout.state",
                            "probability.momentum.state", "probability.merged.state",
                            "probability.hurst.state", "probability.range.state",
                            "probability.pullback.state"],
            "strategy": ["strategy.trend.state", "strategy.reversal.state",
                         "strategy.breakout.state", "strategy.cycle.collected",
                         "strategy.section.live", "strategy.entry_rules.state",
                         "strategy.exit_rules.state"],
            "decision": ["decision.aggregated.state", "decision.room.state",
                         "decision.signal_eval.state", "decision.score.state",
                         "decision.filter.state", "decision.buy.state",
                         "decision.sell.state", "decision.section.live"],
            "risk": ["risk.unified.state", "risk.account.state",
                     "risk.exposure.state", "risk.profit_limits.state",
                     "risk.session_limits.state", "risk.kill_switch.state",
                     "risk.halt.requested"],
            "execution": ["execution.order.submitted", "execution.order.filled",
                          "execution.order.built", "execution.order.rejected",
                          "execution.order.skipped", "execution.unified.state",
                          "execution.quality.state", "execution.desired.state",
                          "platform.trade_event.simulated", "sim.execution.state",
                          "platform.trade_event"],
        }
        for stage, events in monitors.items():
            for event_name in events:
                def monitor(payload, _stage=stage, _event=event_name):
                    self._stage_outputs[_stage].append({
                        "event": _event, "ts": time.time(),
                        "keys": list(payload.keys())[:10] if isinstance(payload, dict) else [],
                    })
                    if _stage == "decision" and _event == "decision.aggregated.state":
                        self._decisions.append(dict(payload) if isinstance(payload, dict) else {})
                self._bus.subscribe(event_name, monitor)

        # Bridge: decision → execution
        # When a decision is resolved, republish as trading.final_decision
        # لا نعيد نشر القرار كأمر حي. ٩٠١/٥٧٦/٦٠١ خارج المختبر والباك تست.
        # التنفيذ الورقي يملأ من decision.resolved عبر دفتر منفصل.

        def count_candle(payload):
            if isinstance(payload, dict):
                self._candle_count += 1
        self._bus.subscribe("market_data.candle_closed", count_candle)

    def _build_result(self, clock_report: dict | None = None) -> dict[str, Any]:
        """بناء نتيجة كاملة."""
        duration = self._finished_at - self._started_at if self._finished_at else 0
        return {
            "run_id": self._run_id,
            "status": "completed" if not self._error else "failed",
            "error": self._error,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "duration_s": round(duration, 3),
            "tick_count": self._tick_count,
            "candle_count": self._candle_count,
            "atoms_loaded": len(self._loaded_atoms),
            "atom_ids": self.loaded_atom_ids,
            "bus_report": self._bus.report(),
            "clock_report": clock_report or {},
            "stages": {
                stage: {"count": len(outputs)}
                for stage, outputs in self._stage_outputs.items()
            },
            "decisions": {"count": len(self._decisions), "samples": self._decisions[:5]},
            "provenance": self._stream.provenance.to_dict() if self._stream and self._stream.provenance else {},
            "data_info": self._stream.to_dict() if self._stream else {},
        }


def _make_context(atom_id: int, config: dict, bus: SyncEventBus) -> Any:
    from core.contracts.atom import AtomContext

    async def async_publish(event_name: str, payload: dict) -> None:
        """غلاف async لـ bus.publish — الذرّة تنتظر await."""
        bus.publish(event_name, payload)

    return AtomContext(
        atom_id=atom_id, config=config, logger=create_logger(),
        publish=async_publish, subscribe=bus.subscribe,
        subscribe_all=bus.subscribe_all,
    )
