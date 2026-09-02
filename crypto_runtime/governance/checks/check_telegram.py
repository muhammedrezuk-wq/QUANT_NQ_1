# -*- coding: utf-8 -*-
"""
فحص منصّة تلغرام (٦١٠) — هل هي مفعّلة ومقترنة وشغّالة، وهل بوّابتها سليمة؟

قراءة وفحص فقط. لا يرسل رسالة، ولا ينفّذ أمرًا، ولا يطبع التوكن أبدًا —
ولا حرفًا منه. كل حاجز يسقط ⇒ FAIL صريح، بلا نتيجة خضراء جزئيّة.
"""
from __future__ import annotations

import ast
import json
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CONF = ROOT / "var" / "governance" / "telegram.json"
SURFACE = ROOT / "governance" / "telegram.py"
LOCK_PORT = 8098
GOV = "http://127.0.0.1:8090"

TOKEN_SHAPE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")
BUY_SELL = re.compile(r"execution\.order|trading\.final_decision|\"BUY\"|\"SELL\"")
CRYPTO = re.compile(r"\bFernet\b|\bderive_key\b|\bcryptography\b|\bKdfParams\b|\.decrypt\(")

failures: list[str] = []
notes: list[str] = []


def check(ok: bool, good: str, bad: str) -> bool:
    print(("  ✓ " if ok else "  ✗ ") + (good if ok else bad))
    if not ok:
        failures.append(bad)
    return ok


