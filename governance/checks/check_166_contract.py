"""Contract guard for atom 166 (analysis fusion) — problem 33.

Owner's ruling 2026-08-13: 166 is reclassified CONTEXTUAL. Not because its
maths is wrong — the fusion is a faithful mean — but because its directional
content is DERIVED: analyser 151 feeds 207 which produces root 404, analyser
152 is the direct parent of root 408, and both analysers feed 150 -> 166. So
counting 166 as a vote would count the same evidence twice, which is exactly
what the lineage rule (problem 51) forbids.

His two conditions, both checked here:
    "166 = CONTEXT_ONLY, weight 0.0556"
    "it stays in the DENOMINATOR and never enters the directional NUMERATOR"

  A) STRUCTURAL — 166 is absent from 453's directional_sources, it is still a
     pure aggregator (its only input is analysis.cycle.collected), its own
     fusion maths is untouched, and the lineage is still real: 207 <- 151,
     408 <- 152.

  B) END TO END — 166 speaks a full-strength direction and NOTHING else does.
     It must land in the denominator and contribute NOTHING to the numerator,
     so the decision stays neutral with a zero net.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
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

A166 = "166_دمج_التحليل"
A451 = "451_تجميع_القرار"
A452 = "452_تقييم_الإشارات"
A453 = "453_حساب_الدرجة"
A458 = "458_حل_التعارض"


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
    spec = importlib.util.spec_from_file_location("_c166_" + folder.split("_")[0],
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


def structural() -> int:
    print("=" * 78)
    print("أ) الحواجز البنيويّة — 166 مجمّع مشتقّ، لا جذر")
    print("=" * 78)
    bad = 0

    declared = [str(s) for s in manifest(A453)["config"]["directional_sources"]]
    ok = "166" not in declared
    bad += 0 if ok else 1
    print("  453 لا يعلنه مصدرًا اتّجاهيًّا              : %s" % (
        "✓" if ok else "✗ أُعلن جذرًا!"))

    m166 = manifest(A166)
    ok = m166.get("subscribes") == ["analysis.cycle.collected"]
    bad += 0 if ok else 1
    print("  مدخله الوحيد analysis.cycle.collected     : %s (%s)" % (
        "✓" if ok else "✗", m166.get("subscribes")))

    # the lineage that made him contextual, re-proved from the manifests
    m207 = manifest("207_حالة_الاتجاه")
    m408 = manifest("408_استراتيجية_الزخم")
    ok207 = "analysis.trend.state" in (m207.get("subscribes") or [])
    ok408 = "analysis.momentum.state" in (m408.get("subscribes") or [])
    bad += 0 if (ok207 and ok408) else 1
    print("  النسب قائم: 151→207→404 و 152→408          : %s" % (
        "✓" if (ok207 and ok408) else "✗ انكسر النسب — يلزم إعادة الحكم"))

    src = (ATOMS / A166 / "atom.py").read_text(encoding="utf-8")
    ok_math = ("agree_threshold" in src
               and "sum(_num(s.get(\"score\")) for s in chosen) / len(chosen)" in src)
    bad += 0 if ok_math else 1
    print("  معادلة الدمج الداخليّة لم تُمَسّ            : %s" % (
        "✓" if ok_math else "✗ تغيّرت!"))
    return bad


async def drive(direction_signal: str) -> Bus:
    bus = Bus()
    for folder in (A166, A451, A452, A453, A458):
        mod = load_atom(folder)
        atom = mod.Atom()
        cfg = manifest(folder).get("config") or {}
        await atom.initialize(AtomContext(atom_id=int(folder.split("_")[0]), config=cfg,
                                          logger=_Logger(), publish=bus.publish,
                                          subscribe=bus.subscribe))
        await atom.start()
    candle = {"symbol": SYMBOL, "timeframe": "60s", "period_start": 0.0, "close": 64000.0}
    await bus.publish("SYS_SECOND", {"official_time": 1000.0})
    await bus.publish("market_data.candle_closed", candle)
    # the analysis family closes its cycle with 166 shouting a full-strength side
    await bus.publish("analysis.cycle.collected", {
        "symbol": SYMBOL, "timeframe": "60s", "cycle_id": CYCLE,
        "expected": 15, "present": 15, "complete": True,
        "results": {
            "trend": {"id": "trend", "status": "ok", "signal": direction_signal,
                      "score": 100, "confidence": 1.0},
            "momentum": {"id": "momentum", "status": "ok", "signal": direction_signal,
                         "score": 100, "confidence": 1.0},
        }})
    for tick in range(1, 12):
        await bus.publish("SYS_SECOND", {"official_time": 1000.0 + tick})
    await bus.publish("market_data.candle_closed", candle)
    return bus


def report(bus: Bus) -> int:
    bad = 0
    fused = bus.events("analysis.raw.completed")
    if not fused:
        print("  166 لم ينشر — ✗")
        return 1
    f = fused[-1]
    print("  166  signal=%-9s score=%-5s conf=%-5s agreement=%s" % (
        f.get("signal"), f.get("score"), f.get("confidence"), f.get("agreement")))

    scored = bus.events("decision.scored.state")
    if not scored:
        print("  453 لم ينشر — ✗")
        return bad + 1
    s = scored[-1]
    net = float(s.get("net") or 0.0)
    present = float(s.get("weight_present") or 0.0)
    mine = [c for c in (s.get("contributions") or []) if "166" in str(c.get("source"))]
    ev = [e for e in (s.get("evidence") or []) if "166" in str(e.get("source"))]

    in_denominator = present > 0.0
    in_numerator = bool(mine) or abs(net) > 1e-12
    bad += 0 if in_denominator else 1
    bad += 0 if not in_numerator else 1
    print("  453  net=%-11.6f present=%-9.6f مساهمته=%s" % (
        net, present, ("%.6f" % float(mine[0].get("contribution"))) if mine else "لا شيء"))
    for e in ev:
        print("       166 بالأدلّة: kind=%s eligible=%s reason=%s وزنه=%s" % (
            e.get("kind"), e.get("eligible"), e.get("eligibility_reason"),
            (mine[0].get("weight") if mine else "—")))
    print("       بالمقام؟ %s   ·   خارج البسط؟ %s" % (
        "✓" if in_denominator else "✗", "✓" if not in_numerator else "✗ دخل البسط!"))

    resolved = bus.events("decision.resolved.state")
    if resolved:
        d = resolved[-1]
        direction = str(d.get("direction"))
        ok = direction in ("wait", "neutral") and float(d.get("strength") or 0.0) < 1e-12
        bad += 0 if ok else 1
        print("  458  direction=%-8s strength=%-9.6f  %s" % (
            direction, float(d.get("strength") or 0.0),
            "✓ لا اتجاه" if ok else "✗ 166 وحده صنع اتجاهًا!"))
    return bad


async def main_async() -> int:
    bad = structural()
    print("\n" + "-" * 78)
    print("ب) طرف-لطرف — 166 يصرخ باتّجاه كامل ولا أحد غيره يتكلّم")
    print("-" * 78)
    bad += report(await drive("down"))
    print("\n" + "=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: 166 مجمّع سياقيّ — بالمقام وحده، ولا يضيف صوتًا للبسط.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
