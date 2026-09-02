#!/usr/bin/env python3
"""
scripts/diagnose.py — أداة التشخيص المنهجي (المرحلة الأولى)

غرضها واحد: **جمع الأدلة قبل اقتراح أي إصلاح.**

لا تُصلح شيئًا ولا تمنع شيئًا. تُظهر، والمالك يحكم.

السبب المباشر لوجودها موثَّق: في جلسة واحدة وقعت خمسة من ثمانية
مؤشرات حمراء يذكرها بروتوكول التشخيص —

  · «تخزين مؤقت» تفسيرًا لعرض متكرّر، مرتين، دون تتبّع     (رقم 2)
  · بناء مسار الأخبار عبر 613 كاملًا ثم اختباره فأعطى صفرًا (رقم 9)
  · وصف قسم 300 بالمعزول دون فتح ورقته وهي موجودة          (رقم 7)
  · تصحيح 608 مقابل 609 ثلاث مرات دون الانتباه للتكرار      (رقم 10)
  · سرد «المشاكل الرئيسية» قبل أي تحقيق                     (رقم 8)

كل واحد منها كان يُكشف بأمر واحد من هذه الأداة قبل كتابة سطر.

    python3 scripts/diagnose.py --event strategy.news.signal
    python3 scripts/diagnose.py --atom 411
    python3 scripts/diagnose.py --chain 616 463
    python3 scripts/diagnose.py --orphans
    python3 scripts/diagnose.py --papers 300
    python3 scripts/diagnose.py --attempt "611 لا يستقبل الخبر"

حين تنقص الأدلة تقول ذلك صراحةً بدل أن يملأ أحد الفراغ بتخمين.
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML مطلوبة: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
ATTEMPTS_FILE = ROOT / ".diagnose_attempts.json"

CORE_EVENTS = {
    "core.atom.started", "core.atom.failed", "core.atom.unhealthy",
    "core.atom.restarted", "core.critical_atom.unhealthy",
    "core.system.rescan_requested",
}

ATTEMPT_CEILING = 3

_OK = "✓"
_NO = "✗"
_WARN = "⚠"


class Graph:
    """خريطة من ينشر ماذا ومن يسمعه — تُبنى من المانيفستات لا من الظن."""

    def __init__(self) -> None:
        self.atoms: dict[int, dict] = {}
        self.publishers: dict[str, list[int]] = collections.defaultdict(list)
        self.subscribers: dict[str, list[int]] = collections.defaultdict(list)
        self.load_errors: list[str] = []
        self._load()

    def _load(self) -> None:
        for manifest_path in sorted(ATOMS.rglob("manifest.yaml")):
            try:
                data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                self.load_errors.append("%s: %s" % (manifest_path.parent.name, exc))
                continue
            atom_id = data.get("id")
            if atom_id is None:
                self.load_errors.append("%s: بلا id" % manifest_path.parent.name)
                continue
            entry = {
                "id": atom_id,
                "name": data.get("name", ""),
                "dir": manifest_path.parent,
                "publishes": list(data.get("publishes") or []),
                "subscribes": list(data.get("subscribes") or []),
                "dependencies": [d.get("id") for d in (data.get("dependencies") or [])],
                "config": data.get("config") or {},
                "critical": bool(data.get("critical", False)),
                "startup_mode": data.get("startup_mode", ""),
            }
            self.atoms[atom_id] = entry
            for event in entry["publishes"]:
                self.publishers[event].append(atom_id)
            for event in entry["subscribes"]:
                self.subscribers[event].append(atom_id)

    def label(self, atom_id: int) -> str:
        entry = self.atoms.get(atom_id)
        return "%d %s" % (atom_id, entry["name"][:32]) if entry else "%d (غير مبنية)" % atom_id

    def source_reads(self, atom_id: int) -> dict:
        """ما يقوله الكود فعلًا — لا ما يعلنه المانيفست."""
        entry = self.atoms.get(atom_id)
        if entry is None:
            return {}
        source_path = entry["dir"] / "atom.py"
        if not source_path.is_file():
            return {"error": "لا atom.py"}
        source = source_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return {"error": "خطأ صياغة سطر %s" % exc.lineno}

        constants = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                        constants[target.id] = node.value.value

        published, subscribed, guards = set(), set(), []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or not node.args:
                continue
            first = node.args[0]
            name = None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                name = first.value
            elif isinstance(first, ast.Name):
                name = constants.get(first.id)
            if name is None:
                continue
            if func.attr == "publish":
                published.add(name)
            elif func.attr == "subscribe":
                subscribed.add(name)

        # الشروط التي تُسقط الحمولة بصمت — أكثر ما يخفي انقطاعًا.
        #
        # الشرط الحارس ينتهي بـreturn، لكن جسمه قد يحمل عدّادًا وسطرَ سجل
        # قبلها: هذا بالضبط شكل الحارس في 613 الذي أسقط كل خبر بصمت،
        # وكاشف يشترط سطرًا واحدًا كان يتخطّاه.
        for handler in [n for n in ast.walk(tree)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(handler):
                if not isinstance(node, ast.If) or node.orelse or not node.body:
                    continue
                last = node.body[-1]
                if not isinstance(last, (ast.Return, ast.Continue)):
                    continue
                if isinstance(last, ast.Return) and last.value is not None:
                    continue
                text = ast.unparse(node.test) if hasattr(ast, "unparse") else "?"
                logged = any(
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in ("warning", "error", "info", "debug")
                    for stmt in node.body for inner in ast.walk(stmt))
                guards.append((handler.name, node.lineno, text[:76], logged))

        return {"publishes": sorted(published), "subscribes": sorted(subscribed),
                "guards": guards, "lines": len(source.splitlines())}


# ----------------------------------------------------------------- عروض --

def show_event(graph: Graph, event: str) -> int:
    print("═══ الحدث: %s ═══\n" % event)
    publishers = graph.publishers.get(event, [])
    subscribers = graph.subscribers.get(event, [])

    if not publishers and not subscribers:
        print("  %s لا يُنشر ولا يُسمع. الاسم غير موجود في أي مانيفست." % _NO)
        near = [e for e in set(list(graph.publishers) + list(graph.subscribers))
                if event.split(".")[0] in e]
        if near:
            print("     أسماء قريبة: %s" % sorted(near)[:6])
        print("\n  الأدلة غير كافية: تحقّق من الاسم قبل أي إصلاح.")
        return 1

    print("  الناشرون  (%d): %s" % (len(publishers),
                                    [graph.label(a) for a in publishers] or "لا أحد"))
    print("  المستمعون (%d): %s" % (len(subscribers),
                                    [graph.label(a) for a in subscribers] or "لا أحد"))

    if publishers and not subscribers:
        print("\n  %s يُنشر ولا يسمعه أحد — الطرف الثاني مفقود." % _WARN)
    if subscribers and not publishers:
        print("\n  %s يُسمع ولا ينشره أحد — المصدر مفقود." % _WARN)

    print("\n  ─ ما يقوله الكود مقابل المانيفست ─")
    for atom_id in publishers:
        reads = graph.source_reads(atom_id)
        if "error" in reads:
            print("    %s %s: %s" % (_NO, graph.label(atom_id), reads["error"]))
            continue
        actual = event in reads["publishes"]
        mark = _OK if actual else _WARN
        note = "" if actual else "  ← معلَن ولا يُنشر في الكود (أو يُبنى الاسم ديناميكيًا)"
        print("    %s %s ينشره فعلًا%s" % (mark, graph.label(atom_id), note))

    for atom_id in subscribers:
        reads = graph.source_reads(atom_id)
        if "error" in reads:
            continue
        if reads["subscribes"] and event not in reads["subscribes"]:
            print("    %s %s معلَن مشتركًا ولا يشترك في الكود"
                  % (_WARN, graph.label(atom_id)))
    return 0


def show_atom(graph: Graph, atom_id: int) -> int:
    entry = graph.atoms.get(atom_id)
    if entry is None:
        print("═══ الذرة %d ═══\n" % atom_id)
        print("  %s غير مبنية. لا مجلد ولا مانيفست." % _NO)
        return 1

    print("═══ %s ═══\n" % graph.label(atom_id))
    print("  المجلد: %s" % entry["dir"].relative_to(ROOT))
    print("  حرجة: %s  ·  الإقلاع: %s" % (entry["critical"], entry["startup_mode"]))

    print("\n  ─ المدخلات ─")
    if not entry["subscribes"]:
        print("    (لا يشترك بشيء)")
    for event in entry["subscribes"]:
        sources = graph.publishers.get(event, [])
        if event in CORE_EVENTS:
            print("    %s %-40s (من النواة)" % (_OK, event))
        elif sources:
            print("    %s %-40s من %s" % (_OK, event, sources))
        else:
            print("    %s %-40s لا ناشر — مدخل ميت" % (_NO, event))

    print("\n  ─ المخرجات ─")
    if not entry["publishes"]:
        print("    (لا ينشر شيئًا)")
    for event in entry["publishes"]:
        listeners = graph.subscribers.get(event, [])
        if listeners:
            print("    %s %-40s إلى %s" % (_OK, event, listeners))
        else:
            print("    %s %-40s لا مستمع — مخرَج مهمَل" % (_WARN, event))

    for dependency in entry["dependencies"]:
        if dependency not in graph.atoms:
            print("\n  %s اعتمادية مفقودة: %s" % (_NO, dependency))

    reads = graph.source_reads(atom_id)
    if "error" in reads:
        print("\n  %s الكود: %s" % (_NO, reads["error"]))
        return 1

    declared_publishes = set(entry["publishes"])
    actual_publishes = set(reads["publishes"])
    if actual_publishes - declared_publishes:
        print("\n  %s ينشر بلا إعلان: %s"
              % (_NO, sorted(actual_publishes - declared_publishes)))

    if reads["guards"]:
        print("\n  ─ الشروط التي تُسقط الحمولة بصمت (%d) ─" % len(reads["guards"]))
        print("    أول ما يُفحص حين يصل الحدث ولا يخرج شيء:")
        print("    (صامت) = يُسقط بلا سطر سجل — لا أثر يُتتبَّع")
        for name, line, test, logged in reads["guards"][:12]:
            print("      سطر %-5d %-20s %s if %s"
                  % (line, name[:20], "       " if logged else "(صامت)", test))
        if len(reads["guards"]) > 12:
            print("      … و%d غيرها" % (len(reads["guards"]) - 12))
    return 0


def show_chain(graph: Graph, start: int, end: int) -> int:
    print("═══ السلسلة: %s ← %s ═══\n" % (graph.label(start), graph.label(end)))
    if start not in graph.atoms or end not in graph.atoms:
        missing = [a for a in (start, end) if a not in graph.atoms]
        print("  %s غير مبنية: %s" % (_NO, missing))
        return 1

    seen = {start: None}
    frontier = [start]
    while frontier:
        nxt = []
        for atom_id in frontier:
            for event in graph.atoms[atom_id]["publishes"]:
                for listener in graph.subscribers.get(event, []):
                    if listener not in seen:
                        seen[listener] = (atom_id, event)
                        nxt.append(listener)
        frontier = nxt

    if end not in seen:
        print("  %s لا مسار من %d إلى %d.\n" % (_NO, start, end))
        print("  ما ينشره %d: %s" % (start, graph.atoms[start]["publishes"]))
        print("  ما يسمعه %d: %s" % (end, graph.atoms[end]["subscribes"]))
        for event in graph.atoms[end]["subscribes"]:
            if not graph.publishers.get(event) and event not in CORE_EVENTS:
                print("\n  %s الحلقة المقطوعة: %s لا ينشره أحد." % (_NO, event))
        return 1

    path = []
    cursor = end
    while seen[cursor] is not None:
        parent, event = seen[cursor]
        path.append((parent, event, cursor))
        cursor = parent
    path.reverse()

    print("  المسار (%d خطوة):\n" % len(path))
    for parent, event, child in path:
        print("    %s" % graph.label(parent))
        print("        ↓ %s" % event)
    print("    %s\n" % graph.label(end))

    print("  ─ ما قد يُسقط الحمولة على الطريق ─")
    any_guard = False
    for _, _, child in path:
        reads = graph.source_reads(child)
        if "error" in reads or not reads.get("guards"):
            continue
        any_guard = True
        print("    %s (%d شرط):" % (graph.label(child), len(reads["guards"])))
        for name, line, test, logged in reads["guards"][:4]:
            print("        سطر %-5d %s if %s"
                  % (line, "       " if logged else "(صامت)", test))
    if not any_guard:
        print("    (لا شروط إسقاط ظاهرة)")
    return 0


def show_orphans(graph: Graph) -> int:
    print("═══ الأطراف المعلَّقة ═══\n")
    silent, starved, isolated = [], [], []
    for atom_id, entry in sorted(graph.atoms.items()):
        real_subs = [e for e in entry["subscribes"] if e not in CORE_EVENTS]
        if not entry["publishes"] and not real_subs:
            isolated.append(atom_id)
            continue
        if entry["publishes"] and all(
                not graph.subscribers.get(e) for e in entry["publishes"]):
            silent.append(atom_id)
        if real_subs and all(not graph.publishers.get(e) for e in real_subs):
            starved.append(atom_id)

    print("  معزولة تمامًا      : %s" % (isolated or "لا شيء " + _OK))
    print("  كل مخرجاتها مهملة  : %d  %s" % (len(silent), silent))
    print("  بلا مدخل واصل      : %d  %s" % (len(starved), starved))

    no_publisher = sorted(e for e in graph.subscribers
                          if e not in graph.publishers and e not in CORE_EVENTS)
    no_listener = sorted(e for e in graph.publishers if e not in graph.subscribers)
    print("\n  أحداث بلا ناشر     : %d" % len(no_publisher))
    for event in no_publisher[:10]:
        print("      %-44s ينتظره %s" % (event, graph.subscribers[event]))
    if len(no_publisher) > 10:
        print("      … و%d غيره" % (len(no_publisher) - 10))
    print("\n  أحداث بلا مستمع    : %d" % len(no_listener))
    return 0


def show_papers(graph: Graph, section: int) -> int:
    """المرحلة الثانية: قارن بالمرجع — واقرأه، لا تتصفّحه."""
    print("═══ ورقة القسم %d مقابل المبني ═══\n" % section)
    papers = sorted(Path("/mnt/user-data/uploads").glob("section_%d*.md" % section))
    if not papers:
        papers = sorted(ROOT.glob("governance/docs/section_%d*.md" % section))
    if not papers:
        print("  %s لا ورقة بناء لهذا القسم." % _WARN)
        print("     ما بُني فيه بلا مرجع يُقاس عليه — وأي حكم عليه استنتاج.")
        built = sorted(a for a in graph.atoms if section <= a < section + 50)
        print("\n  المبني: %d ذرة %s" % (len(built), built))
        return 1

    print("  الورقة: %s" % papers[0].name)
    text = papers[0].read_text(encoding="utf-8")
    declared = set()
    for match in re.finditer(r'^\|\s*(\d{3})\s*\|', text, re.M):
        declared.add(int(match.group(1)))
    for match in re.finditer(r'^#+\s*(\d{3})(?:\s*[-–—/]\s*(\d{3}))?\s*[—-]', text, re.M):
        first = int(match.group(1))
        if match.group(2) and int(match.group(2)) - first < 20:
            declared.update(range(first, int(match.group(2)) + 1))
        else:
            declared.add(first)

    built = {a for a in graph.atoms if section <= a < section + 50}
    print("  معلن %d · مبني %d · ناقص %d"
          % (len(declared), len(declared & built), len(declared - built)))
    if declared - built:
        print("  الناقص: %s" % sorted(declared - built))
    if built - declared:
        print("  مبني وغير معلن: %s" % sorted(built - declared))

    events = set(re.findall(r'`([a-z_]+(?:\.[a-z_]+)+)`', text))
    unpublished = sorted(e for e in events
                         if e not in graph.publishers and e not in CORE_EVENTS)
    if unpublished:
        print("\n  أحداث تذكرها الورقة ولا ينشرها أحد (%d):" % len(unpublished))
        for event in unpublished[:12]:
            print("      %s" % event)
    print("\n  اقرأ الورقة كاملة قبل أي حكم على القسم: %s" % papers[0])
    return 0


def track_attempt(label: str) -> int:
    """المرحلة الرابعة: عدّاد المحاولات.

    يُظهر ولا يمنع. المالك يحكم — لكن الرقم يصير مرئيًا بدل أن يعتمد
    على ذاكرة أثبتت فشلها: تصحيح 608 مقابل 609 تكرّر ثلاث مرات في جلسة
    واحدة دون أن ينتبه أحد إلى أنه تكرار.
    """
    try:
        attempts = json.loads(ATTEMPTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        attempts = {}

    attempts[label] = attempts.get(label, 0) + 1
    count = attempts[label]
    try:
        ATTEMPTS_FILE.write_text(json.dumps(attempts, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except OSError as exc:
        print("  %s تعذّر حفظ العدّاد: %s" % (_WARN, exc))

    print("═══ محاولة رقم %d — %s ═══\n" % (count, label))
    if count < ATTEMPT_CEILING:
        print("  تحت السقف (%d). تابع، وارجع للمرحلة الأولى بالمعلومة الجديدة."
              % ATTEMPT_CEILING)
        return 0

    print("  %s بلغت %d محاولات." % (_WARN, count))
    print("""
  البروتوكول: ثلاثة إصلاحات فاشلة ليست فرضية خاطئة — هي بنية خاطئة.

  اسأل قبل المحاولة الرابعة:
    · هل كل إصلاح يكشف حالة مشتركة جديدة في مكان مختلف؟
    · هل يتطلب الإصلاح إعادة هيكلة لتنفيذه؟
    · هل كل علاج يُنتج عرضًا جديدًا في مكان آخر؟

  إن كان الجواب نعم على أيّها: البنية هي المشكلة، لا التنفيذ.
  اعرض الأمر على المالك قبل أي محاولة إضافية.""")
    return 0


def show_attempts() -> int:
    try:
        attempts = json.loads(ATTEMPTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("  لا محاولات مسجَّلة.")
        return 0
    print("═══ سجل المحاولات ═══\n")
    for label, count in sorted(attempts.items(), key=lambda x: -x[1]):
        mark = _WARN if count >= ATTEMPT_CEILING else " "
        print("  %s %d× %s" % (mark, count, label))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="التشخيص المنهجي — يجمع الأدلة ولا يقترح إصلاحًا")
    parser.add_argument("--event", help="من ينشر هذا الحدث ومن يسمعه")
    parser.add_argument("--atom", type=int, help="مدخلات ومخرجات ذرة وشروط إسقاطها")
    parser.add_argument("--chain", nargs=2, type=int, metavar=("FROM", "TO"),
                        help="هل يوجد مسار بين ذرتين وأين ينقطع")
    parser.add_argument("--orphans", action="store_true",
                        help="كل الأطراف المعلَّقة في المنصة")
    parser.add_argument("--papers", type=int, metavar="SECTION",
                        help="ورقة القسم مقابل المبني")
    parser.add_argument("--attempt", metavar="LABEL",
                        help="سجّل محاولة إصلاح لهذه المشكلة")
    parser.add_argument("--attempts", action="store_true", help="اعرض سجل المحاولات")
    args = parser.parse_args()

    if args.attempt:
        return track_attempt(args.attempt)
    if args.attempts:
        return show_attempts()

    graph = Graph()
    if graph.load_errors:
        print("%s مانيفستات لم تُقرأ:" % _WARN)
        for error in graph.load_errors:
            print("    %s" % error)
        print()

    if args.event:
        return show_event(graph, args.event)
    if args.atom is not None:
        return show_atom(graph, args.atom)
    if args.chain:
        return show_chain(graph, args.chain[0], args.chain[1])
    if args.orphans:
        return show_orphans(graph)
    if args.papers is not None:
        return show_papers(graph, args.papers)

    parser.print_help()
    print("""
المراحل الأربع — الأداة تخدم الأولى والرابعة:

  ١ السبب الجذري : --event · --atom · --chain · --orphans
  ٢ النمط        : --papers  ثم اقرأ الورقة كاملة، لا تتصفّحها
  ٣ الفرضية      : اكتب فرضية واحدة، غيّر متغيّرًا واحدًا، اختبر
  ٤ التنفيذ      : اختبار فاشل أولًا، ثم إصلاح واحد، ثم --attempt

الأداة تُظهر ولا تمنع. المالك يحكم.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
