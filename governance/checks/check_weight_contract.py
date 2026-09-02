"""Weight-contract proof (owner's test contract, 2026-08-13).

An INDEPENDENT ruler is written here from the owner's table alone -- it imports
nothing from atom 453. The real 453 is then driven with a synthetic evidence
list and every number is compared:

    directional sources speaking   ->   required S
    0                                   0
    1                                   ~0.1125   and FORCED NEUTRAL (WAIT)
    2                                   ~0.225
    4                                   ~0.45
    6                                   ~0.675
    8                                   ~0.90     (full consensus, by THIS
                                                   contract -- not 1.00)

Then the same proof is repeated with the list actually declared in the
manifest, so the live numbers are never guessed. Exit 1 on any divergence.
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

import yaml  # noqa: E402

from core.contracts.atom import AtomContext  # noqa: E402

W_DIR = 1.0
W_CTX = 0.0556
MIN_PARTICIPATION = 0.20
ATOM_DIR = ATOM_ROOT / "453_حساب_الدرجة"


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class _Bus:
    def __init__(self):
        self.published = []

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.published.append((name, payload))


def _load():
    spec = importlib.util.spec_from_file_location("_w453", ATOM_DIR / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_w453"] = module
    spec.loader.exec_module(module)
    return module


def evidence(directional: list[str], context: list[str], speaking: int,
             side: str = "sell") -> list[dict]:
    """Every source PRESENT; only `speaking` directional ones carry a side."""
    rows = []
    for i, name in enumerate(directional):
        talks = i < speaking
        rows.append({"source": name, "label": name,
                     "kind": "directional" if talks else "context",
                     "direction": side if talks else "unknown",
                     "score": 100.0 if talks else 0.0,
                     "confidence": 1.0 if talks else 0.0,
                     "quality_factor": 1.0,
                     "eligible": talks,
                     "eligibility_reason": "ELIGIBLE" if talks else "NO_DIRECTION"})
    for name in context:
        rows.append({"source": name, "label": name, "kind": "context",
                     "direction": "unknown", "score": 60.0, "confidence": 1.0,
                     "quality_factor": 1.0, "eligible": False,
                     "eligibility_reason": "CONTEXT_ONLY"})
    return rows


class Ruler:
    """The owner's table, executed. Nothing here comes from 453."""

    def __init__(self, n_dir: int, n_ctx: int):
        self.present = n_dir * W_DIR + n_ctx * W_CTX

    def expect(self, speaking: int) -> dict:
        net = speaking * W_DIR                      # each speaker at full score/conf/quality
        strength = abs(net) / self.present if self.present else 0.0
        participation = (speaking * W_DIR) / self.present if self.present else 0.0
        direction = "sell" if speaking else "neutral"
        if direction != "neutral" and participation < MIN_PARTICIPATION:
            direction = "neutral"
        return {"strength": round(strength, 6), "participation": round(participation, 6),
                "direction": direction, "present": round(self.present, 6)}


async def drive(module, directional, context, speaking_counts):
    out = []
    for n in speaking_counts:
        bus = _Bus()
        atom = module.Atom()
        config = {"directional_weight": W_DIR, "context_weight": W_CTX,
                  "min_participation": MIN_PARTICIPATION,
                  "directional_sources": list(directional)}
        await atom.initialize(AtomContext(atom_id=453, config=config, logger=_Logger(),
                                          publish=bus.publish, subscribe=bus.subscribe))
        await atom.start()
        await atom._on_evaluated({"symbol": "BTCUSD", "cycle_id": "c", "timeframe": "60s",
                                  "evidence": evidence(directional, context, n)})
        rows = [p for n_, p in bus.published if n_ == module.EVENT_OUT]
        out.append(rows[-1] if rows else None)
    return out


def run_block(title, module, directional, context, counts) -> int:
    n_dir, n_ctx = len(directional), len(context)
    ruler = Ruler(n_dir, n_ctx)
    print("\n" + "=" * 78)
    print("%s   —   اتّجاهيّ=%d × %.4f   ·   سياقيّ=%d × %.4f   ⇒   المقام = %.4f"
          % (title, n_dir, W_DIR, n_ctx, W_CTX, ruler.present))
    print("=" * 78)
    rows = asyncio.run(drive(module, directional, context, counts))
    print("%-12s %-12s %-12s %-12s %-12s %s" % (
        "من ينطق", "S المطلوب", "S المقيس", "المشاركة", "الاتجاه", "الحكم"))
    failures = 0
    for n, got in zip(counts, rows):
        want = ruler.expect(n)
        if got is None:
            print("%-12d المحرّك لم ينشر شيئًا" % n)
            failures += 1
            continue
        ok = (abs(float(got.get("strength") or 0) - want["strength"]) < 1e-6
              and abs(float(got.get("participation") or 0) - want["participation"]) < 1e-6
              and str(got.get("direction")) == want["direction"]
              and abs(float(got.get("weight_present") or 0) - want["present"]) < 1e-6)
        failures += 0 if ok else 1
        print("%-12d %-12.6f %-12.6f %-12.6f %-12s %s" % (
            n, want["strength"], float(got.get("strength") or 0),
            float(got.get("participation") or 0), got.get("direction"),
            "✓" if ok else "<-- اختلاف: %s" % want))
    return failures


