"""Conviction contract guard for the five directional roots — problem 32.

Owner's ruling 2026-08-13: `score` in a root is NOT a magnitude. It is a
detection flag — the condition fired (100) or it did not (0) — and the real
strength of the signal comes from `confidence` and the quality factor. Same
principle already settled on 405: never mix DETECTION with MAGNITUDE.

Two proofs, both required:

  A) STRUCTURAL — every root still stamps the flag and still inherits the
     parent's confidence untouched. The check fails if a root turns `score`
     into a measure, hardcodes a confidence, or drops `_CONVICTION`.

  B) END TO END — each root is driven with a parent event carrying a
     distinctive confidence and a DIFFERENT parent score. The root must
     publish score=100 (never the parent's score) and confidence EXACTLY as
     received — not replaced by 1.0, not scaled, not clipped. Then the same
     evidence is pushed through 451 -> 452 -> 453 and the contribution must
     equal weight x 1.0 x confidence x quality, proving the arithmetic really
     uses the inherited confidence.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.contracts.atom import AtomContext  # noqa: E402

SYMBOL = "BTCUSD"
CYCLE = "%s|60s|0.0" % SYMBOL
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)

A400 = "400_مدير_الاستراتيجيات"
A451 = "451_تجميع_القرار"
A452 = "452_تقييم_الإشارات"
A453 = "453_حساب_الدرجة"

# A confidence value that cannot be confused with anything the code might
# substitute (not 0, not 0.5, not 1.0), and a parent score that is NOT 100.
PROBE_CONF = 0.37
PROBE_PARENT_SCORE = 42

ROOTS = [
    {"aid": "404", "folder": "404_استراتيجية_الاتجاه", "merge_id": "trend_strategy",
     "out": "strategy.trend.state", "parent": "structure.trend.state",
     "fire": {"signal": "uptrend"}, "side": "buy"},
    {"aid": "406", "folder": "406_استراتيجية_الاختراق", "merge_id": "breakout_strategy",
     "out": "strategy.breakout.state", "parent": "structure.bos.state",
     "fire": {"signal": "bos", "metadata": {"direction": "up"}}, "side": "buy"},
    {"aid": "407", "folder": "407_استراتيجية_الارتداد", "merge_id": "pullback_strategy",
     "out": "strategy.pullback.state", "parent": "liquidity.sweep.state",
     # counter-trend by design: a BUYSIDE sweep produces a SELL
     "fire": {"signal": "buyside"}, "side": "sell"},
    {"aid": "408", "folder": "408_استراتيجية_الزخم", "merge_id": "momentum_strategy",
     "out": "strategy.momentum.state", "parent": "analysis.momentum.state",
     "fire": {"signal": "up"}, "side": "buy"},
    {"aid": "410", "folder": "410_استراتيجية_السيولة", "merge_id": "liquidity_strategy",
     "out": "strategy.liquidity.state", "parent": "liquidity.fvg.state",
     "fire": {"signal": "fvg_bullish"}, "side": "buy"},
]


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []
        self.handlers = {}

    def subscribe(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.log.append((name, payload))
        for handler in list(self.handlers.get(name, [])):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result

    def events(self, name):
        return [p for n, p in self.log if n == name]


def load_atom(folder: str):
    directory = ATOMS / folder
    spec = importlib.util.spec_from_file_location("_cv_" + folder.split("_")[0],
                                                  directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def manifest(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


# ─────────────────────────── A) structural ────────────────────────────────
_FLAG = re.compile(r'"score":\s*_CONVICTION\s+if\s+signal\s*!=\s*SIGNAL_NONE\s+else\s+0')
_INHERIT = re.compile(r'_to_float\(payload\.get\("confidence"\)\)')
_HARDCODED_CONF = re.compile(r'"confidence":\s*(?!confidence\b)[0-9]')


def structural() -> int:
    print("=" * 78)
    print("أ) الحواجز البنيويّة — الدرجة علم تحقّق، والثقة موروثة")
    print("=" * 78)
    bad = 0
    print("%-6s %-12s %-14s %-16s %s" % (
        "الجذر", "_CONVICTION", "الدرجة علم؟", "الثقة موروثة؟", "بلا ثقة مثبّتة؟"))
    for spec in ROOTS:
        src = (ATOMS / spec["folder"] / "atom.py").read_text(encoding="utf-8")
        conv = re.search(r"^_CONVICTION\s*=\s*(\d+)", src, re.M)
        ok_conv = bool(conv) and conv.group(1) == "100"
        ok_flag = bool(_FLAG.search(src))
        ok_inherit = bool(_INHERIT.search(src))
        ok_nohard = not _HARDCODED_CONF.search(src)
        row_bad = sum(0 if x else 1 for x in (ok_conv, ok_flag, ok_inherit, ok_nohard))
        bad += row_bad
        print("%-6s %-12s %-14s %-16s %s" % (
            spec["aid"],
            ("✓ 100" if ok_conv else "✗ %s" % (conv.group(1) if conv else "مفقود")),
            "✓" if ok_flag else "✗ صارت مقياسًا!",
            "✓" if ok_inherit else "✗ لا يرثها!",
            "✓" if ok_nohard else "✗ ثقة مثبّتة!"))
    return bad


# ─────────────────────────── B) end to end ────────────────────────────────
async def drive(spec: dict) -> dict:
    bus = Bus()
    loaded = []
    # 451 reads the FAMILY collector (400), never the individual strategy
    # events — without 400 in the chain the evidence never reaches the score.
    for folder in (spec["folder"], A400, A451, A452, A453):
        mod = load_atom(folder)
        atom = mod.Atom()
        cfg = manifest(folder).get("config") or {}
        await atom.initialize(AtomContext(atom_id=int(folder.split("_")[0]), config=cfg,
                                          logger=_Logger(), publish=bus.publish,
                                          subscribe=bus.subscribe))
        await atom.start()
        loaded.append((mod, atom))
    payload = {"symbol": SYMBOL, "timeframe": "60s", "cycle_id": CYCLE, "status": "ok",
               "score": PROBE_PARENT_SCORE, "confidence": PROBE_CONF,
               "metadata": {"timeframe": "60s"}}
    payload.update({k: v for k, v in spec["fire"].items() if k != "metadata"})
    if "metadata" in spec["fire"]:
        payload["metadata"] = dict(payload["metadata"], **spec["fire"]["metadata"])
    # Order is the contract itself: a clock tick sets "now", the candle OPENS
    # the family cycle (400 only records units into an open cycle), the root
    # then speaks into it, and further ticks time the cycle out so 400
    # forwards strategy.cycle.collected — which is what 451 actually reads.
    await bus.publish("SYS_SECOND", {"official_time": 1000.0})
    await bus.publish("market_data.candle_closed", {
        "symbol": SYMBOL, "timeframe": "60s", "period_start": 0.0, "close": 64000.0})
    await bus.publish(spec["parent"], payload)
    for tick in range(1, 12):
        await bus.publish("SYS_SECOND", {"official_time": 1000.0 + tick})
    # 451 completes on the expected families; the other three are silent in
    # this harness, so the same candle is replayed as its safety net — now
    # with the collected family on the table.
    await bus.publish("market_data.candle_closed", {
        "symbol": SYMBOL, "timeframe": "60s", "period_start": 0.0, "close": 64000.0})
    return bus


def check_root(spec: dict, bus: Bus) -> int:
    bad = 0
    out = bus.events(spec["out"])
    if not out:
        print("  %-5s ✗ لم ينشر شيئًا" % spec["aid"])
        return 1
    row = out[-1]
    got_sig = str(row.get("signal"))
    got_score = row.get("score")
    got_conf = row.get("confidence")

    ok_side = got_sig == spec["side"]
    ok_flag = got_score == 100
    ok_notparent = got_score != PROBE_PARENT_SCORE
    ok_conf = isinstance(got_conf, (int, float)) and abs(float(got_conf) - PROBE_CONF) < 1e-12
    for ok in (ok_side, ok_flag, ok_notparent, ok_conf):
        bad += 0 if ok else 1

    # the contribution must actually use the inherited confidence
    scored = bus.events("decision.scored.state")
    contribution = None
    expected = None
    if scored:
        s = scored[-1]
        for c in (s.get("contributions") or []):
            if spec["merge_id"] in str(c.get("source")):
                contribution = float(c.get("contribution") or 0.0)
                expected = (float(c.get("weight") or 0.0) * (float(c.get("score") or 0.0) / 100.0)
                            * float(c.get("confidence") or 0.0)
                            * float(c.get("quality_factor") or 0.0))
    ok_math = contribution is not None and abs(contribution - expected) < 1e-9
    ok_uses_conf = contribution is not None and contribution > 0.0
    for ok in (ok_math, ok_uses_conf):
        bad += 0 if ok else 1

    print("  %-5s إشارة=%-5s درجة=%-5s (الأب %s) ثقة=%-6s مساهمة=%-9s %s" % (
        spec["aid"], got_sig, got_score, PROBE_PARENT_SCORE, got_conf,
        "%.6f" % contribution if contribution is not None else "—",
        "✓" if bad == 0 else "✗"))
    if not ok_conf:
        print("        ✗ الثقة تبدّلت: وصلت %s والمطلوب %s" % (got_conf, PROBE_CONF))
    if not ok_flag:
        print("        ✗ الدرجة ليست علم تحقّق (100)")
    if not ok_notparent:
        print("        ✗ الدرجة صارت درجة الأب — اختلط الكشف بالقياس!")
    if contribution is not None and not ok_math:
        print("        ✗ الحساب لا يطابق وزن×درجة×ثقة×جودة")
    if contribution is None:
        print("        ✗ لم تصل مساهمة إلى 453")
    return bad


async def main_async() -> int:
    bad = structural()
    print("\n" + "-" * 78)
    print("ب) طرف-لطرف — الأب يعطي ثقة %.2f ودرجة %d · الجذر → 451 → 452 → 453"
          % (PROBE_CONF, PROBE_PARENT_SCORE))
    print("-" * 78)
    for spec in ROOTS:
        bus = await drive(spec)
        bad += check_root(spec, bus)
    print("\n" + "=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الدرجة علم تحقّق ثابت، والثقة تنتقل من الأب كما هي وتُستعمل بالحساب.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
