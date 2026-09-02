#!/usr/bin/env python3
"""
scripts/validate_atoms.py — مدقق الذرات الرسمي (Atom Validator)
================================================================
المادة 8 (ملف 4): "الجهة الرسمية الوحيدة المخوّلة بالتحقق من التزام
الذرة بكامل مواد هذا الدستور هي مدقق الذرات الرسمي. أداة برمجية آلية
تعتمد على تحليل AST. نتيجته هي المرجع النهائي والوحيد لقبول الذرة."

يُشغَّل قبل التسليم لا بعده. كود خروج غير صفر عند أي مخالفة.

    python3 scripts/validate_atoms.py                 # كل الذرات
    python3 scripts/validate_atoms.py --atom 706      # ذرة واحدة
    python3 scripts/validate_atoms.py --coverage      # مع قياس التغطية (بطيء)
    python3 scripts/validate_atoms.py --json          # مخرَج آلي

كل فحص هنا مشتقّ من مادة دستورية أو من قاعدة مثبتة بعطل وقع فعلًا،
والمصدر مذكور في وصف الفحص. الفحوص التي أخطأت في جولات سابقة
(الأرقام السحرية خصوصًا) تستبعد الحالات المشروعة صراحة، لأن كاشفًا
يعطي إنذارًا كاذبًا أسوأ من غياب الكاشف.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass, field
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
PAPERS = ROOT / "docs"
QUALITY_CONTRACT = ROOT / "config" / "atom_quality_contract.json"


def _load_quality_contract() -> dict:
    try:
        payload = json.loads(QUALITY_CONTRACT.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


QUALITY_RULES = _load_quality_contract()

# حكم المالك المباشر 2026-08-27: «ارفع الحد إلى 400 ولا تفكك 578/601» —
# الحد السابق 350 كان يفرض تفكيكًا بلا سبب تشغيلي. 578=379 و601=389
# يمران الآن، والاستثناءات القديمة تبقى مسجلة كما هي.
MAX_EFFECTIVE_LINES = 400
# الشقّ الثاني من نفس الحكم: «أو عطيها استثناء» — ذرّة مستثناة بسبب موثّق
# تنزل تحذيرًا معلَنًا لا فشلًا. لا استثناء بلا سبب مكتوب هنا.
SIZE_EXEMPT: dict[int, str] = {
    166: "محرك دمج المسارين (NQ بند 22 حزمة أ): مجمّعان + دمج + عقد ثماني — ضغطه يطمس المنطق",
    454: "فلتر القرار (NQ بند 22): حواجز برباعية العقد + نافذة الأخبار — ضغطه يطمس الحواجز",
    622: "بوّابة تغذية FIX (أمر المالك ٢٠٢٦-٠٨-٢١): جلسة + إعادة اتصال + دفتر + "
         "قانون القفزة + عدّادات معلنة، ومنها يدخل كل سعر يُبنى عليه قرار — "
         "ضغطها يخفي أخطر مسار بالمشروع، وهي ستكبر بمصادر سي‑تريدر الباقية",
}
MIN_COVERAGE_PCT = 85

CLOCK_OWNERS = {3, 806, 608}
# مصدرا النبض (003، 806) حلقتان بطبيعتهما؛ كل ما عداهما يجب أن يُقاد بالنبض.
# الكاشف تحذيري لا مانع: قارئات الجسر القديمة عبرت بوابات الفحص الحية قبل
# تقنينه، وتحويلها قرار مالك (سابقة 617: حلقة خفية لم يرها أحد حتى 2026-08-11).
# 622 مستثناة بختم NQ (٢٠٢٦-٠٨-٢١): جلسة FIX تحتاج قارئًا دائمًا على المقبس —
# إن لم يقرأ أحد تتكدّس الرسائل وتضيع نبضة البروتوكول فيقطع السيرفر الجلسة.
# وتصريفها على نبضة الثانية يجمّع التِكّات في دفعات، أي يعيد التكثيف الذي وُجدت
# الذرّة أصلًا لإزالته. استثناء معلَن هنا لا تحذير صامت.
BACKGROUND_ALLOWED = {3, 806, 622}
# 600: بوابات التغذية الخارجية المصرَّح لها في الأوراق.
# 850: خادم عرض محلي (http.server) لا اتصال صادر.
NETWORK_ALLOWED = {608, 610, 620, 850}

# ─── قسم أسمر (الكريبتو) — بوّابات التغذية المصرَّح لها، ٢٠٢٦-٠٨-٢٩ ───
# ق13 تمنع `create_task` لأنّ العمل يُقاد بالنبض، وم3 تمنع الشبكة داخل الذرّة.
# والقاعدتان صحيحتان لذرّة تحليل. لكن **بوّابة تغذية** لا تستطيع أن تكون كذلك:
# مجرى WebSocket لا يُستدعى بالنبض، بل يبقى مفتوحًا يستقبل. فالقائمتان أعلاه
# هما الآليّة المعلَنة لهذا بالضبط (608/610/620/622 بالفوركس) — والذرّات أدناه
# نظائرها بالكريبتو. تُعلَن هنا بأسمائها لا تُخفى بالكاشف، فمن يقرأ الملفّ يرى
# بالضبط أيّ ذرّة أُعفيت ولماذا.
#   1001 مدير كون الأصول    — يستطلع قوائم MEXC عبر urllib
#   2003 الساعة              — مزامنة NTP بخيط مستقلّ
#   2615 جسر مصدر الأخبار    — يجلب من مزوّد الأخبار
#   2616 جسر الأخبار         — يمرّر من الجسر للناقل
#   2620 مصدر MEXC           — مجرى WebSocket دائم
#   2621 مصدر MEXC REST      — استطلاع دوريّ
#   2622 مصدر Binance        — استطلاع دوريّ
# ولا واحدة منها تُصدِر أمرًا: فحص «التنفيذ بشريّ» يثبت ذلك بـ84 منيفستًا.
_CRYPTO_FEED_GATES = {1001, 2003, 2615, 2616, 2620, 2621, 2622}
BACKGROUND_ALLOWED |= _CRYPTO_FEED_GATES
NETWORK_ALLOWED |= _CRYPTO_FEED_GATES

# 610 معفاة بأمر صريح من المالك. الإعفاء مسجّل هنا لا مخفي في الكاشف،
# ويظهر في التقرير كي لا يبدو المشروع نظيفًا وهو ليس كذلك.
WAIVED = {610: "معفاة بأمر المالك"}

ALLOWED_NUMBERS = {0, 1, 2, -1, 0.0, 1.0, 2.0, 100, 100.0, 1000, 1000.0,
                   60, 3600, 86400, 24, 10}
TECHNICAL_NUMBERS = {65536, 32, 404, 503, 200, 180, 429, 4096, 8192,
                     1_000_000_000, 4_000_000_000}

LIFECYCLE = ("initialize", "start", "stop", "shutdown", "health_check")

SEV_ERROR = "ERROR"
SEV_WARN = "WARN"


@dataclass
class Finding:
    atom_id: int
    directory: str
    check: str
    article: str
    severity: str
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0
    waived: list = field(default_factory=list)

    def add(self, atom_id, directory, check, article, severity, detail):
        self.findings.append(Finding(atom_id, directory, check, article,
                                     severity, detail))

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == SEV_ERROR]

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == SEV_WARN]


def dense_semicolon_lines(path: Path) -> list[int]:
    """Lines carrying 3+ statements joined by ';' (real OP tokens only,
    so ';' inside SQL/string literals never false-positives)."""
    rows: dict[int, int] = {}
    try:
        with tokenize.open(path) as handle:
            for tok in tokenize.generate_tokens(handle.readline):
                if tok.type == tokenize.OP and tok.string == ";":
                    rows[tok.start[0]] = rows.get(tok.start[0], 0) + 1
    except (OSError, tokenize.TokenizeError, SyntaxError):
        return []
    return sorted(line for line, count in rows.items() if count >= 2)


def effective_lines(path: Path) -> int:
    """السطر الفعلي حسب المادة 9: يستثني الفراغ والتعليق والاستيراد
    والديكور وسلاسل التوثيق و pass."""
    source = path.read_text(encoding="utf-8")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError:
        return 0
    ignore = (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
              tokenize.DEDENT, tokenize.ENDMARKER, tokenize.STRING)
    lines = {t.start[0] for t in tokens if t.type not in ignore}
    body = source.splitlines()
    return len([n for n in lines
                if not body[n - 1].strip().startswith(("import ", "from ", "@"))
                and body[n - 1].strip() != "pass"])


def literal_numbers(tree: ast.AST) -> list[tuple[str, int, object]]:
    """أرقام في منطق العمل — تستبعد ما هو مشروع صراحةً:

    round(x, N) و int(x, N) — دقة تنسيق لا قيمة تشغيلية.
    cfg.get("k", DEFAULT) — القيمة الفعلية من المانيفست، وهذا افتراضي أمان.
    __init__ — قيم ما قبل قراءة الإعداد.
    الفهرسة والشرائح.
    """
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "get":
                for arg in node.args[1:]:
                    skip.update(id(x) for x in ast.walk(arg))
            if isinstance(func, ast.Name) and func.id in ("round", "int", "float",
                                                          "str", "deque", "len"):
                for arg in node.args[1:]:
                    skip.update(id(x) for x in ast.walk(arg))
            # deque(maxlen=N) وما شابه: سعة بنية لا قيمة تشغيلية
            for kw in node.keywords:
                if kw.arg in ("maxlen", "timeout", "poll_interval", "maxsize"):
                    skip.update(id(x) for x in ast.walk(kw.value))
        if isinstance(node, (ast.Subscript, ast.Slice)):
            skip.update(id(x) for x in ast.walk(node))
        # قاموس ترتيب تصنيفي {LOW: 1, MEDIUM: 2, HIGH: 3} — رتب لا قيم
        if isinstance(node, ast.Dict) and node.keys and all(
                isinstance(k, ast.Name) and k.id.isupper() for k in node.keys if k):
            skip.update(id(x) for x in ast.walk(node))

    hits = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if fn.name == "__init__":
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Constant) or id(node) in skip:
                continue
            value = node.value
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if value in ALLOWED_NUMBERS or value in TECHNICAL_NUMBERS:
                continue
            hits.append((fn.name, node.lineno, value))
    return hits


def published_events(tree: ast.AST, source: str) -> set[str]:
    """أسماء الأحداث المنشورة — نصًا مباشرًا أو عبر ثابت وحدة."""
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                    constants[target.id] = node.value.value

    events = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "publish"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            events.add(first.value)
        elif isinstance(first, ast.Name) and first.id in constants:
            events.add(constants[first.id])
    return events


def subscribed_events(tree: ast.AST) -> set[str]:
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                    constants[target.id] = node.value.value
    events = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "subscribe"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            events.add(first.value)
        elif isinstance(first, ast.Name) and first.id in constants:
            events.add(constants[first.id])
    return events


def has_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def silent_drop_risk(tree: "ast.AST", source: str) -> list[str]:
    """بناء ٣ من ورقة X (ختم المالك ٢٠٢٦-٠٨-٢٣): معالج حدث فيه شرطٌ يفحص
    الحمولة نفسها (payload.get / not payload[...]) ويرميها بـ return عارية،
    والملف كله بلا عدّاد مرميّات ظاهر — إسقاط صامت محتمل. تحذير لا مخالفة.
    حرّاس دورة الحياة (not self._running / context None / isinstance العامّ)
    ليست إسقاط بيانات فلا تُحسب."""
    counters = ("_dropped", "_invalid", "_rejected", "failed_count",
                "identity_rejected", "_not_ready")
    risky: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.AsyncFunctionDef)
                and node.name.startswith("_on_")):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.If):
                continue
            test_src = ast.get_source_segment(source, sub.test) or ""
            guards_payload = ("payload" in test_src or "p.get(" in test_src) \
                and "_running" not in test_src and "_context" not in test_src \
                and "isinstance" not in test_src
            bare_return = (len(sub.body) == 1 and isinstance(sub.body[0], ast.Return)
                           and sub.body[0].value is None)
            if guards_payload and bare_return \
                    and not any(c in source for c in counters):
                risky.append(node.name)
                break
    return sorted(set(risky))


def _executable_language_findings(tree: ast.AST, source: str) -> list[str]:
    """Find Arabic in executable identifiers or event-name contracts only.

    Comments, docstrings, and human-facing messages are intentionally excluded
    by the owner-approved language contract. Event names are included because
    they are runtime protocol identifiers, not presentation text.
    """
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.arg, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = getattr(node, "id", None) or getattr(node, "arg", None) or getattr(node, "name", None)
            if isinstance(name, str) and has_arabic(name):
                findings.append(f"identifier:{name}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr not in {"publish", "subscribe", "subscribe_all"} or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and has_arabic(first.value):
                findings.append(f"event:{first.value}")
    return sorted(set(findings))


def _owned_numeric_values(atom_id: int) -> set[object]:
    owned: set[object] = set()
    groups = (QUALITY_RULES.get("numeric_ownership") or {})
    for values_by_atom in groups.values():
        values = values_by_atom.get(str(atom_id), []) if isinstance(values_by_atom, dict) else []
        if isinstance(values, list):
            owned.update(values)
    return owned


def check_atom(directory: Path, manifest: dict, report: Report,
               all_ids: set[int], strict_language: bool = False) -> None:
    atom_id = manifest["id"]
    label = directory.name
    atom_py = directory / "atom.py"

    def fail(check, article, detail, severity=SEV_ERROR):
        report.add(atom_id, label, check, article, severity, detail)

    if not atom_py.is_file():
        fail("ملف الذرة", "م5/ملف3", "atom.py غير موجود")
        return

    source = atom_py.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        fail("الصياغة", "م5/ملف2", f"خطأ صياغة سطر {exc.lineno}")
        return

    language_hits = _executable_language_findings(tree, source)
    if language_hits:
        fail("لغة الكود", "م3/ملف4", "عربي في عقد تنفيذي: " + ", ".join(language_hits))

    # ── المادة 4 (ملف 3) + المادة 10 (ملف 4): حظر الاستيراد بين الذرات
    for node in ast.walk(tree):
        module = None
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
        elif isinstance(node, ast.Import):
            module = ",".join(a.name for a in node.names)
        if module and re.search(r'(^|\.)\d{3}_\w+', module):
            fail("استيراد ذرة", "م4/ملف3", f"يستورد {module}")
    if re.search(r'parents?\[\d\]\s*(?:\.parent\s*)?/\s*["\']\d{3}_', source):
        fail("استيراد ذرة", "م4/ملف3", "يحمّل atom.py لذرة أخرى بالمسار")

    # ── المادة 7 (ملف 2): حظر الطباعة المباشرة
    if re.search(r'(?<![.\w])print\s*\(', source):
        fail("طباعة مباشرة", "م7/ملف2", "print() في كود الذرة")
    if re.search(r'sys\.(stdout|stderr)\.write', source):
        fail("طباعة مباشرة", "م7/ملف2", "كتابة مباشرة إلى stdout/stderr")

    # ── بناء ٣ (ورقة X، ختم ٢٠٢٦-٠٨-٢٣): إسقاط صامت محتمل — تحذير معلَن
    if (manifest.get("subscribes")
            and any(not str(e).startswith("SYS_") for e in manifest["subscribes"])):
        for handler in silent_drop_risk(tree, source):
            fail("إسقاط صامت (بناء ٣)", "X/بناء٣",
                 f"{handler} يرمي مدخلًا بشرط return بلا عدّاد مرميّات في الملف كله",
                 severity=SEV_WARN)

    # ── المادة 9 (ملف 4): حدّ 400 سطرًا فعليًّا (حكم المالك 2026-08-27) — والاستثناء الموثَّق تحذير معلَن
    lines = effective_lines(atom_py)
    if lines > MAX_EFFECTIVE_LINES:
        if atom_id in SIZE_EXEMPT:
            fail("حجم الذرة (مستثناة بحكم المالك)", "م9/ملف4",
                 f"{lines} سطر فعلي > {MAX_EFFECTIVE_LINES} — {SIZE_EXEMPT[atom_id]}",
                 severity=SEV_WARN)
        else:
            fail("حجم الذرة", "م9/ملف4", f"{lines} سطر فعلي > {MAX_EFFECTIVE_LINES}")

    # ── المادة 9 (ملف 4): حظر الأرقام السحرية
    numbers = literal_numbers(tree)
    owned = _owned_numeric_values(atom_id)
    unowned = [item for item in numbers if item[2] not in owned]
    if unowned:
        sample = sorted({v for _, _, v in unowned})[:5]
        where = sorted({fn for fn, _, _ in unowned})[:2]
        fail("أرقام سحرية", "م9/ملف4", f"{sample} في {where} — لا ملكية معلنة")

    # ── المادة 5 (ملف 2): دوال دورة الحياة الإجبارية
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = [n for n in LIFECYCLE if n not in defined]
    if missing:
        fail("دورة الحياة", "م5/ملف2", f"دوال ناقصة: {missing}")
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name in LIFECYCLE]:
        fail("دورة الحياة", "م5/ملف2", f"{fn.name} ليست async")

    # ── قاعدة مثبتة بعطل: استثناء من الإقلاع يقتل الذرة بدل إعلان حالتها
    #    (601 و617 وقعا فيه، والذرة تُسقط بدل أن تُعلن DEGRADED)
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name in ("initialize", "start")]:
        if any(isinstance(x, ast.Raise) for x in ast.walk(fn)):
            fail("استثناء من الإقلاع", "م8/ملف2",
                 f"{fn.name} ترمي استثناء بدل إعلان الحالة")

    # ── المادة 10 (ملف 2): حظر الوصول للتنفيذ الداخلي للنواة
    for private in ("_registry", "_event_bus", "_health_manager"):
        if re.search(rf'context\.{private}|\._context\.{private}', source):
            fail("تجاوز العقد", "م10/ملف2", f"وصول إلى {private}")

    # ── المادة 3 (ملف 4): الشبكة عبر بوابة ذرية موثّقة فقط
    if atom_id not in NETWORK_ALLOWED:
        net = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                net |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                net.add(node.module.split(".")[0])
        forbidden = net & {"socket", "requests", "urllib", "aiohttp", "httpx",
                           "websockets", "http"}
        if forbidden:
            fail("اتصال شبكي", "م3/ملف4", f"يستورد {sorted(forbidden)}")

    # ── القاعدة 13: لا حلقات خلفية — العمل يُقاد بنبض الناقل (سابقة عاصفة
    #    2026-08-11: 164 ألف أمر من محركات لا يراقبها أحد بين النبضات).
    if atom_id not in BACKGROUND_ALLOWED and re.search(r'\bcreate_task\s*\(', source):
        fail("حلقة خلفية", "ق13", "asyncio.create_task في الذرة — العمل يُقاد بالنبض",
             SEV_WARN)

    # ── مقروئية: الأسلوب المضغوط يخفي المنطق عن المراجعة (سابقة الدمج
    #    2026-08-10: ملفات بسطر واحد عبرت المدقق وأخفت أسلاكًا مقطوعة).
    dense = dense_semicolon_lines(atom_py)
    if len(dense) > 3:
        fail("أسلوب مضغوط", "مقروئية",
             f"{len(dense)} سطرًا بعدة أوامر (أولها: {dense[:3]})", SEV_WARN)

    # ── معيار التوقيت: 3 و806 و608 وحدها تقرأ الساعة، والباقي يورّث.
    #    الخرق يمنع ختم النواة (setdefault) ويضع قراءتين على سلسلة واحدة.
    if atom_id not in CLOCK_OWNERS:
        # يُرصد الختم داخل نداء publish فقط. الحالة الداخلية (received_at
        # لقياس تأخير، requested_at لمهلة) وlقطة الذرة مشروعتان: الأولى
        # قياس والثانية ليست حدثًا يمرّ على الناقل.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "publish"):
                continue
            for arg in node.args[1:]:
                for inner in ast.walk(arg):
                    if not isinstance(inner, ast.Dict):
                        continue
                    for key, value in zip(inner.keys, inner.values):
                        if not (isinstance(key, ast.Constant)
                                and key.value == "timestamp"):
                            continue
                        if (isinstance(value, ast.Call)
                                and isinstance(value.func, ast.Attribute)
                                and value.func.attr == "time"):
                            fail("ختم زمني محلي", "م6/ملف5",
                                 f"سطر {inner.lineno}: يختم حدثًا بساعته")

    # ── المادة 16 (ملف 3): المانيفست يطابق الواقع
    declared_pub = set(manifest.get("publishes") or [])
    declared_sub = set(manifest.get("subscribes") or [])
    actual_pub = published_events(tree, source)
    actual_sub = subscribed_events(tree)

    undeclared = actual_pub - declared_pub
    if undeclared:
        fail("مانيفست", "م16/ملف3", f"ينشر بلا إعلان: {sorted(undeclared)}")
    dynamic_sub = "for " in source and "subscribe(" in source
    if not dynamic_sub:
        undeclared_sub = actual_sub - declared_sub
        if undeclared_sub:
            fail("مانيفست", "م16/ملف3",
                 f"يشترك بلا إعلان: {sorted(undeclared_sub)}")

    # ── المادة 8 (ملف 3): timeout_ms < interval_ms
    health = manifest.get("health") or {}
    interval = health.get("interval_ms", 5000)
    timeout = health.get("timeout_ms", 2000)
    if timeout >= interval:
        fail("إعداد الصحة", "م12/ملف3",
             f"timeout_ms={timeout} >= interval_ms={interval}")

    # ── المادة 10 (ملف 3): الاعتماديات موجودة فعلًا
    for dep in manifest.get("dependencies") or []:
        if dep.get("id") not in all_ids:
            fail("اعتمادية", "م10/ملف3", f"تعتمد على ذرة غير موجودة: {dep.get('id')}")

    # ── أمر المالك: atom.py و manifest.yaml كود إنجليزي خام
    # القاعدة تسري على ما يُبنى أو يُعدَّل من الآن، لا رجعيًا على 135 ذرة
    # قائمة. تُفعَّل بـ--strict-language عند بناء ذرة جديدة.
    if strict_language and language_hits:
        fail("لغة", "أمر المالك", "عربي في عقد تنفيذي: " + ", ".join(language_hits))

    # ── دستور الذرة: نسخة واحدة — الكود والمنيفست لا يفترقان (فجوة ٨٧ ذرّة
    #    اكتُشفت 2026-08-12: snapshot() يكتب رقمًا واللوحة تعرض آخر).
    code_version = re.search(r'^ATOM_VERSION\s*=\s*"(\d+\.\d+\.\d+)"', source, re.M)
    declared_version = str(manifest.get("version") or "")
    if code_version and declared_version and code_version.group(1) != declared_version:
        fail("نسخة مزدوجة", "دستور الذرة",
             f"الكود {code_version.group(1)} ≠ المنيفست {declared_version}")

    # ── حدّ الجوع المعلَن: ذرّة تستقبل حدثًا تُعلن كم تصبر بلا غذاء.
    #    السبب مقيس 2026-08-21: فحص الجوع داخل الذرّات يسأل «هل رأيتُ شيئًا
    #    يومًا؟» لا «هل وصلني شيء مؤخّرًا؟». فحين أطعم الإحماءُ المحلّلات مئتَي
    #    شمعة عند الإقلاع، عبرت العتبة مرّة واحدة إلى الأبد؛ وبعد توقّف باني
    #    الشموع بقيت 71 ذرّة متجمّدة عند العدد 200 وكلّها تعلن «سليمة» خضراء.
    #    الحدّ يُعلَن هنا وتقرؤه الحوكمة (idle_limits في governance/server.py)
    #    فتصبغ الذرّة الساكنة فوق حدّها بلون الجوع. والرقم نفسه قرار مالك —
    #    «لا اختراع مدد»: المدقّق يطالب بالإعلان ولا يفرض عددًا.
    if subscribed_events(tree):
        # Campaign 1-449 batch B (2026-08-23): the sealed manifest contract
        # (core/contracts/manifest.py, extra=forbid) REJECTS a top-level
        # max_idle_s -- so the declaration lives in metadata (the free dict
        # the contract allows, and where atom 200 already declared it). The
        # checker accepts either; satisfying the old top-level demand would
        # have broken boot -- a measured contradiction, now closed.
        metadata_block = manifest.get("metadata")
        declared_idle = manifest.get("max_idle_s")
        if declared_idle is None and isinstance(metadata_block, dict):
            declared_idle = metadata_block.get("max_idle_s")
        if declared_idle is None:
            fail("حدّ الجوع", "عقد النضارة",
                 "لا max_idle_s (بالمانيفست أو metadata) — ذرّة تستقبل حدثًا ولا تعلن كم تصبر بلا غذاء",
                 SEV_WARN)
        elif not isinstance(declared_idle, (int, float)) or isinstance(declared_idle, bool) \
                or declared_idle <= 0:
            fail("حدّ الجوع", "عقد النضارة",
                 f"max_idle_s غير صالح ({declared_idle!r}) — يلزم عدد ثوانٍ موجب")

    # ── المادة 10 (ملف 4): وجود الاختبارات، واسم وحدة فريد فيها
    tests = list((directory / "tests").glob("test_*.py")) if (directory / "tests").is_dir() else []
    if not tests:
        fail("اختبارات", "م10/ملف4", "لا ملف اختبار")
    for tp in tests:
        text = tp.read_text(encoding="utf-8")
        if re.search(r'^\s*(from atom import|import atom)\b', text, re.M):
            fail("اسم وحدة عام", "م4/ملف3",
                 f"{tp.name}: 'from atom import' يحمّل ذرة أخرى عند التشغيل الموحّد")


def measure_coverage(directory: Path) -> int | None:
    env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin:/usr/local/bin"}
    rel = directory.relative_to(ROOT)
    try:
        subprocess.run([sys.executable, "-m", "coverage", "run",
                        f"--source={rel}", "-m", "pytest", str(rel), "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, env=env, capture_output=True, timeout=120)
        result = subprocess.run([sys.executable, "-m", "coverage", "report",
                                 "--include", f"{rel}/atom.py"],
                                cwd=ROOT, env=env, capture_output=True,
                                text=True, timeout=60)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    for line in result.stdout.splitlines():
        if "atom.py" in line:
            try:
                return int(line.split()[-1].rstrip("%"))
            except ValueError:
                return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="مدقق الذرات الرسمي")
    parser.add_argument("--atom", type=int, help="رقم ذرة واحدة")
    parser.add_argument("--coverage", action="store_true",
                        help="قياس التغطية (بطيء)")
    parser.add_argument("--json", action="store_true", help="مخرَج آلي")
    parser.add_argument("--strict-language", action="store_true",
                        help="atom.py و manifest.yaml بلا عربي ولا ترويسة")
    parser.add_argument("--no-waivers", action="store_true",
                        help="يفحص الذرات المعفاة أيضًا")
    parser.add_argument("--warn-as-error", action="store_true")
    args = parser.parse_args()

    manifests = sorted(ATOMS.rglob("manifest.yaml"))
    loaded = []
    report = Report()
    for mf in manifests:
        try:
            data = yaml.safe_load(mf.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            report.add(0, mf.parent.name, "مانيفست", "م4/ملف2", SEV_ERROR,
                       f"YAML غير صالح: {exc}")
            continue
        loaded.append((mf.parent, data))

    all_ids = {d["id"] for _, d in loaded}

    # المادة 2 (ملف 3): الهوية فريدة ولا تتكرر
    seen = {}
    for directory, data in loaded:
        aid = data["id"]
        if aid in seen:
            report.add(aid, directory.name, "هوية مكررة", "م2/ملف3", SEV_ERROR,
                       f"الرقم مستعمل أيضًا في {seen[aid]}")
        seen[aid] = directory.name

    for directory, data in loaded:
        if args.atom and data["id"] != args.atom:
            continue
        if data["id"] in WAIVED and not args.no_waivers:
            report.waived.append((data["id"], WAIVED[data["id"]]))
            continue
        report.checked += 1
        check_atom(directory, data, report, all_ids, args.strict_language)
        if args.coverage:
            pct = measure_coverage(directory)
            if pct is None:
                report.add(data["id"], directory.name, "تغطية", "م9/ملف4",
                           SEV_WARN, "تعذّر القياس")
            elif pct < MIN_COVERAGE_PCT:
                report.add(data["id"], directory.name, "تغطية", "م9/ملف4",
                           SEV_ERROR, f"{pct}% < {MIN_COVERAGE_PCT}%")

    if args.json:
        # ٢٠٢٦-٠٩-٠١: التقرير عربيّ، ومخرَج العمليّة على ويندوز يرث ترميز
        # الطرفيّة (cp1256 هنا) لا UTF-8. فكان `print` ينهار بـ
        # `UnicodeEncodeError` كلّما نودي المدقّق من عمليّة فرعيّة — تخرج
        # العمليّة بلا مخرَج، ويسقط فاحصه بـ`JSONDecodeError` وكأن المدقّق
        # لا يعمل. المخرَج المُعلَن JSON يُثبَّت على UTF-8 صراحةً.
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # مخرَج غير قابل للضبط
            pass
        print(json.dumps({
            "checked": report.checked,
            "waived": report.waived,
            "errors": len(report.errors),
            "warnings": len(report.warnings),
            "findings": [f.__dict__ for f in report.findings],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"مدقق الذرات الرسمي — المادة 8\n")
        print(f"  فُحصت {report.checked} ذرة")
        print(f"  مخالفات {len(report.errors)} · تحذيرات {len(report.warnings)}")
        # دَين النضارة يُعلَن برأس التقرير، لا يُدفن بين التحذيرات: عدد الذرّات
        # التي لا تستطيع أن تقول «أنا جائعة» مهما جاعت. ينكمش الرقم كلما أعلن
        # المالك حدًّا، ويبقى في وجهنا حتى يبلغ صفرًا.
        blind = [f for f in report.findings if f.check == "حدّ الجوع"]
        if blind:
            print(f"  🔇 بلا حدّ جوع معلَن: {len(blind)} ذرّة — تقول «سليمة» وهي صائمة")
        for aid, why in report.waived:
            print(f"  معفاة: {aid} — {why}")
        print()
        if report.findings:
            by_check = {}
            for f in report.findings:
                by_check.setdefault((f.severity, f.check, f.article), []).append(f)
            for (sev, check, article), items in sorted(by_check.items()):
                mark = "✗" if sev == SEV_ERROR else "⚠"
                print(f"  {mark} {check} ({article}) — {len(items)}")
                for f in items[:6]:
                    print(f"        {f.atom_id:>4} {f.directory[:30]:<32} {f.detail[:56]}")
                if len(items) > 6:
                    print(f"        … و{len(items) - 6} غيرها")
                print()
        if not report.errors:
            print("  لا مخالفات.")

    failed = report.errors or (args.warn_as_error and report.warnings)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
