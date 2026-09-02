"""Contract guard for problem 21 — a divergence verdict may only be formed from
two samples taken at effectively the same instant.

Owner's ruling 2026-08-14, verbatim:

    "I adopt alignment_window_s = 0.15.  The number is justified by the
     measurement you presented: up to 0.15s p95 <= 3 points, far from the 50
     threshold; moving into 0.15-0.20s p95 jumps to 103 points, and that is the
     first clear jump in damage.  So 0.15s is not a chosen tolerance; it is the
     measured dividing line between a safe alignment region and a region where
     the deviation is contaminated by the sampling gap."
    "Scope: 582 only.  Inside the window deviation is computed as usual.
     Outside it the status is UNALIGNED, it must not turn into DIVERGED and it
     must not block.  The 50 threshold is not touched now.  578 is not touched
     in this round."
    "I want the verdict AT the boundary to be explicit and unambiguous: is 0.15
     inside the window or outside?  Test the value itself, do not rely on
     >/>= by intuition."

What was measured live before this guard existed (2035 samples, 420s, BTCUSD):

    slice        n     median   p95      max      over 50
    0.00-0.05   10     0.0      3.0      3.0      0%
    0.05-0.10   10     0.0      0.0      0.0      0%
    0.10-0.15   15     0.0      1.0      3.0      0%
    0.15-0.20   15     0.0    103.0    200.0     13%   <- the cliff
    1.00-1.50  503    150.0   1309.0   2368.0     67%

  and the status split was SYNCED 50.5% / DIVERGED 49.2% / STALE 0.4%, so the
  blocking was never staleness: it was the artefact.

ONE INTERPRETATION I MADE INSIDE HIS RULING, stated openly: STALE is decided
BEFORE the window.  A gap beyond max_age_s means a feed died, which is a real
protective signal and has nothing to do with sampling; folding it into
UNALIGNED would silently delete a protection he never asked to remove -- and
his own requirement 7 says the blocking path must not change.

  ١ بنيويّ   -- the window is declared, UNALIGNED exists, 50 and 5 untouched,
                578 frozen by version and by its blocking line.
  ٢ داخل     -- real divergence above the threshold still says DIVERGED.
  ٣ خارج     -- a huge deviation with a wide gap says UNALIGNED, never DIVERGED.
  ٤ الحدّ     -- Δt = 0.15 exactly, judged explicitly.
  ٥ الزمن    -- a missing timestamp never becomes DIVERGED.
  ٦ التقادم  -- a dead feed still says STALE and still blocks.

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

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
A582 = "582_انحراف_المرجع"
A578 = "578_منفذ_التحوط"
SYM = "BTCUSD"
POINT = 0.01          # measured from the bridge: 50 points = $0.50
WINDOW = 0.15         # his ruling
THRESHOLD = 50.0
BASE = 63000.0

VERSION_582 = "1.3.0"
# 578 must not move a letter in this round.
FROZEN_578 = "3.0.0"
BLOCK_LINE = ('return self._quality.get(_key(account, symbol), {}).get("status") == "BLOCKED" '
              'or self._divergence.get(symbol, {}).get("status") in ("DIVERGED", "STALE")')


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

    def last(self, name):
        rows = [p for n, p in self.log if n == name]
        return rows[-1] if rows else None


def load(folder: str, tag: str):
    directory = ATOMS / folder
    spec = importlib.util.spec_from_file_location(tag, directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[tag] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def manifest(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


async def verdict(gap, deviation_points, ct_stamp=0.0, drop_stamp=""):
    """One real 582, one cTrader sample and one MT5 sample, and its verdict.

    `gap` is the timestamp distance; `deviation_points` is the REAL price
    difference expressed in points, so the two axes are set independently.

    The first stamp is 0.0 ON PURPOSE.  Building the boundary as 1000.0 + 0.15
    yields 0.14999999999997726, so the exact edge was never actually tested and
    a `>` / `>=` flip slipped past the guard.  With 0.0 the atom computes
    abs(gap - 0.0) == gap, bit for bit, and the edge is the real edge.
    """
    module = load(A582, "_cref_582")
    bus = Bus()
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=582, config=dict(manifest(A582)["config"]),
                                      logger=_Logger(), publish=bus.publish,
                                      subscribe=bus.subscribe))
    await atom.start()
    await atom._on_specs({"symbols": [{"symbol": SYM, "point": POINT}]})
    mt_stamp = None if ct_stamp is None else ct_stamp + gap
    ct = {"symbol": SYM, "price": BASE, "timestamp": ct_stamp}
    mt = {"symbol": SYM, "price": BASE + deviation_points * POINT, "timestamp": mt_stamp}
    if drop_stamp == "ct":
        ct.pop("timestamp")
    if drop_stamp == "mt":
        mt.pop("timestamp")
    await atom._on_ct(ct)
    await atom._on_mt(mt)
    return bus.last("execution.reference_divergence.state") or {}


def structural() -> int:
    print("=" * 90)
    print("١· الحواجز البنيويّة — النافذة معلَنة، والعتبة و578 لم تُمَسّا")
    print("=" * 90)
    bad = 0
    src = (ATOMS / A582 / "atom.py").read_text(encoding="utf-8")
    card = manifest(A582)
    config = card.get("config") or {}
    schema = ((card.get("config_schema") or {}).get("properties") or {})

    checks = (
        ("النافذة معلَنة بالبطاقة", "alignment_window_s" in config),
        ("وقيمتها %.2f" % WINDOW, config.get("alignment_window_s") == WINDOW),
        ("ومعرَّفة بالمخطّط", "alignment_window_s" in schema),
        ("وإلزاميّة", "alignment_window_s" in (card.get("config_schema") or {}).get("required", [])),
        ("حالة UNALIGNED موجودة بالكود", "UNALIGNED" in src),
        ("العتبة 50 لم تُمَسّ", config.get("max_deviation_points") == 50),
        ("مهلة التقادم 5 لم تُمَسّ", config.get("max_age_s") == 5),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-32s %s" % (label, "✓" if ok else "✗"))

    got = str(manifest(A582).get("version"))
    ok = got == VERSION_582
    bad += 0 if ok else 1
    print("      %-32s %-8s %s" % ("نسخة 582 المعلَنة", got, "✓" if ok else "✗"))
    got = str(manifest(A578).get("version"))
    ok = got == FROZEN_578
    bad += 0 if ok else 1
    print("      %-32s %-8s %s" % ("578 لم تُمَسّ", got, "✓" if ok else "✗ تغيّرت!"))
    ok = BLOCK_LINE in (ATOMS / A578 / "atom.py").read_text(encoding="utf-8")
    bad += 0 if ok else 1
    print("      %-32s %s" % ("مسار الحجب حرفيًّا كما هو", "✓" if ok else "✗ تغيّر!"))
    return bad


async def main_async() -> int:
    bad = structural()

    print("\n" + "=" * 90)
    print("٢·٣·٤· الحكم حسب الفجوة — والانحراف الحقيقيّ ثابت فوق العتبة (200 نقطة)")
    print("=" * 90)
    print("      %-34s %-12s %-14s %s" % ("الحالة", "Δt", "الانحراف", "الحكم"))
    cases = (
        ("داخل النافذة · انحراف حقيقيّ كبير", 0.05, 200.0, "DIVERGED"),
        ("داخل النافذة · انحراف صغير", 0.05, 3.0, "SYNCED"),
        # الحدّ يُختبر بقيمته الحقيقيّة بت ببت، لا بجمع يفقد الدقّة.
        ("قبل الحدّ بشعرة", 0.1499999, 200.0, "DIVERGED"),
        ("الحدّ بالضبط Δt = 0.15 ⇒ داخل", WINDOW, 200.0, "DIVERGED"),
        ("بعد الحدّ بشعرة", 0.1500001, 200.0, "UNALIGNED"),
        ("فجوة نموذجيّة مقيسة (الوسيط)", 1.04, 200.0, "UNALIGNED"),
        ("فجوة واسعة", 3.0, 900.0, "UNALIGNED"),
    )
    for label, gap, deviation, expected in cases:
        state = await verdict(gap, deviation)
        got = str(state.get("status"))
        measured = state.get("timestamp_gap_s")
        # الفجوة التي قاستها الذرّة يجب أن تكون هي التي قصدتُها بالضبط،
        # وإلّا فالحدّ لم يُختبر أصلًا.
        exact = measured is not None and float(measured) == gap
        ok = got == expected and exact
        bad += 0 if ok else 1
        print("      %-34s %-12.7f %-14.1f %-11s %s%s"
              % (label, gap, deviation, got, "✓" if ok else "✗ المتوقّع %s" % expected,
                 "" if exact else "  ⚠ الفجوة المقيسة %r ≠ المقصودة" % measured))

    print("\n" + "=" * 90)
    print("٥·٦· الزمن الغائب لا يكذب، والمغذّي الميّت ما زال يحجب")
    print("=" * 90)
    extra = (
        ("طابع سي‑تريدر غائب", 0.0, 900.0, "ct", "UNALIGNED"),
        ("طابع MT5 غائب", 0.0, 900.0, "mt", "UNALIGNED"),
        ("مغذّي ميّت Δt = 10s", 10.0, 900.0, "", "STALE"),
        ("مغذّي ميّت وانحراف صفر", 10.0, 0.0, "", "STALE"),
    )
    for label, gap, deviation, drop, expected in extra:
        state = await verdict(gap, deviation, drop_stamp=drop)
        got = str(state.get("status"))
        ok = got == expected
        bad += 0 if ok else 1
        print("      %-34s %-14s %-11s %s"
              % (label, "Δt=%.1f" % gap, got, "✓" if ok else "✗ المتوقّع %s" % expected))

    print("\n      وأيّ حالة خارج النافذة لا تدخل مسار الحجب:")
    blocking = ("DIVERGED", "STALE")
    outside = await verdict(1.04, 900.0)
    ok = str(outside.get("status")) not in blocking
    bad += 0 if ok else 1
    print("      %-34s %-11s %s" % ("UNALIGNED ليست من مجموعة الحجب",
                                     outside.get("status"), "✓" if ok else "✗"))

    print("\n" + "=" * 90)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: لا حكم انحراف إلّا من عيّنتين متزامنتين، والحجب لم يتغيّر.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
