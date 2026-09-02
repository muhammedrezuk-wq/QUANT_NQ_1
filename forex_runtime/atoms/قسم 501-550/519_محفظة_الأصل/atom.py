from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "3.2.0"
FAIL_CLOSED = "RESTORE_FAILED_FAIL_CLOSED"
EVENT_LEDGER = "risk.asset_ledger.state"
EVENT_TRADE = "platform.trade_event"
EVENT_ACCOUNT = "platform.account.state"
EVENT_TERMINAL = "platform.terminal_state"
EVENT_HALT = "emergency.halt"
EVENT_RESET = "risk.kill_switch.reset_requested"
EVENT_COMMAND = "risk.asset.command"
EVENT_OUT = "asset.portfolio.state"
EVENT_INTENT = "asset.portfolio.owner_intent"

NORMAL = "NORMAL"
WARNING = "WARNING"
HEDGING = "HEDGING"
FROZEN = "FROZEN"
MANUAL = "MANUAL_RELEASE"
BUY = "BUY"
SELL = "SELL"
NEUTRAL = "NEUTRAL"
NONE = "NONE"
REQUEST_HEDGE = "REQUEST_HEDGE"
FREEZE = "FREEZE"
HOLD = "HOLD"
PAUSE = "pause"
RESUME = "resume"
RELEASE = "release"
FORCE_HEDGE = "force_hedge"
SEP = "\x1f"
ZERO_TOL = 1e-12
WARN_RATIO = 0.95
BREACH_RATIO = 1.0
_VALID_STATES = {NORMAL, WARNING, HEDGING, FROZEN, MANUAL}


