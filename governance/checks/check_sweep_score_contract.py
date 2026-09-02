"""Contract guard for problem 26 — one line in 254 was lying three times.

Owner's ruling (settled 2026-08-15, inside the frozen closure package):

    score is a DETECTION FLAG: 100 when a real sweep is detected, 0 when the
    measurement ran and found none.  Deficiency is not zero: with no high, no
    low, or no pools at all there is NOTHING to measure, so the atom must say
    `insufficient_data` and publish NO score.  Confidence must be MEASURED --
    wick penetration depth relative to the candle range -- not fabricated.

What was measured before this guard existed, at 254/atom.py:157:

    "score": 0, "confidence": 1.0 if swept else 0.0,

    1) score is 0 even on a real sweep  -> the atom cannot say "I detected".
    2) confidence 1.0 is flat and carries no information.
    3) there is no deficiency state at all: with no pools, no high or no low it
       still published `status: ok` with a zero -- a fake denial presented as a
       measurement.

  أ) بنيويّ  -- the detection constant exists, the hardcoded zero score and the
              fabricated confidence are gone, the deficiency status exists, the
              version moved in BOTH code and card, and the shared vocabulary
              of the family was not touched.
  ب) طرف-لطرف -- the REAL atom, driven through its REAL handlers: a deep sweep,
              a shallow sweep, a sell-side sweep, a measured no-sweep, and the
              three deficiency shapes.  A lying line cannot pass this.
  ج) الجار 255 -- migrated 2026-08-18, owner's explicit ruling on the day's
              "إغلاق منظومة التحليل" closure paper (§12). The neighbour is no
              longer required to sit frozen at 1.1.0 forever: that freeze only
              ever protected against 255 being touched *by accident* while
              fixing problem 26 in 254. Today's paper closes a NEW,
              owner-authorized contract for 255 itself (real measured
              confidence = gap_size/window_range, the dead "score" field
              removed entirely) -- so keeping the old freeze would now mean
              failing this guard forever for a change the owner ordered.
              Per his instruction: do not weaken the guard, do not delete it,
              do not silence it to match the code -- migrate the proof to the
              new closed contract, keep the protective intent. So this
              section no longer asserts "255 unchanged since problem 26"; it
              asserts "255 implements ITS OWN closed contract": no "score"
              key anywhere, confidence is a real measured ratio (two
              different gap sizes in the same window MUST produce two
              different confidence values, not a flat constant), and the
              deficiency path still tells the truth. A neighbour that
              regresses either fact still fails this guard exactly as before.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
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
A254 = "254_كنس_السيولة"
A255 = "255_الفجوة_FVG"
OLD_VERSION = "1.1.0"
OLD_255 = "1.1.0"
CLOSED_255 = "1.2.0"
EVENT_OUT = "liquidity.sweep.state"
EVENT_OUT_255 = "liquidity.fvg.state"
STATUS_INSUFFICIENT = "insufficient_data"
CONVICTION = 100
SYM, TF = "BTCUSD", "60s"

# الثقة = عمق اختراق الفتيل ÷ مدى الشمعة. الرقمان من حكمه: عميق 0.5 · سطحيّ 0.0909
DEEP, SHALLOW = 0.5, 1.0 / 11.0
TOLERANCE = 1e-9


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.log.append((name, payload))

    def last(self, name):
        rows = [p for n, p in self.log if n == name]
        return rows[-1] if rows else None


def load():
    directory = ATOMS / A254
    spec = importlib.util.spec_from_file_location("_c26_254", directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def card(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


async def run(module, pools, high, low):
    """يشغّل الذرّة الحقيقيّة عبر مُعالِجاتها الحقيقيّة — لا محاكاة لمنطقها."""
    bus = Bus()
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=254, config={}, logger=_Logger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    await atom.start()
    for side, price in pools:
        payload = {"symbol": SYM, "signal": side,
                   "metadata": {"price": price, "timeframe": TF}}
        if side == "buyside":
            await atom._on_buyside(payload)
        else:
            await atom._on_sellside(payload)
    await atom._on_candle({"symbol": SYM, "timeframe": TF, "high": high, "low": low,
                           "period_start": "T0"})
    return bus.last(EVENT_OUT)


def load255():
    directory = ATOMS / A255
    spec = importlib.util.spec_from_file_location("_c26_255", directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


async def run255(module, candles):
    """يشغّل ٢٥٥ الحقيقيّة عبر _on_candle الحقيقيّة -- لا محاكاة لحساب الفجوة."""
    bus = Bus()
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=255, config={}, logger=_Logger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    await atom.start()
    for high, low in candles:
        await atom._on_candle({"symbol": SYM, "timeframe": TF, "high": high, "low": low,
                               "period_start": "T0"})
    return bus.last(EVENT_OUT_255)


def close(value, target) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and abs(float(value) - target) < 1e-4


def structural() -> int:
    print("=" * 86)
    print("أ) الحواجز البنيويّة — الكشف علم، والعجز يُعلَن، والثقة تُقاس")
    print("=" * 86)
    bad = 0
    src = (ATOMS / A254 / "atom.py").read_text(encoding="utf-8")
    code_version = re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', src, re.M)
    code_version = code_version.group(1) if code_version else ""
    card_version = str(card(A254).get("version"))

    checks = (
        ("ثابت القناعة 100 موجود", re.search(r"^_?CONVICTION\s*=\s*100\s*$", src, re.M) is not None),
        ("لا درجة صفر مثبَّتة بالنشر", re.search(r'"score"\s*:\s*0\s*,', src) is None),
        ("لا ثقة مصنوعة 1.0/0.0", re.search(r'"confidence"\s*:\s*1\.0 if', src) is None),
        ("حالة العجز معلَنة", STATUS_INSUFFICIENT in src),
        ("العجز بلا درجة إطلاقًا", re.search(r'"score"\s*:\s*None', src) is not None),
        ("النسخة تحرّكت عن %s" % OLD_VERSION, code_version != OLD_VERSION and code_version != ""),
        ("الكود والبطاقة نسخة واحدة", code_version == card_version),
        ("لا حرف عربيّ داخل الكود", not re.search(r"[؀-ۿ]", src)),
        ("الملفّ تحت ٣٠٠ سطر", len(src.splitlines()) <= 300),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))

    src255 = (ATOMS / A255 / "atom.py").read_text(encoding="utf-8")
    ok = STATUS_INSUFFICIENT in src255
    bad += 0 if ok else 1
    print("      %-38s %s" % ("القاموس من العائلة لا مخترَع", "✓" if ok else "✗"))

    print("\n" + "-" * 86)
    print("ج) الجار 255 -- رُحِّل لعقده المقفول اليوم (§12), لا يُحسَب مُلغىً")
    print("-" * 86)
    card255_version = str(card(A255).get("version"))
    checks255 = (
        ("255 غادر النسخة القديمة %s" % OLD_255, card255_version != OLD_255),
        ("255 عند عقده المقفول %s" % CLOSED_255, card255_version == CLOSED_255),
        ('255 بلا حقل "score" إطلاقًا', re.search(r'"score"', src255) is None),
        ("255 يقيس الثقة (gap_size/window_range) لا يخترعها",
         "gap_size" in src255 and "window_range" in src255),
    )
    for label, ok in checks255:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))
    return bad


async def main_async() -> int:
    bad = structural()
    module = load()

    print("\n" + "=" * 86)
    print("ب) طرف-لطرف على الذرّة الحقيقيّة — الكشف والعمق والعجز")
    print("=" * 86)

    # كنس شرائيّ عميق: البركة 100 · الشمعة 95→105 ⇒ اختراق 5 من مدى 10 = 0.5
    deep = await run(module, [("buyside", 100.0)], 105.0, 95.0)
    # كنس شرائيّ سطحيّ: البركة 100 · الشمعة 90→101 ⇒ اختراق 1 من مدى 11 = 0.0909
    shallow = await run(module, [("buyside", 100.0)], 101.0, 90.0)
    # كنس بيعيّ عميق: البركة 100 · الشمعة 95→105 ⇒ اختراق 5 من مدى 10 = 0.5
    sell = await run(module, [("sellside", 100.0)], 105.0, 95.0)
    # قياس تمّ ولم يجد كنسًا: بركة بعيدة ⇒ درجة صفر صادقة لا عجز
    none = await run(module, [("buyside", 200.0)], 105.0, 95.0)

    rows = (
        ("كنس شرائيّ عميق", deep, CONVICTION, DEEP, "ok", "sweep"),
        ("كنس شرائيّ سطحيّ", shallow, CONVICTION, SHALLOW, "ok", "sweep"),
        ("كنس بيعيّ عميق", sell, CONVICTION, DEEP, "ok", "sweep"),
        ("قياس بلا كنس", none, 0, 0.0, "ok", "none"),
    )
    for label, state, score, confidence, status, signal in rows:
        if not state:
            print("      %-20s ✗ لم يُنشَر شيء" % label)
            bad += 1
            continue
        ok_score = state.get("score") == score
        ok_conf = close(state.get("confidence"), confidence)
        ok_rest = state.get("status") == status and state.get("signal") == signal
        ok = ok_score and ok_conf and ok_rest
        bad += 0 if ok else 1
        print("      %-20s درجة=%-6s ثقة=%-10s حالة=%-18s %s"
              % (label, state.get("score"), state.get("confidence"),
                 state.get("status"), "✓" if ok else "✗"))

    print("\n  والعجز يُعلَن ولا يُقدَّم صفرًا — ثلاث صور:")
    shapes = (
        ("بلا بِرَك", await run(module, [], 105.0, 95.0)),
        ("بلا قمّة", await run(module, [("buyside", 100.0)], None, 95.0)),
        ("بلا قاع", await run(module, [("buyside", 100.0)], 105.0, None)),
    )
    for label, state in shapes:
        if not state:
            print("      %-12s ✗ لم يُنشَر شيء" % label)
            bad += 1
            continue
        ok = state.get("status") == STATUS_INSUFFICIENT and state.get("score") is None
        bad += 0 if ok else 1
        print("      %-12s ⇒ حالة=%-18s درجة=%-6s %s"
              % (label, state.get("status"), state.get("score"), "✓" if ok else "✗"))

    print("\n  والعمق يفرّق فعلًا — رقمان مختلفان لا رقم واحد مسطَّح:")
    distinct = bool(deep and shallow) and deep.get("confidence") != shallow.get("confidence")
    bad += 0 if distinct else 1
    print("      %-38s %s" % ("ثقة العميق ≠ ثقة السطحيّ",
                              "✓" if distinct else "✗ الثقة ما زالت مصنوعة"))

    if deep and none:
        same = set(deep) == set(none)
        bad += 0 if same else 1
        print("      %-38s %s" % ("أسماء الحقول لم تتحرّك", "✓" if same else "✗"))

    print("\n" + "=" * 86)
    print("ج) طرف-لطرف على ٢٥٥ الحقيقيّة -- عقده المقفول اليوم لا نسخته القديمة")
    print("=" * 86)
    module255 = load255()
    # فجوة عريضة: أوّل قمّة=100 · ثالث قاع=110 ⇒ حجم=10 من مدى نافذة=25 ⇒ 0.4
    wide = await run255(module255, [(100.0, 90.0), (105.0, 101.0), (115.0, 110.0)])
    # فجوة ضيّقة: أوّل قمّة=100 · ثالث قاع=101 ⇒ حجم=1 من مدى نافذة=12 ⇒ 0.0833
    narrow = await run255(module255, [(100.0, 90.0), (101.0, 99.0), (102.0, 101.0)])
    # عجز حقيقي: نافذة لم تمتلئ بعد (شمعتان لا ثلاث)
    scarce = await run255(module255, [(100.0, 90.0), (105.0, 101.0)])

    if wide and narrow:
        distinct255 = close(wide.get("confidence"), 0.4) and close(narrow.get("confidence"), 1.0 / 12.0) \
            and wide.get("confidence") != narrow.get("confidence")
        bad += 0 if distinct255 else 1
        print("      %-38s واسعة=%-8s ضيّقة=%-8s %s"
              % ("ثقة الفجوة الواسعة ≠ الضيّقة", wide.get("confidence"), narrow.get("confidence"),
                 "✓" if distinct255 else "✗ الثقة ما زالت مصنوعة"))
        no_score = "score" not in wide and "score" not in narrow
        bad += 0 if no_score else 1
        print("      %-38s %s" % ('لا حقل "score" في المخرَج الحقيقي', "✓" if no_score else "✗"))
    else:
        bad += 2
        print("      ✗ لم تُنشَر فجوة (واسعة أو ضيّقة) -- تعذّر إثبات القياس")

    if scarce:
        ok_scarce = scarce.get("status") == STATUS_INSUFFICIENT and "score" not in scarce
        bad += 0 if ok_scarce else 1
        print("      %-38s حالة=%-18s %s"
              % ("نافذة ناقصة ⇒ عجز صادق بلا score", scarce.get("status"),
                 "✓" if ok_scarce else "✗"))
    else:
        bad += 1
        print("      ✗ لم يُنشَر شيء عند النافذة الناقصة")

    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الكنس يقول 100/0 · والعجز insufficient_data بلا درجة · والثقة مقيسة.")
        print("      والجار 255 على عقده المقفول اليوم: بلا score · ثقة مقيسة لا مسطَّحة.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
