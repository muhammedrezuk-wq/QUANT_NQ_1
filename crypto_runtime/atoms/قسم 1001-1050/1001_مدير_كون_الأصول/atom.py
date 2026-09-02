from __future__ import annotations

import asyncio
import json
import math
import time
import urllib.request
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.2.0"
PROVIDER = "MEXC"
TICKER_URL = "https://contract.mexc.com/api/v1/contract/ticker"
DETAIL_URL = "https://contract.mexc.com/api/v1/contract/detail"

EVENT_TICKER_ALL = "crypto.market.ticker.all"
EVENT_SNAPSHOT = "crypto.universe.snapshot.state"
EVENT_MEMBERSHIP = "crypto.universe.membership.state"
EVENT_REJECTED = "crypto.universe.rejected.state"
EVENT_OVERRIDE = "crypto.universe.override.command"
EVENT_SCAN = "crypto.universe.scan.requested"

REASON_LIQUIDITY_UNKNOWN = "LIQUIDITY_UNKNOWN"
REASON_LIQUIDITY_BELOW_OUTER = "LIQUIDITY_BELOW_OUTER"
REASON_SPREAD_UNKNOWN = "SPREAD_UNKNOWN"
REASON_SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
REASON_TICK_SIZE_UNKNOWN = "TICK_SIZE_UNKNOWN"
REASON_NON_CRYPTO = "NON_CRYPTO"
REASON_ASSET_CLASS_UNKNOWN = "ASSET_CLASS_UNKNOWN"
REASON_NOT_FUTURES_USDT = "NOT_FUTURES_USDT"
REASON_FUTURES_METADATA_UNKNOWN = "FUTURES_METADATA_UNKNOWN"
REASON_RANGE_UNKNOWN = "DAILY_RANGE_UNKNOWN"
REASON_RANGE_TOO_WIDE = "RANGE_TOO_WIDE"
REASON_MANUAL_DENY = "MANUAL_DENY"

