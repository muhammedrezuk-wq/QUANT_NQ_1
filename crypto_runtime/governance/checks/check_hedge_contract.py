"""Hedge-contract proof — the owner's FINAL table (2026-08-13).

An INDEPENDENT ruler is written here from the owner's words alone; it imports
nothing from atom 581. On top of the field-by-field comparison it asserts the
owner's own percentages verbatim:

      S        E(S)    H(S)    Net as % of Capacity
      < 0.20    0%     100%    0%   (net zero, GROSS KEPT -- never flat)
      0.20-40  10%      70%    3%
      0.40-60  25%      40%    15%
      0.60-90  50%      20%    40%
      >= 0.90 100%       0%    100%

Then: band edges, lot-step rounding, transitions up and down, reversal,
gross preservation at neutral, and the risk limits. Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)
sys.path.insert(0, str(ROOT))

from core.contracts.atom import AtomContext  # noqa: E402

# ── the owner's two ladders, typed from his message ───────────────────────
# Item 6, owner's ruling 2026-08-15: STRIKE the `>= 0.90 -> 100%` promise.
# Measured full consensus of the five declared roots is S = 0.8256 against a
# denominator of 6.0564, and a search for an eighth root source found none --
# so 0.90 is unreachable by construction. A ladder that advertises a band the
# system can never enter is a paper promise, and he ordered it removed rather
# than reached by lowering a threshold or normalising the number.
# The top REAL band is therefore 0.60 and above: E 50%, H 20%, net 40%.
EXPOSURE_BANDS = {0.0: 0.0, 0.2: 0.1, 0.4: 0.25, 0.6: 0.5}
HEDGE_BANDS = {0.0: 1.0, 0.2: 0.7, 0.4: 0.4, 0.6: 0.2}
# His stated result column, independent of any formula: net / capacity.
# The struck band collapses into the one below it: 0.90 and 0.971 are now the
# SAME 40% as 0.60. Nothing above 0.60 promises more than the system can do.
OWNER_NET_PCT = {0.20: 0.03, 0.40: 0.15, 0.60: 0.40, 0.90: 0.40, 0.971: 0.40}

_STRUCK_BAND = "0.9"


def _assert_shipped_ladder() -> None:
    """The ladder the ATOM SHIPS, not the one this guard injects.

    Injecting the table into 581 and then checking the same table is circular:
    both sides move together and the struck band would pass unnoticed. The
    barrier reads what 581 actually carries -- its module defaults AND its card.
    """
    import re as _re

    import yaml as _yaml
    folder = ATOM_ROOT / "581_محرك_فرق_المركز"
    src = (folder / "atom.py").read_text(encoding="utf-8")
    card = _yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8")) or {}
    shipped = card.get("config") or {}
    problems = []
    for name in ("DEFAULT_BANDS", "DEFAULT_HEDGE_BANDS"):
        line = _re.search(r"^%s\s*=\s*\{([^}]*)\}" % name, src, _re.M)
        if line and ('"%s"' % _STRUCK_BAND) in line.group(1):
            problems.append("%s فيه %s" % (name, _STRUCK_BAND))
    for name in ("bands", "hedge_bands"):
        table = shipped.get(name)
        if isinstance(table, dict) and _STRUCK_BAND in {str(k) for k in table}:
            problems.append("البطاقة %s فيها %s" % (name, _STRUCK_BAND))
    print("  البند ٦ — نطاق ≥0.90 مشطوب من المشحون: %s"
          % ("✓" if not problems else "✗ " + " · ".join(problems)))
    if problems:
        raise SystemExit(1)

S_ENTER = 0.20
S_EXIT = 0.15
MAX_TARGET = 20.0
MAX_STEP = 1.0
MIN_VOLUME = 0.01
LOT_STEP = 0.01
EPS = 1e-9

BUDGET = 100.0
PRICE = 64000.0
STOP_FRAC = 0.0055
VPU = 1.0
CAPACITY = min(MAX_TARGET, BUDGET / (PRICE * STOP_FRAC * VPU))
GROSS_CAP = min(MAX_TARGET, 2.0 * BUDGET / (PRICE * STOP_FRAC * VPU))


def band(table: dict[float, float], strength: float, default: float) -> float:
    result = default
    for threshold in sorted(table):
        if strength >= threshold:
            result = table[threshold]
    return max(0.0, min(1.0, result))


class Ruler:
    """The owner's contract, executed. Nothing here comes from 581."""

    def __init__(self) -> None:
        self.held: str | None = None
        self.prev_strength: float | None = None
        self.prev_gross: float | None = None

    def compute(self, desired: str, strength: float, current_buy: float,
                current_sell: float, state: str = "NORMAL") -> dict:
        current_net = current_buy - current_sell
        current_gross = current_buy + current_sell
        if state in ("FROZEN", "PAUSED"):
            return {"action": "BLOCKED", "status": "BLOCKED"}
        if state in ("WARNING", "HEDGING"):
            return self._finish(0.0, min(current_gross, GROSS_CAP), current_buy, current_sell)
        held = self._direction(desired, strength, current_net)
        exposure = band(EXPOSURE_BANDS, strength, 0.0)
        hedge = band(HEDGE_BANDS, strength, 1.0)
        if exposure <= 0.0:
            # His rule: E = 0 means zero directional NET, not zero position.
            gross = min(current_gross, GROSS_CAP)
        else:
            gross = min(CAPACITY * exposure, GROSS_CAP)
            if (self.prev_strength is not None and strength < self.prev_strength
                    and self.prev_gross is not None):
                gross = min(gross, self.prev_gross)
        sign = 1.0 if held == "buy" else -1.0
        net = 0.0 if held is None else gross * (1.0 - hedge) * sign
        self.prev_strength = strength
        self.prev_gross = gross
        return self._finish(net, gross, current_buy, current_sell)

    def _direction(self, desired: str, strength: float, current_net: float) -> str | None:
        if self.held is None:
            if desired in ("buy", "sell") and strength >= S_ENTER:
                self.held = desired
            return self.held
        if strength <= S_EXIT:
            self.held = None
            return None
        if desired in ("buy", "sell") and desired != self.held:
            if abs(current_net) <= MIN_VOLUME and strength >= S_ENTER:
                self.held = desired
                return self.held
            return None
        return self.held

    @staticmethod
    def _finish(net: float, gross: float, current_buy: float, current_sell: float) -> dict:
        buy = max(0.0, (gross + net) / 2.0)
        sell = max(0.0, (gross - net) / 2.0)
        d_buy = max(-MAX_STEP, min(MAX_STEP, buy - current_buy))
        d_sell = max(-MAX_STEP, min(MAX_STEP, sell - current_sell))
        active = abs(d_buy) >= MIN_VOLUME or abs(d_sell) >= MIN_VOLUME
        current_net = current_buy - current_sell
        if not active:
            d_buy = d_sell = 0.0
            action = "HOLD"
        elif d_buy < -MIN_VOLUME or d_sell < -MIN_VOLUME:
            action = "REBALANCE" if (d_buy > MIN_VOLUME or d_sell > MIN_VOLUME) else "REDUCE"
        else:
            action = "HEDGE" if net * current_net < 0 else "ADD"
        return {"target_net": round(net, 8), "target_gross": round(gross, 8),
                "target_buy": round(buy, 8), "target_sell": round(sell, 8),
                "delta_buy": round(d_buy, 8), "delta_sell": round(d_sell, 8),
                "action": action, "status": "READY"}


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class _Bus:
    def __init__(self):
        self.published = []
        self.handlers = {}

    def subscribe(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.published.append((name, payload))
        for handler in list(self.handlers.get(name, [])):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result


def _load_581():
    path = ATOM_ROOT / "581_محرك_فرق_المركز" / "atom.py"
    spec = importlib.util.spec_from_file_location("_hc581", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_hc581"] = module
    spec.loader.exec_module(module)
    return module


async def _drive(module, scenarios):
    bus = _Bus()
    atom = module.Atom()
    _assert_shipped_ladder()
    config = {"bands": {str(k): v for k, v in EXPOSURE_BANDS.items()},
              "hedge_bands": {str(k): v for k, v in HEDGE_BANDS.items()},
              "s_enter": S_ENTER, "s_exit": S_EXIT,
              "max_target_volume": MAX_TARGET, "max_step_volume": MAX_STEP,
              "min_volume": MIN_VOLUME, "hedge_cost_per_volume": 0.0}
    await atom.initialize(AtomContext(atom_id=581, config=config, logger=_Logger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    await atom.start()
    # 581 تفهرس السعر والمواصفات بمفتاح (حساب، رمز) — الهوية الخماسية؛ بلا
    # account_id لا يُخزَّن شيء فتسقط كل الحالات على MISSING_R_PRICE_DIAL_OR_SPECS.
    await atom._on_specs({"account_id": "A",
                          "symbols": [{"symbol": "BTCUSD", "tick_value": VPU, "tick_size": 1.0}]})
    await atom._on_candle({"account_id": "A", "symbol": "BTCUSD", "close": PRICE})
    await atom._on_dial({"profiles": [{"account_id": "A", "symbol": "BTCUSD",
                                       "stop_distance_frac": STOP_FRAC}]})
    results = []
    for index, step in enumerate(scenarios):
        # ‏581 v3.1.0 تشترط إشارة حياة النظام من محفظة الأصل (519) وإلا حكمت
        # BLOCKED SYSTEM_NOT_ALIVE على كل شيء — هذا الحارس يقيس عقد التحوّط
        # نفسه، فيمرّر الإشارة صراحةً كما تمرّرها 519 حين يكون النظام حيًّا
        # (نفس ما تفعله اختبارات 581 الذاتية). غيابها هنا كان يصفّر الجدول كله.
        await atom._on_portfolio({"portfolios": [{"account_id": "A", "symbol": "BTCUSD",
                                                  "state": step.get("state", "NORMAL"),
                                                  "system_alive": True,
                                                  "account_mode": "HEDGING"}]})
        legs = []
        if step["current_buy"] > 0:
            legs.append({"account_id": "A", "symbol": "BTCUSD", "side": "BUY", "ticket": 1,
                         "volume": step["current_buy"], "entry_price": PRICE})
        if step["current_sell"] > 0:
            legs.append({"account_id": "A", "symbol": "BTCUSD", "side": "SELL", "ticket": 2,
                         "volume": step["current_sell"], "entry_price": PRICE})
        await atom._on_positions({"source": "test", "account_id": "A", "positions": legs})
        await atom._on_ledger({"ledgers": [{"account_id": "A", "symbol": "BTCUSD",
                                            "risk_budget": BUDGET,
                                            "v_net": step["current_buy"] - step["current_sell"]}]})
        bus.published.clear()
        # حكم المالك ٢٠٢٦-٠٨-١٣ (البند ٤): 581 مغلق افتراضًا — لا حكم فلاتر
        # مطابق للدورة ⇒ لا اتجاه. فهذا الحارس يمرّر الحكم صراحةً كي يقيس
        # عقد التحوّط نفسه لا بوّابة الفلاتر.
        await atom._on_verdict({"symbol": "BTCUSD", "cycle_id": "hedge-%d" % index,
                                "metadata": {"approved": True}})
        await atom._on_decision({"symbol": "BTCUSD", "account_id": "A",
                                 "cycle_id": "hedge-%d" % index,
                                 "direction": step["desired"], "strength": step["strength"]})
        targets = [p for n, p in bus.published if n == module.EVENT_OUT]
        results.append(targets[-1] if targets else None)
    return results


def _scenarios():
    rows = []
    # A) every band edge, entered from a flat book
    for s in (0.19, 0.20, 0.39, 0.40, 0.59, 0.60, 0.89, 0.90, 0.971):
        rows.append({"name": "حدّ S=%.3f" % s, "desired": "buy", "strength": s,
                     "current_buy": 0.0, "current_sell": 0.0, "reset": True})
    # B) transitions UP, carrying the book forward
    rows += [
        {"name": "صعود 0.20", "desired": "buy", "strength": 0.20, "current_buy": 0.0, "current_sell": 0.0, "reset": True},
        {"name": "صعود 0.45", "desired": "buy", "strength": 0.45, "current_buy": 0.019, "current_sell": 0.009},
        {"name": "صعود 0.70", "desired": "buy", "strength": 0.70, "current_buy": 0.058, "current_sell": 0.014},
        {"name": "صعود 0.95", "desired": "buy", "strength": 0.95, "current_buy": 0.130, "current_sell": 0.014},
    ]
    # C) transitions DOWN -- gross must shrink, never grow
    rows += [
        {"name": "هبوط من 0.95", "desired": "buy", "strength": 0.95, "current_buy": 0.0, "current_sell": 0.0, "reset": True},
        {"name": "هبوط 0.70", "desired": "buy", "strength": 0.70, "current_buy": 0.288, "current_sell": 0.0},
        {"name": "هبوط 0.45", "desired": "buy", "strength": 0.45, "current_buy": 0.130, "current_sell": 0.014},
        {"name": "هبوط 0.25", "desired": "buy", "strength": 0.25, "current_buy": 0.058, "current_sell": 0.014},
    ]
    # D) the neutral band: net zero but the GROSS IS KEPT (never flat)
    rows += [
        {"name": "حياد S=0.10 والمركز قائم", "desired": "buy", "strength": 0.10,
         "current_buy": 0.144, "current_sell": 0.144, "reset": True},
        {"name": "حياد S=0.05 ومركز اتجاهي", "desired": "buy", "strength": 0.05,
         "current_buy": 0.288, "current_sell": 0.0, "reset": True},
        {"name": "حياد بلا مركز (لا شيء يُفتح)", "desired": "buy", "strength": 0.10,
         "current_buy": 0.0, "current_sell": 0.0, "reset": True},
    ]
    # E) reversal only through a flat book
    rows += [
        {"name": "شراء قائم ثم طلب بيع", "desired": "buy", "strength": 0.95,
         "current_buy": 0.0, "current_sell": 0.0, "reset": True},
        {"name": " ... بيع 0.70 والكتاب شراء", "desired": "sell", "strength": 0.70,
         "current_buy": 0.288, "current_sell": 0.0},
        {"name": " ... الكتاب تعادل ⇒ يتبنّى البيع", "desired": "sell", "strength": 0.70,
         "current_buy": 0.0, "current_sell": 0.0},
        {"name": "بيع قائم ثم طلب شراء", "desired": "buy", "strength": 0.70,
         "current_buy": 0.0, "current_sell": 0.144, "reset": False},
    ]
    # F) risk limits win over the strategy
    rows += [
        {"name": "WARNING يحيّد ويحفظ الإجمالي", "desired": "buy", "strength": 0.95,
         "current_buy": 0.2, "current_sell": 0.0, "state": "WARNING", "reset": True},
        {"name": "FROZEN يمنع كل شيء", "desired": "buy", "strength": 0.95,
         "current_buy": 0.2, "current_sell": 0.0, "state": "FROZEN"},
    ]
    return rows


def main() -> int:
    scenarios = _scenarios()
    module = _load_581()
    engine_rows = asyncio.run(_drive(module, scenarios))

    failures = 0
    fields = ("target_net", "target_gross", "target_buy", "target_sell",
              "delta_buy", "delta_sell", "action")
    ruler = Ruler()
    print("السعة = %.6f لوت   ·   سقف الإجمالي = %.6f\n" % (CAPACITY, GROSS_CAP))
    print("%-34s %-11s %-11s %-11s %-11s %-10s %s" % (
        "الحالة", "الصافي", "الإجمالي", "شراء", "بيع", "الفعل", "الحكم"))
    for step, engine in zip(scenarios, engine_rows):
        if step.get("reset"):
            ruler = Ruler()
        expected = ruler.compute(step["desired"], step["strength"],
                                 step["current_buy"], step["current_sell"],
                                 step.get("state", "NORMAL"))
        if engine is None:
            print("%-34s المحرّك لم ينشر شيئًا" % step["name"])
            failures += 1
            continue
        if expected.get("status") == "BLOCKED":
            ok = engine.get("action") == "BLOCKED"
            print("%-34s %s" % (step["name"], "BLOCKED ✓" if ok else "<-- اختلاف"))
            failures += 0 if ok else 1
            continue
        row_ok = True
        for field in fields:
            got, want = engine.get(field), expected.get(field)
            if isinstance(want, float):
                if got is None or abs(float(got) - want) > 1e-6:
                    row_ok = False
            elif got != want:
                row_ok = False
        buy = float(engine.get("target_buy") or 0.0)
        sell = float(engine.get("target_sell") or 0.0)
        net = float(engine.get("target_net") or 0.0)
        gross = float(engine.get("target_gross") or 0.0)
        identities = (buy >= -EPS and sell >= -EPS
                      and abs((buy - sell) - net) < 1e-6
                      and abs((buy + sell) - gross) < 1e-6)
        ok = row_ok and identities
        failures += 0 if ok else 1
        print("%-34s %-11.6f %-11.6f %-11.6f %-11.6f %-10s %s" % (
            step["name"], net, gross, buy, sell, engine.get("action"),
            "✓" if ok else "<-- اختلاف عن الحاسبة: %s" % expected))

    # ── the owner's own percentages, asserted verbatim ────────────────────
    print("\nنسب المالك حرفيًّا (الصافي ÷ السعة):")
    edge_rows = {s["strength"]: e for s, e in zip(scenarios, engine_rows) if s.get("reset")}
    for s, want_pct in OWNER_NET_PCT.items():
        row = edge_rows.get(s)
        if row is None:
            continue
        got = abs(float(row.get("target_net") or 0.0)) / CAPACITY
        ok = abs(got - want_pct) < 1e-6
        failures += 0 if ok else 1
        print("  S=%-6s المطلوب %-6.2f%%  المقيس %-8.4f%%  %s" % (
            s, want_pct * 100, got * 100, "✓" if ok else "<-- اختلاف"))

    # ── lot-step rounding: direction must survive the broker step ─────────
    print("\nالتقريب لخطوة اللوت (%.2f):" % LOT_STEP)
    for step, engine in zip(scenarios, engine_rows):
        if not step.get("reset") or engine is None or engine.get("target_buy") is None:
            continue
        rb = round(round(float(engine["target_buy"]) / LOT_STEP) * LOT_STEP, 8)
        rs = round(round(float(engine["target_sell"]) / LOT_STEP) * LOT_STEP, 8)
        raw_net = float(engine["target_net"])
        rounded_net = rb - rs
        flipped = raw_net > 1e-9 and rounded_net < -1e-9 or raw_net < -1e-9 and rounded_net > 1e-9
        killed = abs(raw_net) >= MIN_VOLUME and abs(rounded_net) < 1e-9
        bad = flipped or killed or rb < 0 or rs < 0
        failures += 1 if bad else 0
        note = "قلب الاتجاه!" if flipped else ("محا الاتجاه!" if killed else "✓")
        print("  S=%-6s شراء %.6f→%.2f · بيع %.6f→%.2f · صافي %.6f→%.2f  %s" % (
            step["strength"], float(engine["target_buy"]), rb,
            float(engine["target_sell"]), rs, raw_net, rounded_net, note))

    print("\nالحالات=%d  ·  الاختلافات=%d" % (len(scenarios), failures))
    if failures == 0:
        print("سليم: الحاسبة المستقلّة والمحرّك 581 يقولان حقيقة واحدة.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