def main() -> int:
    module = _load()
    failures = 0

    # ── 1) the owner's own table: 8 directional + 16 context ─────────────
    d8 = ["D%d" % i for i in range(1, 9)]
    c16 = ["C%d" % i for i in range(1, 17)]
    failures += run_block("جدول المالك (٨ اتّجاهيّة + ١٦ سياقيّة)", module, d8, c16,
                          [0, 1, 2, 4, 6, 8])

    # the guard he was chasing, stated on its own
    ruler = Ruler(8, 16)
    one = ruler.expect(1)
    guard_ok = one["direction"] == "neutral" and one["strength"] < MIN_PARTICIPATION
    print("\nحارس الصوت الواحد: S=%.4f · مشاركة=%.4f ⇒ %s  %s" % (
        one["strength"], one["participation"], one["direction"],
        "✓ يُجبَر محايدًا" if guard_ok else "<-- يعبر!"))
    failures += 0 if guard_ok else 1

    # ── 2) the list actually declared in the manifest ────────────────────
    manifest = yaml.safe_load((ATOM_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    declared = [str(s) for s in manifest["config"]["directional_sources"]]
    # the context sources seen live on this account
    # Every other source seen live on this account. 401 and 400:signals_merged
    # sit here by the owner's ruling on problems 49/51: they are derivatives of
    # the five strategies, not independent votes.
    live_context = ["350:trend_model", "350:reversal_model", "350:breakout_model",
                    "350:pullback_model", "350:momentum_model", "350:range_model",
                    "350:models_merged", "350:hurst", "350:confidence_aggregator", "359",
                    "166", "401", "400:entry_rules", "400:signals_merged", "400:exit_rules",
                    "400:news_strategy", "400:range_strategy", "400:reversal_strategy",
                    "400:session_strategy"]
    failures += run_block("القائمة المعلَنة بالمنيفست + الحاضرون حيًّا", module,
                          declared, live_context, list(range(0, len(declared) + 1)))

    print("\nالمعلَن اتّجاهيًّا: %s" % ", ".join(declared))

    # ── 3) the ceiling is carried honestly: no rounding, no clamp ─────────
    print("\n" + "=" * 78)
    print("السقف الحقيقيّ — يُحمل كما هو، بلا تقريب ولا حيلة")
    print("=" * 78)
    ruler2 = Ruler(len(declared), len(live_context))
    top = ruler2.expect(len(declared))
    rows = asyncio.run(drive(module, declared, live_context, [len(declared)]))
    got = rows[0]
    measured = float(got.get("strength") or 0.0)
    print("  إجماع كامل (%d من %d) ⇒ S = %.6f" % (len(declared), len(declared), measured))
    exact = abs(measured - top["strength"]) < 1e-9
    not_rounded = abs(measured - 0.90) > 1e-9 if top["strength"] < 0.9 else True
    reachable = [b for b in (0.20, 0.40, 0.60, 0.90) if b <= measured + 1e-12]
    print("  النطاقات القابلة للوصول: %s" % (reachable or "لا شيء"))
    print("  النطاق الأعلى (≥0.90): %s" % ("قابل للوصول" if measured + 1e-12 >= 0.90
                                            else "🔴 غير قابل للوصول — والعقد يحمل هذه الحقيقة"))
    print("  بلا تقريب إلى 0.90؟ %s   ·   القيمة كما حسبتها الذرّة حرفًا؟ %s" % (
        "نعم ✓" if not_rounded else "لا ✗", "نعم ✓" if exact else "لا ✗"))
    if not (exact and not_rounded):
        failures += 1

    print("\nالاختلافات = %d" % failures)
    if failures == 0:
        print("سليم: الحاسبة المستقلّة والذرّة 453 تقولان حقيقة واحدة.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