def code_only(source: str) -> str:
    """الكود وحده — بلا تعليقات ولا شروح.

    الحارس يفحص ما ينفّذه البرنامج، لا ما يشرحه عن نفسه. بلا هذا كانت جملة
    تشرح أنّ الأوامر «تمرّ من commands.db» تُسقِط الفحص وهي مجرّد كلام.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    return ast.unparse(tree)


def token_really_comes_from_vault(source: str) -> bool:
    """هل التوكن الذي تعمل به المنصّة هو فعلًا مخرَج الخزنة؟

    البحث النصّيّ لا يكفي هنا: كسرٌ يقرأ التوكن من ملفّ نصّ يبقى فيه اسم
    الدالّة (بتعريفها) وتبقى فيه عبارة `conf.get("token")` — فيمرّ الحارس
    وهو أعمى. لذلك نقرأ الشجرة: داخل `main` لازم يُنادى `token_from_vault`،
    ولازم **نفس المتغيّر** الذي أخذ نتيجته هو الذي يُسلَّم لـ`Surface`.
    """
    tree = ast.parse(source)
    main_fn = next((n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    if main_fn is None:
        return False
    from_vault: set[str] = set()
    for node in ast.walk(main_fn):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "token_from_vault"):
            continue
        for target in node.targets:                      # يدعم  token, why = ...
            for name in ([target] if isinstance(target, ast.Name) else
                         getattr(target, "elts", [])):
                if isinstance(name, ast.Name):
                    from_vault.add(name.id)
                    break                                # الأوّل هو التوكن
    if not from_vault:
        return False
    for node in ast.walk(main_fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Surface" and node.args
                and isinstance(node.args[0], ast.Name)):
            return node.args[0].id in from_vault
    return False


def vault_token_state() -> tuple[bool, str]:
    """هل التوكن بالخزنة؟ — نسأل المنصّة نفسها فلا نكرّر منطقًا ولا نكشف قيمة."""
    try:
        sys.path.insert(0, str(ROOT))
        from governance.telegram import token_from_vault
        token, why = token_from_vault()
        return bool(token), (why or "")
    except Exception as exc:  # noqa: BLE001
        return False, "تعذّر سؤال الخزنة (%s)" % type(exc).__name__


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) == 0


def lock_held(port: int) -> bool:
    """هل منفذ القفل محجوز؟ — أصدق من محاولة الاتصال به.

    ٢٠٢٦-٠٨-٣١ (ختم NQ): قفل المنصّة في `telegram.py` سوكِت صامت — يَبِند
    ويستمع بطابور 1 و**لا يقبل أيّ اتصال أبدًا** (وظيفته منع نسخة ثانية لا
    خدمة أحد). فأوّل `connect_ex` يشغل مكان الطابور الوحيد ويبقى معلّقًا،
    وكلّ فحص تالٍ يسقط بالمهلة — فكان الفحص يقول «متوقّفة» والمنصّة تعمل
    (مقاس: المنفذ LISTENING بحوزة العملية والفحص يعلن التوقّف).
    الحجز نفسه هو السؤال الصحيح: إن عجزنا عن الحجز فأحدهم يمسكه.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def main() -> int:
    print("=" * 58)
    print("  فحص منصّة تلغرام (٦١٠)")
    print("=" * 58)

    # ١ — الملفّ المصدر موجود
    print("\n١) المصدر")
    check(SURFACE.is_file(), "ملفّ المنصّة موجود: governance/telegram.py",
          "ملفّ المنصّة مفقود — governance/telegram.py")
    if not SURFACE.is_file():
        return finish()
    source = SURFACE.read_text(encoding="utf-8")
    try:
        code = code_only(source)          # الكود وحده — الشرح ليس سلوكًا
    except SyntaxError as exc:
        check(False, "", "ملفّ المنصّة لا يُقرأ كبايثون: %s" % exc)
        return finish()

    # ٢ — حواجز بنيويّة: لا شراء/بيع، ولا بوّابة ثانية
    print("\n٢) الحواجز البنيويّة")
    check("/gov/command" in code,
          "الأوامر تمرّ من بوّابة اللوحة نفسها (/gov/command → ٩٠١)",
          "لا يستعمل بوّابة اللوحة — طريق أوامر ثانٍ ممنوع")
    check("commands.db" not in code,
          "لا يكتب بجسر الأوامر مباشرة — البوّابة وحدها تكتب",
          "يكتب commands.db مباشرة — التفاف على البوّابة")
    check(not BUY_SELL.search(code),
          "لا أمر شراء/بيع مباشر بالكود — ولا يمكن أن يوجد",
          "وُجد أثر أمر شراء/بيع مباشر — خرق للقاعدة ٣٥")
    check("confirm" in code and "CONFIRM_TTL_S" in code,
          "التأكيد بخطوتين مبنيّ (رمز بمهلة)",
          "لا تأكيد بخطوتين للأوامر الخطِرة")
    check("owner" in code and "ignored" in code,
          "قفل على محادثة المالك وحده، وغيرها يُتجاهل ويُعدّ",
          "لا قفل على المالك — أي محادثة تقدر تأمر")
    check("import core" not in code and "from core" not in code,
          "لا يستورد النواة ولا ذرّة — عزل محفوظ",
          "يستورد النواة/ذرّة — كسر عزل")
    check("mode=ro" in code,
          "جسر التداول يُفتح للقراءة فقط",
          "جسر التداول يُفتح للكتابة — يجب أن يكون قراءة فقط")

    # ٣ — السرّ: بالخزنة المشفّرة، لا بملفّ نصّ ولا بالكود
    print("\n٣) السرّ")
    check(not TOKEN_SHAPE.search(source),   # هنا الملفّ كلّه عمدًا — حتى بالتعليقات
          "لا توكن داخل الكود",
          "🔴 توكن مكتوب داخل الكود — انقله للخزنة فورًا")
    # الممنوع هو فكّ التشفير بيده، لا ذكر اسم أداة الإدارة بسطر إرشاد للمالك.
    check(not CRYPTO.search(code),
          "لا يفكّ التشفير بنفسه — يمرّ من طبقة الأمان الرسميّة",
          "يتعامل مع التشفير مباشرة بدل طبقة الأمان")
    check("FileSecretProvider" in code and "telegram_bot_token" in code,
          "يأخذ التوكن من الخزنة المشفّرة (المفتاح telegram_bot_token)",
          "لا يقرأ التوكن من الخزنة — السرّ بمكان غير محميّ")
    check(token_really_comes_from_vault(source),
          "التوكن الذي تعمل به فعلًا هو مخرَج الخزنة (مقروء من شجرة الكود)",
          "التوكن العامل ليس من الخزنة — مصدره مكان آخر")
    check('conf.get("token")' in code or "conf.get('token')" in code,
          "يرفض أي توكن مكتوب بملفّ الإعداد بدل الخزنة",
          "يقبل توكنًا من ملفّ نصّ — الخزنة تصير زينة")
    check("provider.clear()" in code,
          "يمسح السرّ من الذاكرة بعد أخذه",
          "يترك الخزنة مفتوحة بالذاكرة")

    # ٤ — الخزنة والاقتران
    print("\n٤) الخزنة والاقتران")
    conf = {}
    if CONF.is_file():
        try:
            conf = json.loads(CONF.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            conf = {}
    if not check("token" not in conf,
                 "ملفّ الإعداد بلا أسرار — إعدادات فقط",
                 "🔴 حقل token داخل ملفّ الإعداد — انقله للخزنة واحذفه"):
        pass
    has_token, why = vault_token_state()
    owner = int(conf.get("owner_chat_id") or 0)
    print("  %s الخزنة: %s" % ("✓" if has_token else "○",
                               "فيها telegram_bot_token" if has_token else why))
    print("  %s الاقتران: %s" % ("✓" if owner else "○",
                                 "مقترن بمحادثة المالك" if owner else "لم يقترن بعد"))

    # ٥ — التشغيل
    print("\n٥) التشغيل")
    running = lock_held(LOCK_PORT)
    print("  %s المنصّة: %s" % ("✓" if running else "○",
                                "شغّالة" if running else "متوقّفة"))
    gov_up = False
    try:
        with urllib.request.urlopen(GOV + "/gov/health", timeout=5) as r:
            gov_up = json.loads(r.read().decode("utf-8")).get("status") == "ok"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        gov_up = False
    check(gov_up, "خادم الحوكمة يردّ — مصدر البيانات حاضر",
          "خادم الحوكمة لا يردّ — المنصّة ما رح تشوف شيء")

    # ٦ — الخلاصة بلغة المالك
    print("\n" + "=" * 58)
    if failures:
        print("  ✗ فشل — %d حاجز ساقط:" % len(failures))
        for f in failures:
            print("     • " + f)
        print("=" * 58)
        return 1
    if not has_token:
        print("  ○ الحواجز كلّها سليمة، والمنصّة غير مفعّلة بعد.")
        print("     الناقص: %s" % (why or "توكن بالخزنة"))
        print("     secrets_admin.py init  ثمّ  set telegram_bot_token")
        print("     ثمّ  $env:QUANT_MASTER_KEY = \"pass:<عبارتك>\"")
        print("=" * 58)
        return 0
    if not owner:
        print("  ○ مفعّلة ولم تقترن بعد — شغّل «غرفة القيادة» وأرسل رمز الاقتران")
        print("     الظاهر بنافذة تلغرام (٦١٠) من موبايلك.")
        print("=" * 58)
        return 0
    if not running:
        print("  ○ مفعّلة ومقترنة، لكنّها متوقّفة — شغّل «غرفة القيادة».")
        print("=" * 58)
        return 0
    print("  ✓ سليمة: مفعّلة · مقترنة · شغّالة · وبوّابتها هي بوّابة اللوحة نفسها.")
    print("=" * 58)
    return 0


def finish() -> int:
    print("\n✗ فشل — %d حاجز ساقط" % len(failures))
    return 1


if __name__ == "__main__":
    sys.exit(main())
