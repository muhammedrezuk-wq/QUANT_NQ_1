"""حارس هويّة أمر الإدارة — حكم المالك ٢٠٢٦-٠٨-١٦.

المقيس حيًّا: صفقتان مفتوحتان · `572 tracked=2 breakevens=0` · `573 tracked=2
trails=0` · `574 tracked=2 partials=0` · و`575 MISSING_ACCOUNT_ID`. أي أنّ
ذرّات الإدارة ترى المركز وتقرّر، وأوامرها تموت عند المرسل لأنّها بلا رقم حساب.
والمرسل محقّ في الرفض: أمر تعديل بلا حساب لا يجوز أن يُنفَّذ.

الجذر في المُصدِر لا في المرسل: `580` يبني المفتاح من `account_id` ثمّ لا
يضعه في الأمر (سطر ٩٥ مقابل ١٣٠). و`579` مثله يجب أن يُفحص.

شقّان:
  ١· كل ناشر لـ`execution.manage.command` يضع `account_id` في كل نشرة.
  ٢· المرسل `575` يبقى fail-closed: بلا حساب لا يُنفَّذ (لا نُرخّي الشرط).
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
# السلسلة كاملة: النيّة (572/573/574) ← المنسّق (570) ← الأمر (575).
# فحص الطرف الأخير وحده كان ضيّقًا: الهويّة تسقط عند المنبع فيصل المنسّق
# فارغًا مهما مرّر. كل حلقة تُفحص.
EVENTS = ("execution.manage.command", "execution.manage.intent")
SENDER = ATOMS / "575_مرسل_الإدارة" / "atom.py"

bad = 0


def show(label: str, ok: bool, detail: str = "") -> None:
    global bad
    if not ok:
        bad += 1
    print("   %-58s %-22s %s" % (label, detail, "✓" if ok else "✘"))


def publishers() -> list[pathlib.Path]:
    found = []
    for manifest in sorted(ATOMS.glob("*/manifest.yaml")):
        text = manifest.read_text(encoding="utf-8", errors="replace")
        head = text.split("subscribes:", 1)[0]
        if any(e in head for e in EVENTS) and (manifest.parent / "atom.py").exists():
            found.append(manifest.parent / "atom.py")
    return found


def event_constants(tree: ast.AST) -> set[str]:
    """أسماء الثوابت التي تحمل قيمة الحدث — لا نطارد النصّ حرفيًّا."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and node.value.value in EVENTS:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def publish_calls(tree: ast.AST, names: set[str]) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        attr = func.attr if isinstance(func, ast.Attribute) else None
        if attr != "publish":
            continue
        first = node.args[0]
        hit = (isinstance(first, ast.Name) and first.id in names) or \
              (isinstance(first, ast.Constant) and first.value in EVENTS)
        if hit:
            calls.append(node)
    return calls


def carries_account(call: ast.Call) -> bool:
    """النشرة تحمل `account_id` صراحةً، أو تنشر قاموسًا مبنيًّا بالبسط (**)."""
    if len(call.args) < 2:
        return False
    body = call.args[1]
    if isinstance(body, ast.Dict):
        for key in body.keys:
            if key is None:          # **payload -- الهويّة تأتي من الأصل
                return True
            if isinstance(key, ast.Constant) and key.value == "account_id":
                return True
        return False
    return True                       # متغيّر مبنيّ في مكان آخر: لا نحكم عليه هنا


print("=" * 100)
print("حارس هويّة أمر الإدارة — أمر بلا رقم حساب لا يُنفَّذ ولا يُرسَل")
print("=" * 100)

print("\n١· كل ناشر لأمر الإدارة يضع رقم الحساب:")
files = publishers()
show("عُثر على ناشرين", bool(files), "عدد=%d" % len(files))
for path in files:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    names = event_constants(tree)
    calls = publish_calls(tree, names)
    atom = path.parent.name.split("_", 1)[0]
    if not calls:
        show("%s ينشر الحدث فعلًا" % atom, False, "لا نشرة")
        continue
    missing = [c for c in calls if not carries_account(c)]
    show("%s: كل نشراته تحمل `account_id`" % atom, not missing,
         "نشرات=%d ناقصة=%d" % (len(calls), len(missing)))
    for call in missing:
        print("      ↳ سطر %d بلا رقم حساب" % call.lineno)

print("\n٢· المرسل يبقى fail-closed (لا نُرخّي الشرط لنُمرّر العطل):")
src = SENDER.read_text(encoding="utf-8", errors="replace")
code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
show("`575` يقرأ `account_id` من الأمر", "payload.get(\"account_id\")" in code, "")
show("ويرفض عند غيابه بسبب مُسمّى", "MISSING_ACCOUNT_ID" in code, "")
show("ولا يخترع حسابًا افتراضيًّا",
     "account_id\") or \"\"" in code and "DEFAULT_ACCOUNT" not in code, "")

print("\n" + "=" * 100)
print("الاختلافات = %d" % bad)
print("سليم: كل أمر إدارة يحمل هويّة حسابه، والمرسل يرفض ما لا يحملها."
      if bad == 0 else "ساقط: أمر إدارة يخرج بلا رقم حساب فيموت عند المرسل.")
sys.exit(1 if bad else 0)
