"""Contract guard for atom 405 (reversal strategy) — problem 31.

Two proofs, both required:

  A) STRUCTURAL — the prohibitions are still written where they belong, so a
     future edit that quietly promotes 405 trips this check instead of
     reaching the market: 405's own code carries no buy/sell literal,
     413 keeps reversal_strategy out of its directional vote, and 453's
     declared directional_sources does not contain it.

  B) END TO END — the REAL atoms wired on a routing bus:
        structure.trend.state -> 405 -> 413 -> 401
                                     -> 451 -> 452 -> 453 -> 458 -> 581
     A 'transition' fires the reversal and NOTHING else speaks. The chain must
     end at WAIT/NEUTRAL with a zero net: a reversal alone can never create a
     direction or a position.

Owner's ruling 2026-08-13 (problem 31): 405 stays a BINARY DETECTION
(transition -> reversal, else none); score=100 is a detection flag, not a
strength; the inherited confidence is not re-read as conviction; and 405 may
never be promoted to directional while it drinks from structure.trend, the
same root parent that feeds 404. Exit 1 on any divergence.
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
ACCOUNT = "52992818"
CYCLE = "%s|60s|0.0" % SYMBOL
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)

A405 = "405_استراتيجية_الانعكاس"
A409 = "409_استراتيجية_المدى"
A413 = "413_دمج_الإشارات"
A401 = "401_قواعد_الدخول"
A400 = "400_مدير_الاستراتيجيات"
A451 = "451_تجميع_القرار"
A452 = "452_تقييم_الإشارات"
A453 = "453_حساب_الدرجة"
A458 = "458_حل_التعارض"

# One guard, two contextual strategies. Same machinery, different words: both
# are binary detections hanging off the SAME root parent (structure.trend)
# that feeds root 404, so neither may ever become an independent vote.
SPEC_405 = {"folder": A405, "aid": "405", "name": "انعكاس",
            "trigger": "transition", "out": "reversal", "merge_id": "reversal_strategy",
            "trigger_conf": 0.5}
SPEC_409 = {"folder": A409, "aid": "409", "name": "المدى",
            "trigger": "range", "out": "ranging", "merge_id": "range_strategy",
            # 207 publishes confidence 0.0 for the range branch — inherited as is.
            "trigger_conf": 0.0}


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
    spec = importlib.util.spec_from_file_location("_c405_" + folder.split("_")[0],
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
def structural(spec: dict = None) -> int:
    spec = spec or SPEC_405
    folder, aid, merge_id = spec["folder"], spec["aid"], spec["merge_id"]
    print("=" * 78)
    print("أ) الحواجز البنيويّة — مكتوبة حيث يجب، فأي ترقية صامتة تصطدم بها")
    print("=" * 78)
    bad = 0

    src = (ATOMS / folder / "atom.py").read_text(encoding="utf-8")
    literals = re.findall(r"[\"'](buy|sell|BUY|SELL)[\"']", src)
    ok = not literals
    bad += 0 if ok else 1
    print("  %s لا يحمل حرف buy/sell في كوده            : %s" % (aid, (
        "✓" if ok else "✗ وجدت %s" % set(literals))))

    mine = manifest(folder)
    ok = mine.get("subscribes") == ["structure.trend.state"]
    bad += 0 if ok else 1
    print("  مدخله الوحيد structure.trend.state           : %s (%s)" % (
        "✓" if ok else "✗", mine.get("subscribes")))

    m404 = manifest("404_استراتيجية_الاتجاه")
    shares = set(m404.get("subscribes") or []) & set(mine.get("subscribes") or [])
    print("  يشارك الجذر 404 نفس الأب                     : %s  ⇒ ليس استقلالًا إحصائيًّا" % (
        ", ".join(sorted(shares)) or "لا"))

    merged = load_atom(A413)
    ok = merge_id not in merged.DIRECTIONAL_IDS
    bad += 0 if ok else 1
    print("  413 يستثنيه من عدّ الأصوات الاتّجاهيّة        : %s (%s)" % (
        "✓" if ok else "✗ دخل القائمة!", ", ".join(merged.DIRECTIONAL_IDS)))

    declared = [str(s) for s in manifest(A453)["config"]["directional_sources"]]
    ok = not any(merge_id in s for s in declared)
    bad += 0 if ok else 1
    print("  453 لا يعلنه مصدرًا اتّجاهيًّا                 : %s" % ("✓" if ok else "✗ أُعلن!"))

    scorer = load_atom(A453)
    ok = "CONTEXT_ONLY" not in scorer._ABSENT_REASONS
    bad += 0 if ok else 1
    print("  CONTEXT_ONLY يبقى بالمقام (لا يُحذف)          : %s" % ("✓" if ok else "✗"))
    return bad


# ─────────────────────────── B) end to end ────────────────────────────────
async def chain(trend_signal: str, spec: dict = None, confidence: float = 0.5) -> dict:
    spec = spec or SPEC_405
    bus = Bus()
    mods = {}
    # 400 (the family collector) and the ordering below are what actually
    # carry the evidence to 453. Without them the chain reaches WAIT for the
    # trivial reason that nothing arrived at all — a guard that proves nothing.
    for folder in (spec["folder"], A413, A401, A400, A451, A452, A453, A458):
        mod = load_atom(folder)
        atom = mod.Atom()
        cfg = manifest(folder).get("config") or {}
        await atom.initialize(AtomContext(atom_id=int(folder.split("_")[0]), config=cfg,
                                          logger=_Logger(), publish=bus.publish,
                                          subscribe=bus.subscribe))
        await atom.start()
        mods[folder] = (mod, atom)

    candle = {"symbol": SYMBOL, "timeframe": "60s", "period_start": 0.0, "close": 64000.0}
    await bus.publish("SYS_SECOND", {"official_time": 1000.0})
    await bus.publish("market_data.candle_closed", candle)   # opens the family cycle
    await bus.publish("structure.trend.state", {
        "symbol": SYMBOL, "timeframe": "60s", "cycle_id": CYCLE,
        "signal": trend_signal, "score": 0 if trend_signal == spec["trigger"] else 20,
        "confidence": confidence, "status": "ok",
        "metadata": {"timeframe": "60s"}})
    for tick in range(1, 12):
        await bus.publish("SYS_SECOND", {"official_time": 1000.0 + tick})
    await bus.publish("market_data.candle_closed", candle)   # 451's safety net
    return bus


def report(bus: Bus, title: str, spec: dict = None) -> int:
    spec = spec or SPEC_405
    aid, out_signal = spec["aid"], spec["out"]
    print("\n" + "-" * 78)
    print(title)
    print("-" * 78)
    bad = 0
    rev = bus.events("strategy.%s.state" % ("reversal" if aid == "405" else "range"))
    merged = bus.events("strategy.merged.state")
    entry = bus.events("strategy.entry.state")
    scored = bus.events("decision.scored.state")
    resolved = bus.events("decision.resolved.state")

    if rev:
        r = rev[-1]
        print("  %s  signal=%-9s score=%-4s conf=%-4s  (كشف ثنائيّ)" % (
            aid, r.get("signal"), r.get("score"), r.get("confidence")))
        if str(r.get("signal")) not in (out_signal, "none"):
            print("       ✗ أخرج إشارة خارج العقد!")
            bad += 1
    else:
        print("  %s  لم ينشر — ✗" % aid)
        bad += 1

    if merged:
        m = merged[-1]
        sig = str(m.get("signal"))
        ok = sig not in ("buy", "sell")
        bad += 0 if ok else 1
        print("  413  merged=%-8s score=%-4s  %s" % (
            sig, m.get("score"), "✓ لم يتحوّل لاتجاه" if ok else "✗ صار اتجاهًا!"))
    if entry:
        e = entry[-1]
        sig = str(e.get("signal"))
        ok = sig not in ("buy", "sell")
        bad += 0 if ok else 1
        print("  401  entry=%-9s score=%-4s  %s" % (
            sig, e.get("score"), "✓" if ok else "✗ صار اتجاهًا!"))

    if scored:
        s = scored[-1]
        net = float(s.get("net") or 0.0)
        strength = float(s.get("strength") or 0.0)
        ok = abs(net) < 1e-9 and strength < 1e-9
        bad += 0 if ok else 1
        print("  453  net=%-10.6f strength=%-10.6f present=%-8s  %s" % (
            net, strength, s.get("weight_present"), "✓ صفر" if ok else "✗ أنتج كتلة!"))
        for c in (s.get("contributions") or []):
            if spec["merge_id"] in str(c.get("source")):
                print("       ✗ %s دخل البسط بمساهمة %s!" % (aid, c.get("contribution")))
                bad += 1
        for e in (s.get("evidence") or []):
            if spec["merge_id"] in str(e.get("source")):
                print("       %s بالأدلّة: kind=%s eligible=%s reason=%s" % (
                    aid, e.get("kind"), e.get("eligible"), e.get("eligibility_reason")))

    if resolved:
        d = resolved[-1]
        direction = str(d.get("direction"))
        strength = float(d.get("strength") or 0.0)
        ok = direction in ("wait", "neutral") and strength < 1e-9
        bad += 0 if ok else 1
        print("  458  direction=%-8s strength=%-9.6f reason=%-22s %s" % (
            direction, strength, d.get("reason"),
            "✓ لا اتجاه" if ok else "✗ أنتج اتجاهًا!"))
    else:
        print("  458  لم يصدر قرارًا (لا أدلّة مؤهّلة) — ✓ مقبول")
    return bad


async def main_async(spec: dict = None) -> int:
    spec = spec or SPEC_405
    aid, name = spec["aid"], spec["name"]
    bad = structural(spec)
    bus = await chain(spec["trigger"], spec, spec["trigger_conf"])
    bad += report(bus, "ب) طرف-لطرف: %s وحده — 207 → %s → 413/451/452/453 → 458" % (
        name, aid), spec)
    bus2 = await chain("downtrend", spec, 0.5)
    bad += report(bus2, "ب٢) ضبط: اتجاه هابط — %s يصمت بصدق" % aid, spec)
    print("\n" + "=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: %s كشف سياقيّ ثنائيّ — لا ينتج اتجاهًا ولا مركزًا، ولا يستطيع." % aid)
    return 1 if bad else 0



# ⏳ م-56/م-58 (ورقة ٤١، 2026-08-28): انهيار AttributeError أُصلح والفحص يعمل،
# لكن عقوده متقادمة — الذرّة صارت تعمل بالتكة الموثّقة (market.tick.validated →
# strategy.*.state) والفحص لا يزال يسبر البنية القديمة (structure.trend.state).
# ترحيله الكامل لنافذة لاحقة — يبقى أحمر صادقًا بلا تلوين.

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async(SPEC_405)))
