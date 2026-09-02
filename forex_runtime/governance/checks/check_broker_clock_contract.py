"""Contract guard for problems 78 and 10 — a timezone worn as an epoch.

Measured live (2026-08-15) on a captured `feed.mt5.tick` payload:

    exchange_timestamp = 1786802378.973
    received_at        = 1786791577.281
    difference         = 10,801.7 s  =  3 hours + 1.7 s

618 divided the EA's `tick_ms` by 1000 and published it as `exchange_timestamp`
-- but that number is the BROKER SERVER clock (ICMarketsSC-Demo = UTC+3), not a
UTC epoch. cTrader's stamp, next to it, matched its own receipt time exactly.
So every cross-feed time comparison was measuring a timezone, not market lag --
and items 21 and 68 were both settled on numbers produced under that error.

Owner's ruling: (a) fix the 618 stamp, then RE-MEASURE 21. And paper 23 is
explicit that deviation is MEASURED, never silently corrected -- so the offset
is declared, not subtracted behind anyone's back.

  أ) الختم    -- the raw broker clock keeps its own name, the offset is
               published as a measured number, and `exchange_timestamp` is only
               offered when it actually agrees with receipt time.
  ب) الزمن ١٠ -- 575 obeys the official clock like everything else, and every
               remaining sub-second reader DECLARES its exception by name.
  ج) طرف-لطرف -- the REAL 618 fed a UTC+3 row: the offset comes out at 10800,
               and no UTC-looking stamp is handed downstream.

Exit 1 on any divergence.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
A618 = "618_مصدر_جسر_MT5"
A575 = "575_مرسل_الإدارة"
CLOCK_ALLOWED = {"3", "806"}
DECLARED = "SUBSECOND_CLOCK_REASON"


def card(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


def code(folder: str) -> str:
    src = (ATOMS / folder / "atom.py").read_text(encoding="utf-8")
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def clock_readers() -> dict:
    """Every atom that reads the wall clock, and whether it declares why."""
    out = {}
    for path in sorted(ATOMS.glob("*/atom.py")):
        atom_id = path.parent.name.split("_")[0].lstrip("0") or "0"
        src = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                        if not l.lstrip().startswith("#"))
        # A wall-clock read hides easily: `__import__('time').time()` slipped
        # straight past a plain `time.time()` search and left the barrier hollow.
        if re.search(r"time\s*\.\s*time\s*\(|__import__|datetime\s*\.\s*(now|utcnow)", src):
            out[atom_id] = DECLARED in src
    return out


def structural() -> int:
    print("=" * 86)
    print("أ) الختم — ساعة الوسيط باسمها، والانحراف مقيس معلَن")
    print("=" * 86)
    bad = 0
    src = code(A618)
    checks = (
        ("الختم الخام باسمه الصريح", '"broker_timestamp"' in src),
        ("والانحراف يُنشَر مقيسًا", '"broker_clock_offset_s"' in src),
        ("ولا ختم UTC إلّا بموافقته الاستلام", "_CLOCK_TOLERANCE_S" in src),
        ("ولا طرح صامت للفارق", "- offset" not in src and "-offset" not in src),
        # The wiring itself, not only the helper: the published field must carry
        # the GATED value. Testing the function alone left this break standing.
        ("والمنشور هو الختم المشروط",
         '"exchange_timestamp": exchange_stamp,' in src),
        ("ولا الخام مكانه", '"exchange_timestamp": broker_stamp' not in src),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))

    print("\n" + "=" * 86)
    print("ب) البند ١٠ — الساعة الرسميّة قاعدة، والاستثناء يُعلن باسمه")
    print("=" * 86)
    readers = clock_readers()
    undeclared = sorted(a for a, declared in readers.items()
                        if a not in CLOCK_ALLOWED and not declared)
    ok = not undeclared
    bad += 0 if ok else 1
    print("      %-38s %s" % ("قارئو الساعة بلا إعلان",
                              "✓ صفر" if ok else "✗ " + " · ".join(undeclared)))
    ok = A575.split("_")[0].lstrip("0") not in readers
    bad += 0 if ok else 1
    print("      %-38s %s" % ("575 يخضع للساعة الرسميّة", "✓" if ok else "✗ ما زال يقرأ الساعة"))
    ok = "official_time" in code(A575)
    bad += 0 if ok else 1
    print("      %-38s %s" % ("ويستعملها فعلًا", "✓" if ok else "✗"))
    print("      %-38s %s" % ("قارئو الساعة المرصودون",
                              " · ".join("%s%s" % (a, "" if d else "(بلا إعلان)")
                                         for a, d in sorted(readers.items())) or "لا أحد"))

    for folder in (A618, A575):
        atom_id = folder.split("_")[0]
        version = re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', code(folder), re.M)
        version = version.group(1) if version else ""
        ok = version != "" and version == str(card(folder).get("version"))
        bad += 0 if ok else 1
        print("      %-38s كود=%-8s بطاقة=%-8s %s"
              % ("%s نسخة واحدة" % atom_id, version, card(folder).get("version"),
                 "✓" if ok else "✗"))
    return bad


def behavioural() -> int:
    print("\n" + "=" * 86)
    print("ج) حسابيّ — صفّ بساعة UTC+3 يعطي انحرافًا ١٠٨٠٠ بلا ختم كاذب")
    print("=" * 86)
    bad = 0
    src = (ATOMS / A618 / "atom.py").read_text(encoding="utf-8")
    namespace: dict = {}
    block = re.search(r"def broker_clock\(.*?\n(?=\n\S|\nclass |\ndef )", src, re.S)
    if block is None:
        print("      ✗ لا دالّة broker_clock مستقلّة تُقاس")
        return 1
    # The tolerance is read from the atom too -- hardcoding it here would let a
    # widened threshold slip past the barrier that is supposed to catch it.
    tolerance = re.search(r"^_CLOCK_TOLERANCE_S\s*=\s*([0-9.]+)", src, re.M)
    if tolerance is None:
        print("      ✗ لا عتبة معلَنة تُقاس")
        return 1
    exec(compile("_CLOCK_TOLERANCE_S = %s\n" % tolerance.group(1) + block.group(0),
                 "<618>", "exec"), namespace)                          # noqa: S102
    broker_clock = namespace["broker_clock"]

    received = 1786791577.281
    rows = (("ساعة الوسيط UTC+3", received + 10800.0, 10800.0, None),
            ("وساعة متوافقة", received + 0.4, 0.4, received + 0.4))
    for label, stamp, want_offset, want_exchange in rows:
        offset, exchange = broker_clock(stamp, received)
        ok = abs(offset - want_offset) < 0.01 and exchange == want_exchange
        bad += 0 if ok else 1
        print("      %-38s انحراف=%-10s ختم=%-16s %s"
              % (label, round(offset, 3), exchange, "✓" if ok else "✗"))
    return bad


def main() -> int:
    bad = structural() + behavioural()
    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: ساعة الوسيط تُقاس ولا تُقنَّع · والساعة الرسميّة قاعدة باستثناءات معلَنة.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