# v1.2.0: kept ONLY as a defensive fallback for when a provider's detail
# row carries no conceptPlate/tag data at all (see _classify). "SPX" and
# "MSTR" were removed -- verified live against MEXC 2026-08-27: SPX_USDT
# is SPX6900, a real meme coin (conceptPlate=["mc-trade-zone-MEME"]), and
# HMSTR_USDT (Hamster Kombat, a real token) matched "MSTR" as a bare
# substring. Neither belongs in a NON-crypto marker list.
_NON_CRYPTO_MARKERS = (
    "GOLD", "SILVER", "XAU", "XAG", "OIL", "NDX", "US500",
    "US30", "NAS100", "DJI", "TSLA", "AAPL", "NVDA",
)
_NON_CRYPTO_CLASSES = {
    "INDEX", "EQUITY", "STOCK", "COMMODITY", "TOKENIZED_EQUITY",
    "TOKENIZED_COMMODITY", "FOREX",
}
# MEXC's own explicit categorization (contract/detail's `conceptPlate`
# list) is authoritative where the symbol-marker list above is a fragile
# guess. Verified live against all 1024 real USDT/USDT MEXC contracts
# 2026-08-27: "mc-trade-zone-tradfi" is the complete umbrella tag -- every
# item also tagged Stock/ETF/Commodities/Forex/stockindex/metals/
# metalsfutures/koreanstocks/preipo/OIL carries tradfi too, with zero
# exceptions either direction (no real crypto token wrongly caught, no
# wrapped tradfi instrument left uncaught among ~400 found). Before this
# fix, the marker list alone caught 12 of those ~400 non-crypto
# contracts -- roughly 390 tokenized stocks/ETFs/forex/commodity wrappers
# (AAPL, TSLA, SPY, QQQ, EUR, GBP, copper, silver...) were silently
# admitted as "NATIVE_CRYPTO".
_NON_CRYPTO_CONCEPT_TAGS = frozenset({"mc-trade-zone-tradfi"})


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _get_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "QUANT_NQ-PhaseA/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _rows(body: Any) -> list[dict[str, Any]]:
    data = body.get("data") if isinstance(body, dict) else body
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            data = data["data"]
        elif "symbol" in data:
            data = [data]
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _detail_value(detail: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in detail and detail[name] not in (None, ""):
            return detail[name]
    lowered = {str(k).lower(): v for k, v in detail.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def _concept_plates(detail: dict[str, Any]) -> list[str]:
    raw = _detail_value(detail, "conceptPlate", "concept_plate")
    return [str(p) for p in raw] if isinstance(raw, list) else []


def _classify(symbol: str, detail: dict[str, Any]) -> tuple[str, str]:
    explicit = str(_detail_value(
        detail, "assetClass", "asset_class", "instrumentType", "instrument_type",
        "category", "contractCategory", "contract_category",
    ) or "").strip().upper()
    if explicit in _NON_CRYPTO_CLASSES:
        return "NON_CRYPTO", explicit
    if any(plate in _NON_CRYPTO_CONCEPT_TAGS for plate in _concept_plates(detail)):
        return "NON_CRYPTO", "CONCEPT_PLATE_TRADFI"
    upper = symbol.upper()
    if any(marker in upper for marker in _NON_CRYPTO_MARKERS):
        return "NON_CRYPTO", "SYMBOL_MARKER"
    base = str(_detail_value(detail, "baseCoin", "base_coin", "baseAsset", "base_asset") or "").strip()
    quote = str(_detail_value(detail, "quoteCoin", "quote_coin", "quoteAsset", "quote_asset") or "").strip()
    if explicit in {"CRYPTO", "DIGITAL_ASSET", "PERPETUAL", "SWAP"}:
        return "NATIVE_CRYPTO", explicit
    if base and quote:
        return "NATIVE_CRYPTO", "BASE_QUOTE_METADATA"
    return "UNKNOWN", "ASSET_CLASS_UNKNOWN"


class Atom(AtomBase):
    """Phase A only: build the dynamic crypto asset universe.

    No senses, strategy, risk, order, or portfolio logic lives here. The atom
    owns only discovery, filtering, membership, rotation bookkeeping, and a
    single normalized ticker snapshot for the feed atom.
    """

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._poll_interval_s = 300.0
        self._core_min_usd = 50_000_000.0
        self._outer_min_usd = 10_000_000.0
        self._max_spread_ticks = 3.0
        self._max_range_pct = 20.0
        self._core_target = 12
        self._outer_target = 15
        self._entry_resilience_s = 0.25 * 86400.0
        self._exit_failure_s = 1 * 86400.0
        self._overrides_path = Path("var/universe_overrides.json")
        self._membership_path = Path("var/universe_membership.json")
        self._open_positions_path = Path("var/open_positions.json")
        self._membership_state: dict[str, dict[str, Any]] = {}
        self._version = 0
        self._last_snapshot: dict[str, Any] | None = None
        self._last_success_at: float | None = None
        self._last_error = ""
        self._scans = 0
        self._accepted = 0
        self._rejected = 0
        self._manual_overrides: dict[str, dict[str, Any]] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._poll_interval_s = float(cfg.get("poll_interval_s", 300.0))
        self._core_min_usd = float(cfg.get("core_min_liquidity_usd_24h", 50_000_000))
        self._outer_min_usd = float(cfg.get("outer_min_liquidity_usd_24h", 10_000_000))
        self._max_spread_ticks = float(cfg.get("max_spread_ticks", 3))
        self._max_range_pct = float(cfg.get("max_daily_range_pct", 20.0))
        self._core_target = int(cfg.get("core_target_count", 12))
        self._outer_target = int(cfg.get("outer_target_count", 15))
        self._entry_resilience_s = float(cfg.get("entry_resilience_days", 0.25)) * 86400.0
        self._exit_failure_s = float(cfg.get("exit_failure_days", 1)) * 86400.0
        self._overrides_path = Path(str(cfg.get("overrides_path", "var/universe_overrides.json")))
        self._membership_path = Path(str(cfg.get("membership_state_path", "var/universe_membership.json")))
        self._open_positions_path = Path(str(cfg.get("open_positions_path", "var/open_positions.json")))
        self._load_overrides()
        self._load_membership_state()
        context.subscribe(EVENT_OVERRIDE, self._on_override)
        context.subscribe(EVENT_SCAN, self._on_scan_requested)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        task, self._poll_task = self._poll_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def shutdown(self) -> None:
        await self.stop()

    def _load_overrides(self) -> None:
        try:
            data = json.loads(self._overrides_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._manual_overrides = {}
            return
        self._manual_overrides = data if isinstance(data, dict) else {}

    def _save_overrides(self) -> None:
        self._overrides_path.parent.mkdir(parents=True, exist_ok=True)
        self._overrides_path.write_text(
            json.dumps(self._manual_overrides, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _load_membership_state(self) -> None:
        try:
            data = json.loads(self._membership_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._membership_state = {}
            return
        self._membership_state = data if isinstance(data, dict) else {}

    def _save_membership_state(self) -> None:
        self._membership_path.parent.mkdir(parents=True, exist_ok=True)
        self._membership_path.write_text(
            json.dumps(self._membership_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _open_positions(self) -> set[str]:
        try:
            data = json.loads(self._open_positions_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        if isinstance(data, dict):
            data = data.get("symbols", [])
        if not isinstance(data, list):
            return set()
        return {str(item).strip().upper() for item in data if str(item).strip()}

    def _rotate(self, core: list[dict[str, Any]], outer: list[dict[str, Any]], rejected: list[dict[str, Any]], now: float) -> dict[str, Any]:
        """Apply the persistent entry/exit door without killing open positions."""
        qualified = {row["symbol"]: row for row in core + outer}
        previous = self._membership_state
        open_positions = self._open_positions()
        active_core: list[dict[str, Any]] = []
        active_outer: list[dict[str, Any]] = []
        enter_candidates: list[str] = []
        held_for_resilience: list[str] = []
        exit_candidates: list[str] = []
        protected: list[str] = []
        next_state: dict[str, dict[str, Any]] = {}

        for symbol, row in qualified.items():
            old = dict(previous.get(symbol) or {})
            qualified_since = float(old.get("qualified_since") or now)
            status = str(old.get("status") or "CANDIDATE")
            ring = str(row.get("ring") or "outer")
            age = now - qualified_since
            if status in {"CORE_ACTIVE", "OUTER_ACTIVE"}:
                active = True
            elif age >= self._entry_resilience_s:
                active = True
                status = "CORE_ACTIVE" if ring == "core" else "OUTER_ACTIVE"
            else:
                active = False
                status = "PROBATION"
                enter_candidates.append(symbol)
                held_for_resilience.append(symbol)
                rejected.append({"symbol": symbol, "reasons": ["ENTRY_RESILIENCE"], "metrics": row})
            state = {"status": status, "ring": ring, "qualified_since": qualified_since,
                     "failed_since": None, "last_seen": now, "protected": symbol in open_positions}
            next_state[symbol] = state
            if active:
                row["membership"] = "ACTIVE"
                (active_core if ring == "core" else active_outer).append(row)

        for symbol, old in previous.items():
            if symbol in qualified:
                continue
            if str(old.get("status") or "") not in {"CORE_ACTIVE", "OUTER_ACTIVE", "EXIT_PENDING", "HELD_OPEN_POSITION"}:
                continue
            failed_since = float(old.get("failed_since") or now)
            age = now - failed_since
            if symbol in open_positions:
                status = "HELD_OPEN_POSITION"
                protected.append(symbol)
            elif age < self._exit_failure_s:
                status = "EXIT_PENDING"
                exit_candidates.append(symbol)
            else:
                status = "EXITED"
            next_state[symbol] = {**old, "status": status, "failed_since": failed_since, "last_seen": now,
                                  "protected": symbol in open_positions}
            rejected.append({"symbol": symbol, "reasons": [status], "metrics": {"symbol": symbol}})

        self._membership_state = next_state
        try:
            self._save_membership_state()
        except OSError as exc:
            self._last_error = f"MEMBERSHIP_STATE_SAVE_FAILED:{exc}"
        return {
            "enter_candidates": enter_candidates,
            "held_for_resilience": held_for_resilience,
            "exit_candidates": exit_candidates,
            "protected_open_positions": protected,
        }, active_core, active_outer

    async def _on_override(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "").strip().upper()
        decision = str(payload.get("decision") or "").strip().upper()
        if not symbol or decision not in {"ALLOW", "DENY", "NEUTRAL"}:
            return
        self._manual_overrides[symbol] = {
            "decision": decision,
            "scope": str(payload.get("scope") or "BOTH").upper(),
            "reason": str(payload.get("reason") or "manual"),
            "operator": str(payload.get("operator") or "unknown"),
            "command_id": str(payload.get("command_id") or ""),
            "updated_at": time.time(),
        }
        try:
            self._save_overrides()
        except OSError as exc:
            self._last_error = f"OVERRIDE_SAVE_FAILED:{exc}"
            return
        await self._scan_once()

    async def _on_scan_requested(self, _payload: dict[str, Any]) -> None:
        if self._running:
            await self._scan_once()

    async def _poll_loop(self) -> None:
        try:
            while self._running:
                await self._scan_once()
                await asyncio.sleep(max(5.0, self._poll_interval_s))
        except asyncio.CancelledError:
            return

    def _fetch(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        # The ticker request is the single market-wide source of truth. Detail
        # metadata is fetched once per scan only to make spread/classification
        # decisions honest; no per-symbol ticker request is made.
        ticker_body = _get_json(TICKER_URL)
        try:
            detail_body = _get_json(DETAIL_URL)
        except Exception:
            detail_body = []
        return _rows(ticker_body), _rows(detail_body)

    def _normalize(self, ticker: dict[str, Any], details: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        symbol = str(ticker.get("symbol") or "").strip().upper()
        if not symbol:
            return None
        detail = details.get(symbol, {})
        bid = _float(ticker.get("bid1"))
        ask = _float(ticker.get("ask1"))
        last = _float(ticker.get("lastPrice"))
        amount = _float(ticker.get("amount24"))
        high = _float(ticker.get("high24Price"))
        low = _float(ticker.get("lower24Price"))
        tick_size = _float(_detail_value(detail, "priceUnit", "price_unit", "tickSize", "tick_size"))
        spread_ticks = ((ask - bid) / tick_size) if bid is not None and ask is not None and tick_size and ask >= bid else None
        range_pct = ((high - low) / last * 100.0) if high is not None and low is not None and last and last > 0 and high >= low else None
        classification, classification_source = _classify(symbol, detail)
        quote_asset = str(_detail_value(detail, "quoteCoin", "quote_coin", "quoteAsset", "quote_asset") or "").strip().upper()
        settle_asset = str(_detail_value(detail, "settleCoin", "settle_coin", "settlementAsset", "settlement_asset") or "").strip().upper()
        contract_type = str(_detail_value(detail, "contractType", "contract_type", "instrumentType", "instrument_type") or "").strip().upper()
        return {
            "symbol": symbol,
            "provider": PROVIDER,
            "market": "futures",
            "market_segment": "futures_usdt" if quote_asset == "USDT" and settle_asset == "USDT" else "other",
            "contract_type": contract_type,
            "settle_asset": settle_asset,
            "last_price": last,
            "bid": bid,
            "ask": ask,
            "volume24_contracts": _float(ticker.get("volume24")),
            "amount24_usd": amount,
            "high24": high,
            "low24": low,
            "rise_fall_rate": _float(ticker.get("riseFallRate")),
            "rise_fall_value": _float(ticker.get("riseFallValue")),
            "open_interest": _float(ticker.get("holdVol")),
            "index_price": _float(ticker.get("indexPrice")),
            "fair_price": _float(ticker.get("fairPrice")),
            "funding_rate": _float(ticker.get("fundingRate")),
            "price_tick_size": tick_size,
            "contract_size": _float(_detail_value(detail, "contractSize", "contract_size", "contractSizeMultiplier")),
            "base_asset": _detail_value(detail, "baseCoin", "base_coin", "baseAsset", "base_asset"),
            "quote_asset": _detail_value(detail, "quoteCoin", "quote_coin", "quoteAsset", "quote_asset"),
            "asset_class": classification,
            "asset_class_source": classification_source,
            "spread_ticks": spread_ticks,
            "daily_range_pct": range_pct,
            "timestamp_ms": ticker.get("timestamp"),
        }

    def _evaluate(self, row: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        symbol = row["symbol"]
        override = self._manual_overrides.get(symbol, {})
        if str(override.get("decision") or "").upper() == "DENY":
            reasons.append(REASON_MANUAL_DENY)
        # Hard universe boundary: only USDT-settled futures are eligible.
        # A missing contract quote/settle field is unknown, never accepted as
        # a generic crypto asset.
        if not row.get("quote_asset") or not row.get("settle_asset"):
            reasons.append(REASON_FUTURES_METADATA_UNKNOWN)
        elif row.get("market_segment") != "futures_usdt":
            reasons.append(REASON_NOT_FUTURES_USDT)
        amount = row.get("amount24_usd")
        if amount is None:
            reasons.append(REASON_LIQUIDITY_UNKNOWN)
        elif amount < self._outer_min_usd:
            reasons.append(REASON_LIQUIDITY_BELOW_OUTER)
        if row.get("spread_ticks") is None:
            reasons.append(REASON_TICK_SIZE_UNKNOWN if row.get("bid") is not None else REASON_SPREAD_UNKNOWN)
        elif row["spread_ticks"] > self._max_spread_ticks:
            reasons.append(REASON_SPREAD_TOO_WIDE)
        if row.get("asset_class") == "NON_CRYPTO":
            reasons.append(REASON_NON_CRYPTO)
        elif row.get("asset_class") != "NATIVE_CRYPTO":
            reasons.append(REASON_ASSET_CLASS_UNKNOWN)
        if row.get("daily_range_pct") is None:
            reasons.append(REASON_RANGE_UNKNOWN)
        elif row["daily_range_pct"] > self._max_range_pct:
            reasons.append(REASON_RANGE_TOO_WIDE)
        return reasons

    async def _scan_once(self) -> None:
        if self._context is None:
            return
        started = time.time()
        try:
            tickers, detail_rows = await asyncio.to_thread(self._fetch)
            details = {
                str(row.get("symbol") or "").strip().upper(): row
                for row in detail_rows if row.get("symbol")
            }
            rows = [row for ticker in tickers if (row := self._normalize(ticker, details)) is not None]
            candidates: list[dict[str, Any]] = []
            rejected: list[dict[str, Any]] = []
            for row in rows:
                reasons = self._evaluate(row)
                # حكم المالك ٢٠٢٦-٠٨-٢٩: «أدخلت BNB_USDT ⇒ مباشرةً تدخل على
                # تحليل، مو بس مدخل نظري». كان ALLOW يُقبَل بالتحقّق ولا أثر له
                # في `_evaluate` إطلاقًا (DENY وحده مُنفَّذ) — فزرّ «إدخال»
                # يسجّل تجاوزًا ولا يُدخل شيئًا. الآن ALLOW يُدخل الرمز فعلًا.
                # والأقفال التي تجاوزها **لا تُمحى**: تُنقل إلى `manual_allow_bypassed`
                # فتظهر على اللوحة باسمها ورقمها. إدخالٌ بأمر المالك فوق الفرز،
                # لا إخفاءٌ لما قاله الفرز.
                if reasons and str(self._manual_overrides.get(row["symbol"], {})
                                   .get("decision") or "").upper() == "ALLOW":
                    row["manual_allow_bypassed"] = reasons
                    reasons = []
                if reasons:
                    rejected.append({"symbol": row["symbol"], "reasons": reasons, "metrics": row})
                else:
                    candidates.append(row)
            candidates.sort(key=lambda r: (-float(r.get("amount24_usd") or 0), float(r.get("spread_ticks") or 0), r["symbol"]))
            core = candidates[:self._core_target]
            outer = candidates[self._core_target:self._core_target + self._outer_target]
            # v1.1.0: _rotate() below reads row["ring"] to decide CORE_ACTIVE
            # vs OUTER_ACTIVE -- _normalize() never sets this key, so every
            # row (core slice included) silently defaulted to "outer" and
            # the Core tier never existed in the published output. Tag each
            # row with the ring its own slice already decided.
            for row in core:
                row["ring"] = "core"
            for row in outer:
                row["ring"] = "outer"
            # Qualified but not selected is visible, never silently discarded.
            selected_names = {row["symbol"] for row in core + outer}
            for row in candidates:
                if row["symbol"] not in selected_names:
                    rejected.append({"symbol": row["symbol"], "reasons": ["RING_CAPACITY"], "metrics": row})
            rotation, core, outer = self._rotate(core, outer, rejected, time.time())
            self._version += 1
            version = f"U-{time.strftime('%Y%m%d')}-{self._version:06d}"
            snapshot = {
                "universe_version": version,
                "scan_started_at": started,
                "scan_finished_at": time.time(),
                "source": "mexc.contract.ticker.all",
                "thresholds": {
                    "core_liquidity_usd_24h": self._core_min_usd,
                    "outer_liquidity_usd_24h": self._outer_min_usd,
                    "max_spread_ticks": self._max_spread_ticks,
                    "max_daily_range_pct": self._max_range_pct,
                    "core_target_count": self._core_target,
                    "outer_target_count": self._outer_target,
                },
                "core": core,
                "outer": outer,
                "rejected": rejected,
                "rotation": rotation,
            }
            self._last_snapshot = snapshot
            self._last_success_at = time.time()
            self._last_error = ""
            self._scans += 1
            self._accepted = len(core) + len(outer)
            self._rejected = len(rejected)
            membership = {
                "universe_version": version,
                "core": core,
                "outer": outer,
                "symbols": [row["symbol"] for row in core + outer],
            }
            await self._context.publish(EVENT_SNAPSHOT, snapshot)
            await self._context.publish(EVENT_MEMBERSHIP, membership)
            await self._context.publish(EVENT_REJECTED, {
                "universe_version": version,
                "rejected": rejected,
                "count": len(rejected),
            })
            await self._context.publish(EVENT_TICKER_ALL, {
                "universe_version": version,
                "membership": membership,
                "rows": rows,
            })
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            await self._context.publish(EVENT_SNAPSHOT, {
                "universe_version": None,
                "status": "UNIVERSE_SOURCE_STALE",
                "error": self._last_error,
                "scan_started_at": started,
                "scan_finished_at": time.time(),
            })

    async def health_check(self) -> HealthStatus:
        details = {
            "scans": self._scans,
            "accepted": self._accepted,
            "rejected": self._rejected,
            "last_success_at": self._last_success_at,
            "age_s": time.time() - self._last_success_at if self._last_success_at else None,
            "last_error": self._last_error,
            "overrides": len(self._manual_overrides),
        }
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_success_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_UNIVERSE_SCAN", details=details)
        if self._last_error:
            return HealthStatus(state=HealthState.DEGRADED, message="UNIVERSE_SOURCE_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY, message=f"accepted={self._accepted}", details=details)

    async def snapshot(self) -> dict[str, Any] | None:
        return self._last_snapshot

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._last_snapshot = state
            self._version = max(self._version, int(str(state.get("universe_version") or "0").split("-")[-1] or 0))