def num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._exit_ratio = 0.90
        self._states: dict[str, str] = {}
        self._alive: dict[str, bool] = {}
        self._modes: dict[str, str] = {}
        self._brokers: dict[str, str] = {}
        self._paused: set[str] = set()
        self._halted_accounts: set[str] = set()
        self._last: dict[str, Any] | None = None
        self._updates = 0
        self._restore_error = ""
        self._rehydrated = False
        self._dropped_trade = 0
        self._invalid = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._exit_ratio = float(context.config.get("exit_ratio", 0.90))
        for event, handler in (
            (EVENT_INTENT, self._rehydrate),
            (EVENT_LEDGER, self._on_ledger),
            (EVENT_TRADE, self._on_trade),
            (EVENT_ACCOUNT, self._on_account),
            (EVENT_TERMINAL, self._on_terminal),
            (EVENT_HALT, self._on_halt),
            (EVENT_RESET, self._on_reset),
            (EVENT_COMMAND, self._on_command),
        ):
            context.subscribe(event, handler)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def key(self, payload: dict[str, Any]) -> str:
        account = str(payload.get("account_id") or "")
        broker = str(payload.get("broker") or "") or self._brokers.get(account, "")
        symbol = str(payload.get("symbol") or payload.get("asset_canonical") or "")
        return SEP.join((account, broker, symbol)) if account and broker and symbol else ""

    async def _on_terminal(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "")
        if account:
            self._alive[account] = (
                bool(payload.get("connected", True))
                and bool(payload.get("trade_allowed", True))
            )

    async def _on_halt(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict) and payload.get("account_id"):
            self._halted_accounts.add(str(payload["account_id"]))

    async def _on_reset(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict) and payload.get("account_id"):
            self._halted_accounts.discard(str(payload["account_id"]))

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "")
        broker = str(payload.get("broker") or "")
        mode = payload.get("margin_mode")
        if not account:
            return
        if broker:
            self._brokers[account] = broker
        try:
            self._modes[account] = (
                "HEDGING" if int(mode) == 2
                else "NETTING" if mode is not None
                else "UNKNOWN"
            )
        except (TypeError, ValueError):
            self._modes[account] = str(mode or "UNKNOWN").upper()

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            self._invalid += 1
            return
        if str(payload.get("reason") or "").upper() != "SL":
            self._dropped_trade += 1
            return
        scope = self.key(payload)
        if not scope:
            self._invalid += 1
            return
        self._states[scope] = HEDGING

    async def _rehydrate(self, payload: dict[str, Any]) -> None:
        if self._rehydrated:
            return
        self._rehydrated = True
        intent = payload.get("owner_intent") if isinstance(payload, dict) else None
        if not self._valid_intent(intent):
            self._halted_accounts = set(self._modes) or {"__UNKNOWN__"}
            self._restore_error = FAIL_CLOSED
            return
        self._halted_accounts = set(intent["halted_accounts"])
        self._paused = set(intent["paused"])
        self._states = dict(intent["states"])
        self._restore_error = ""

    @staticmethod
    def _valid_intent(intent: Any) -> bool:
        return bool(
            isinstance(intent, dict)
            and isinstance(intent.get("halted_accounts"), list)
            and all(isinstance(item, str) for item in intent["halted_accounts"])
            and isinstance(intent.get("paused"), list)
            and all(isinstance(item, str) for item in intent["paused"])
            and isinstance(intent.get("states"), dict)
            and all(
                isinstance(key, str) and value in _VALID_STATES
                for key, value in intent["states"].items()
            )
        )

    async def _on_command(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        self._rehydrated = True
        scope = self.key(payload)
        command = str(payload.get("command") or "").lower()
        if not scope:
            self._invalid += 1
            return
        if command in (PAUSE, "freeze"):
            self._paused.add(scope)
            self._states[scope] = FROZEN
        elif command in (RESUME, "unfreeze"):
            self._paused.discard(scope)
            self._states[scope] = NORMAL
        elif command == RELEASE:
            self._paused.discard(scope)
            self._states[scope] = MANUAL
        elif command == FORCE_HEDGE:
            self._states[scope] = HEDGING
        await self._republish_intent()

    @staticmethod
    def direction(value: float | None) -> str:
        if value is None or abs(value) < ZERO_TOL:
            return NEUTRAL
        return BUY if value > 0 else SELL

    def next_state(
        self, scope: str, utilization: float | None, warning: bool, breached: bool
    ) -> tuple[str, str]:
        current = self._states.get(scope, NORMAL)
        account = scope.partition(SEP)[0]
        if scope in self._paused or account in self._halted_accounts:
            return FROZEN, FREEZE
        if current == FROZEN:
            return FROZEN, FREEZE
        if current == HEDGING:
            return HEDGING, HOLD
        if breached:
            return FROZEN, FREEZE
        if warning:
            return WARNING, REQUEST_HEDGE
        if current == WARNING and utilization is not None and utilization <= self._exit_ratio:
            return NORMAL, NONE
        return NORMAL, NONE

    async def _on_ledger(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        raw_rows = payload.get("ledgers")
        rows = raw_rows if isinstance(raw_rows, list) else [payload]
        portfolios = []
        for ledger in rows:
            row = self._portfolio_row(ledger)
            if row is not None:
                portfolios.append(row)
        self._last = {
            "portfolios": portfolios,
            "count": len(portfolios),
            "halted_accounts": sorted(self._halted_accounts),
            "owner_intent": self._intent(),
        }
        self._updates += 1
        await self._context.publish(EVENT_OUT, self._last)
        await self._context.publish(EVENT_INTENT, {
            "account_scope": "519", "owner_intent": self._intent()
        })

    def _portfolio_row(self, ledger: Any) -> dict[str, Any] | None:
        if not isinstance(ledger, dict):
            self._invalid += 1
            return None
        symbol = str(ledger.get("symbol") or ledger.get("asset_canonical") or "")
        account = str(ledger.get("account_id") or "")
        broker = str(ledger.get("broker") or "") or self._brokers.get(account, "")
        if not account or not broker or not symbol:
            self._invalid += 1
            return None
        scope = SEP.join((account, broker, symbol))
        utilization = num(ledger.get("u"))
        warning = bool(ledger.get("warning")) or (
            utilization is not None and utilization >= WARN_RATIO
        )
        breached = bool(ledger.get("breached")) or (
            utilization is not None and utilization >= BREACH_RATIO
        )
        state, intent = self.next_state(scope, utilization, warning, breached)
        self._states[scope] = state
        net = num(ledger.get("v_net"))
        alive = self._alive.get(account)
        mode = self._modes.get(account, "UNKNOWN")
        return {
            "account_id": account, "broker": broker,
            "asset_canonical": symbol, "symbol": symbol,
            "state": state, "direction": self.direction(net),
            "protection_intent": intent, "u": utilization,
            "warning": warning, "breached": breached,
            "budgeted": bool(ledger.get("budgeted")),
            "risk_budget": ledger.get("risk_budget", ledger.get("R")),
            "K": ledger.get("K"), "X": ledger.get("X"), "v_net": net,
            "open_legs": ledger.get("open_legs", ledger.get("position_count")),
            "loss_exposure": ledger.get("loss_exposure"),
            "account_mode": mode, "account_mode_supported": mode == "HEDGING",
            "risk_reason": (
                "NETTING_UNSUPPORTED" if mode == "NETTING"
                else "ACCOUNT_MODE_UNKNOWN" if mode != "HEDGING" else ""
            ),
            "system_alive": alive is True,
            "system_status": (
                "HEALTHY" if alive is True else "DEGRADED" if alive is False else "UNKNOWN"
            ),
            "paused": scope in self._paused,
        }

    def _intent(self) -> dict[str, Any]:
        return {
            "halted_accounts": sorted(self._halted_accounts),
            "paused": sorted(self._paused),
            "states": {str(key): str(value) for key, value in self._states.items()},
        }

    async def _republish_intent(self) -> None:
        if self._context is None:
            return
        body = dict(self._last or {"portfolios": [], "count": 0})
        body.update({
            "halted_accounts": sorted(self._halted_accounts),
            "owner_intent": self._intent(),
        })
        self._last = body
        await self._context.publish(EVENT_OUT, body)
        await self._context.publish(EVENT_INTENT, {
            "account_scope": "519", "owner_intent": self._intent()
        })

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, **self._intent()}

    async def restore(self, state: dict[str, Any]) -> None:
        if not self._valid_intent(state):
            self._halted_accounts = set(self._modes) or {"__UNKNOWN__"}
            self._restore_error = FAIL_CLOSED
            raise ValueError(FAIL_CLOSED)
        self._halted_accounts = set(state["halted_accounts"])
        self._paused = set(state["paused"])
        self._states = dict(state["states"])
        self._restore_error = ""

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {
            "updates": self._updates, "tracked": len(self._states),
            "halted_accounts": sorted(self._halted_accounts),
            "paused": len(self._paused), "restore_error": self._restore_error,
            "dropped_trade": self._dropped_trade, "invalid": self._invalid,
        }
        if self._last is None:
            return HealthStatus(
                state=HealthState.DEGRADED, message="NO_LEDGER_YET", details=details
            )
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="portfolios=%d dropped_trade=%d" % (
                self._last.get("count", 0), self._dropped_trade
            ),
            details=details,
        )
