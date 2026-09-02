"""Contract guard for problem 22 (stage a) — a safety switch must never be
opened by a word.

Owner's ruling 2026-08-14, verbatim:

    "(a) first, then (b).  The real problem here is not the absence of a
     switch; it is that an existing SAFETY switch can read "لا" as True.  That
     must be closed before any interface work."
    "server.py: if the schema says boolean, accept only a real Boolean value
     after explicit, specific normalisation.  Any other value such as لا,
     nope, an unacceptable number, or arbitrary text -> explicit fail-closed
     rejection.  No implicit bool(string) conversion."
    "Settings.tsx: boolean -> a real toggle/switch.  No inputMode=decimal for
     this type.  The displayed state must reflect the actual logical value, not
     the truthiness of the text."
    ""0" and "1" are not accepted as boolean strings unless that is an explicit
     part of the contract -- and I am not adding that behaviour myself."
    "No change to atom behaviour or to 901."

What was measured before this guard existed, by running the REAL
`write_atom_config` against a temporary copy of 552's card:

    'nope' -> stored as the string 'nope' -> bool('nope') is True -> THE GATE
    'لا'   -> stored as the string 'لا'   -> bool('لا')   is True -> OPENS

  because the writer coerces `integer` and `number` and nothing else, while the
  panel draws every setting as a decimal text box even though the server tells
  it `type: boolean`.

  ١ القبول   -- real booleans and the two canonical spellings, nothing else.
  ٢ الرفض    -- everything else, and the card must stay byte-identical.
  ٣ الأرقام  -- integer/number keep coercing and keep their bounds.
  ٤ الواجهة  -- boolean is drawn as a switch, not a decimal box.
  ٥ الجيران  -- the six guards, 901 and the atoms are untouched.

Exit 1 on any divergence.
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from governance import server  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
A552 = ATOMS / "552_مدقق_الأمر" / "manifest.yaml"
A516 = ATOMS / "516_قاطع_الأمان" / "manifest.yaml"
UI = ROOT / "governance" / "ui" / "src" / "sections" / "Settings.tsx"

# Atoms and the gateway must not move a letter FOR THIS ITEM (22-a: the switch
# is a server/UI concern). Rebased 2026-08-15: items 70 and 10 moved 575, 516
# and 901 by the owner's order -- the pins follow, the meaning does not.
FROZEN = {"552_مدقق_الأمر": "2.8.2", "575_مرسل_الإدارة": "1.2.0",
          "584_شرعية_الستوب": "1.3.0", "585_حارس_الهامش": "1.2.0",
          "586_بوابة_الرموز": "1.0.0", "516_قاطع_الأمان": "2.5.0",
          "901_بوابة_الأوامر": None}

# His explicit list: a real Boolean, or the two canonical spellings. Nothing
# else -- and "0"/"1" are deliberately NOT in it.
ACCEPT = ((True, True), (False, False), ("true", True), ("false", False),
          ("TRUE", True), ("False", False), ("  true  ", True))
REJECT = ("لا", "nope", "", "0", "1", "yes", "no", "on", "off",
          "نعم", "2", "true false", None, 1, 0, 1.0, [], {})


def card(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def value_of(path: Path, key: str):
    return (card(path).get("config") or {}).get(key)


class Sandbox:
    """The REAL writer, pointed at a copy, with the live core cut off."""

    def __init__(self, real: Path):
        self.real = real
        self.dir = Path(tempfile.mkdtemp(prefix="sw22_"))
        self.copy = self.dir / "manifest.yaml"
        self._path = server._manifest_path
        self._request = server.core_request

    def __enter__(self):
        server._manifest_path = lambda atom_id: self.copy
        server.core_request = lambda path, method="GET": (200, b"[]")
        # الكاتب ينتظر النواة ثماني ثوانٍ ليتأكّد من إعادة التحميل. النواة مقطوعة
        # هنا عمدًا، فالانتظار وقتٌ بلا معنى — وكان يحوّل سقوط الحارس إلى مهلة
        # منتهية بدل نتيجة مقروءة. ننزع النوم وحده ونترك المنطق كما هو.
        self._sleep = server.time.sleep
        server.time.sleep = lambda *_a, **_k: None
        return self

    def __exit__(self, *exc):
        server._manifest_path = self._path
        server.core_request = self._request
        server.time.sleep = self._sleep
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, updates: dict):
        shutil.copy2(self.real, self.copy)
        before = self.copy.read_bytes()
        status, result = server.write_atom_config(0, updates)
        return status, result, before, self.copy.read_bytes()


def booleans() -> int:
    print("=" * 92)
    print("١+٢· المفتاح المنطقيّ — لا يُفتح إلّا بقيمة منطقيّة صريحة")
    print("=" * 92)
    bad = 0
    with Sandbox(A552) as box:
        print("  المقبول — ويُكتب بصيغة واحدة قانونيّة:")
        for typed, expected in ACCEPT:
            status, result, before, after = box.write({"enabled": typed})
            stored = value_of(box.copy, "enabled") if status == 200 else None
            # مفتاح أمان لا يجوز أن تبقى له قراءتان بالبطاقة (`True` بايثونيّة
            # و`true` يامليّة) — يُكتب صغيرًا دائمًا.
            written = [ln.strip() for ln in box.copy.read_text(encoding="utf-8").splitlines()
                       if ln.startswith("  enabled:")]
            canonical = bool(written) and written[-1] == "enabled: %s" % str(expected).lower()
            ok = status == 200 and stored is expected and canonical
            bad += 0 if ok else 1
            print("      %-14r ⇒ %-6s مُخزَّن=%-8r سطر=%-18s %s"
                  % (typed, status, stored, written[-1] if written else "—",
                     "✓" if ok else "✗ المتوقّع %r بصيغة صغيرة" % expected))

        print("\n  والنسخة تُرفع بالبطاقة **وبالكود معًا**:")
        # مقيس حيًّا: فتح البوّابة رفع بطاقة `552` إلى 2.8.1 وترك الكود 2.8.0،
        # فصار المشروع بمخالفة «نسخة مزدوجة» بضغطة زرّ واحدة.
        shutil.copy2(A552, box.copy)
        code_copy = box.copy.parent / "atom.py"
        shutil.copy2(A552.parent / "atom.py", code_copy)
        status, result, _, _ = box.write({"enabled": True})
        card_version = str(card(box.copy).get("version"))
        code_version = ""
        found = re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"',
                          code_copy.read_text(encoding="utf-8"), re.M)
        if found:
            code_version = found.group(1)
        ok = status == 200 and card_version == code_version
        bad += 0 if ok else 1
        print("      بطاقة=%-8s كود=%-8s %s"
              % (card_version, code_version or "—",
                 "✓ متزامنتان" if ok else "✗ مخالفة «نسخة مزدوجة»"))

        print("\n  المرفوض — ويجب ألّا يُكتب حرف واحد:")
        for typed in REJECT:
            status, result, before, after = box.write({"enabled": typed})
            untouched = before == after
            ok = status != 200 and untouched
            bad += 0 if ok else 1
            note = ""
            if status == 200:
                stored = value_of(box.copy, "enabled")
                note = "🔴 قُبل وخُزِّن %r ⇒ bool=%s" % (stored, bool(stored))
            elif not untouched:
                note = "✗ رُفض لكنّ الملفّ تغيّر"
            print("      %-14r ⇒ %-6s %s %s"
                  % (typed, status, "✓ رُفض ولم يُكتب" if ok else "", note))
    return bad


def numbers() -> int:
    print("\n" + "=" * 92)
    print("٣· الأرقام لم تتغيّر — التحويل والحدود كما كانت")
    print("=" * 92)
    bad = 0
    with Sandbox(A516) as box:
        cases = (
            ({"max_consecutive_losses": "7"}, 200, ("max_consecutive_losses", 7)),
            ({"max_daily_loss_pct": "12.5"}, 200, ("max_daily_loss_pct", 12.5)),
            ({"max_daily_loss_pct": "-1"}, 400, None),
            ({"max_daily_loss_pct": "سبعة"}, 400, None),
            ({"لا_يوجد": "1"}, 400, None),
        )
        for updates, expect_status, expect_pair in cases:
            status, result, before, after = box.write(updates)
            ok = status == expect_status
            if ok and expect_pair:
                key, want = expect_pair
                got = value_of(box.copy, key)
                ok = got == want
            if expect_status != 200:
                ok = ok and before == after
            bad += 0 if ok else 1
            print("      %-38s ⇒ %-6s %s" % (updates, status, "✓" if ok else "✗"))
    return bad


def surface() -> int:
    print("\n" + "=" * 92)
    print("٤+٥· الواجهة تعرضه مفتاحًا، والذرّات و901 لم تُمَسّ")
    print("=" * 92)
    bad = 0
    src = UI.read_text(encoding="utf-8")
    # المربّع الرقميّ يجب أن يبقى للأرقام وحدها: مرّة واحدة، وبعد فرع المفتاح.
    has_type = "'boolean'" in src or '"boolean"' in src
    has_switch = 'type="checkbox"' in src
    ordered = (has_switch and "inputMode" in src
               and src.index('type="checkbox"') < src.index("inputMode")
               and src.count("inputMode") == 1)
    # الربط نفسه، لا مجرّد وجود المقارنة في مكان ما بالملفّ: كسر سطر `checked`
    # وحده كان يمرّ لأنّ المقارنة باقية في سطر العرض.
    logical = "checked={vals[s.key] === 'true'}" in src
    checks = (
        ("الواجهة تفرّق حسب النوع", has_type),
        ("مفتاح حقيقيّ لا مربّع نصّ", has_switch),
        ("المربّع الرقميّ للأرقام وحدها", ordered),
        ("الحالة من القيمة المنطقيّة", logical),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-34s %s" % (label, "✓" if ok else "✗"))

    for folder, version in FROZEN.items():
        current = str(card(ATOMS / folder / "manifest.yaml").get("version"))
        ok = version is None or current == version
        bad += 0 if ok else 1
        print("      %-34s %-8s %s" % ("لم تُمَسّ: " + folder.split("_")[0], current,
                                       "✓" if ok else "✗ تغيّرت عن %s!" % version))
    gateway = (ATOMS / "901_بوابة_الأوامر" / "atom.py").read_text(encoding="utf-8")
    # Item 70 renamed what the gateway PUBLISHES (it requests now; 516 is the
    # single authority) -- the four owner actions and their wiring are unchanged,
    # which is what this barrier is actually about.
    ok = ("ACTIONS = {ACTION_HALT: EVENT_HALT_REQUEST, ACTION_RESET: EVENT_RESET," in gateway
          and "guard" not in gateway.lower())
    bad += 0 if ok else 1
    print("      %-34s %s" % ("أفعال البوّابة 901 كما هي", "✓" if ok else "✗"))
    return bad


def main() -> int:
    bad = booleans() + numbers() + surface()
    print("\n" + "=" * 92)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: المفتاح لا يُفتح بكلمة، والمرفوض لا يُكتب، والأرقام والذرّات كما هي.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
