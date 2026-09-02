# -*- coding: utf-8 -*-
"""
تنصيب المشروع على جهاز جديد — بيئة مِلكنا داخل مجلّدنا.
=========================================================
٢٠٢٦-٠٩-٠١ (حكم المالك: «لازم نركب على جهاز نستغلّ موارده ونشتغل بدون ما
حدا يزعجنا — إذا ثبّت تطبيق، حدّث مكتبة، غيّر بايثون، ما يخرب بيتنا»).

المشكلة التي بدأت السلسلة: نسخة أُرسلت وفُتحت عند صاحبها ولم تفتح على جهاز
آخر. والسبب مقيس: النسخة تحمل الشيفرة واللوحتين المبنيّتين، ولا تحمل بيئة
بايثون — و`scripts\\py.bat` عند غيابها يرجع إلى بايثون النظام العاري، فيسقط
أوّل `import fastapi` ولا تقلع خدمة ولا تفتح لوحة، بلا رسالة مفهومة. ولم
يكن في المشروع مسار تنصيب أوّل مرّة إطلاقًا.

والعلاج ليس «نصّب الحزم» — بل **العزل الكامل بثلاث طبقات**:

  ١) مفسّرنا نحن: `vendor/python/runtime/` — توزيعة بايثون مضمّنة تُفكّ داخل
     المشروع. لا نعتمد على بايثون الجهاز أصلًا، فترقيته أو حذفه أو تعدّد
     نسخه لا يمسّنا. (`python312._pth` يضمّ جذر المشروع فتُستورد `core`
     و`clock` من أي مجلّد عمل.)

  ٢) نسخ مقفولة: `requirements.lock.txt` — نسخة مثبّتة بالضبط لكل حزمة،
     مباشرة وغير مباشرة. `requirements.txt` يقول *ماذا* نحتاج بحدود دنيا
     (`>=`)، وذلك يعني أن جهازين يُنصَّبان بيومين مختلفين يحصلان على
     مكتبتين مختلفتين — وتحديث طرف ثالث يكسر النظام بلا أن تتغيّر شيفرتنا.

  ٣) مخزن محليّ: `vendor/wheels/` — ملفّات الحزم نفسها بجانبنا. التنصيب
     يجري بـ`--no-index`، أي **بلا إنترنت وبلا PyPI**: ما نُصِّب اليوم هو
     نفسه ما يُنصَّب بعد سنة.

يُشغَّل هذا السكربت بمفسّرنا (الزرّ يفكّه أوّلًا إن لزم). ولو شُغّل ببايثون
الجهاز فسيعمل أيضًا، لكنه يقول ذلك صراحةً بدل أن يصمت.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
WHEELS = VENDOR / "wheels"
RUNTIME = VENDOR / "python" / "runtime"
RUNTIME_PY = RUNTIME / "python.exe"
GET_PIP = VENDOR / "python" / "get-pip.py"
LOCK = ROOT / "requirements.lock.txt"
REQS = ROOT / "requirements.txt"

VAR_DIRS = ("alerts", "audit", "backups", "crypto", "forex", "governance",
            "logs", "reconciliation", "snapshots", "store", "telemetry")

#: نسخة بايثون التي بُنيت لها عجلات `vendor/wheels` (cp312). عجلة cp312 لا
#: تُنصَّب على 3.13 — فالتحقّق صريح بدل فشلٍ غامض في منتصف التنصيب.
PINNED_PY = (3, 12)

_ok = True

# ٢٠٢٦-٠٩-٠١ (مقيس على نسخة نظيفة): مخرَج العمليّة على ويندوز يرث ترميز
# الطرفيّة (cp1256 هنا)، فأوّل رسالة عربيّة تُسقط السكربت بـ
# `UnicodeEncodeError` — أي أنّ أداة التنصيب تموت وهي تشرح ما تفعل. لا
# نعتمد على `chcp` ولا على متغيّر بيئة قد لا يصل: المخرَج يُثبَّت هنا.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def say(mark: str, text: str) -> None:
    print(f"  {mark} {text}", flush=True)


def fail(text: str) -> None:
    global _ok
    _ok = False
    say("✗", text)


def step(n: int, title: str) -> None:
    print(f"\n[{n}/6] {title}", flush=True)


#: التوزيعة المضمّنة تُنزَّل عند أوّل تنصيب ولا تُشحن داخل النسخة.
#: حكم المالك ٢٠٢٦-٠٩-٠١: «لازم ما يتجاوز ٢٠ ميغا — لازم يطلب تحميل أو
#: يحمّل بعد الفكّ، مو لازم ينتقل معه». فالنسخة تبقى ١٣ م.ب، والعزل لا
#: يُمَسّ: النسخة المنزَّلة **مثبّتة ومُتحقَّق منها ببصمة**، والحزم تُنصَّب
#: من `requirements.lock.txt` بنسخها المقفولة لا بأحدث ما على PyPI.
EMBED_URL = ("https://www.python.org/ftp/python/3.12.10/"
             "python-3.12.10-embed-amd64.zip")
EMBED_SHA = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
EMBED_ZIP = VENDOR / "python" / "python-3.12.10-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

_PTH_LINES = ["python312.zip", ".", r"Lib\site-packages", r"..\..\..", "import site"]


def _download(url: str, dst: Path, sha256: str | None = None) -> bool:
    """ينزّل ملفًّا ويتحقّق من بصمته إن أُعطيت. يعيد True عند النجاح."""
    import hashlib
    import urllib.request
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        say("·", f"تنزيل {dst.name} …")
        urllib.request.urlretrieve(url, dst)
    except Exception as exc:  # noqa: BLE001 — الشبكة خارجة عن سيطرتنا
        say("✗", f"تعذّر التنزيل ({type(exc).__name__}). تحقّق من الإنترنت.")
        return False
    if sha256:
        got = hashlib.sha256(dst.read_bytes()).hexdigest()
        if got != sha256:
            say("✗", "بصمة الملفّ المنزَّل لا تطابق المتوقَّع — أُلغي.")
            try:
                dst.unlink()
            except OSError:
                pass
            return False
        say("✓", "البصمة مطابقة")
    return True


def _fix_path_file() -> bool:
    """يضبط `pythonXY._pth` للتوزيعة المضمّنة. يعيد True إن غُيّر فعلًا.

    محتوى التوزيعة الافتراضي يُبقي `import site` معطّلًا ولا يضمّ جذر
    المشروع، فلا تُرى الحزم المنصَّبة ولا تُستورد `core`/`clock`. الحارس:
    لا نلمس الملفّ إن كان مضبوطًا سلفًا، ولا نعيد التشغيل أكثر من مرّة."""
    if os.environ.get("NQ_SETUP_REEXEC"):
        return False
    want = "\n".join(_PTH_LINES) + "\n"
    for pth in RUNTIME.glob("python*._pth"):
        try:
            if pth.read_text(encoding="ascii", errors="replace") == want:
                return False
            pth.write_text(want, encoding="ascii", newline="\n")
            return True
        except OSError:
            return False
    return False


def _build_runtime() -> int | None:
    """يجهّز مفسّر المشروع ثم يسلّمه بقيّة التنصيب.

    يعيد رمز خروج العمليّة الجديدة عند النجاح، أو None إن تعذّر — فيكمل
    المستدعي ببايثون الجهاز بدل أن يقف."""
    import zipfile
    if not RUNTIME_PY.exists():
        if not EMBED_ZIP.is_file():
            say("·", "مفسّر المشروع غير موجود — يُجلب مرّة واحدة "
                     "(‏١١ م.ب تقريبًا).")
            if not _download(EMBED_URL, EMBED_ZIP, EMBED_SHA):
                return None
        try:
            with zipfile.ZipFile(EMBED_ZIP) as z:
                z.extractall(RUNTIME)
        except Exception as exc:  # noqa: BLE001
            say("✗", f"تعذّر فكّ المفسّر: {type(exc).__name__}")
            return None
        if not RUNTIME_PY.exists():
            say("✗", "فُكّ الأرشيف ولم يظهر python.exe — نسخة غير متوقّعة")
            return None
        say("✓", f"جُهّز مفسّر المشروع: {RUNTIME_PY}")
    _fix_path_file()
    if not GET_PIP.is_file():
        _download(GET_PIP_URL, GET_PIP)
    say("·", "تسليم بقيّة التنصيب لمفسّر المشروع…")
    env = dict(os.environ, NQ_SETUP_REEXEC="1",
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return subprocess.run([str(RUNTIME_PY), __file__], env=env).returncode


def _has_pip() -> bool:
    r = subprocess.run([sys.executable, "-m", "pip", "--version"],
                       capture_output=True)
    return r.returncode == 0


def main() -> int:
    if os.name == "nt":
        os.system("chcp 65001 >nul")
    print("=" * 64)
    print("  تنصيب QUANT_NQ — بيئة مِلك المشروع داخل مجلّده")
    print("=" * 64)

    v = sys.version_info
    ours = RUNTIME_PY.exists() and Path(sys.executable).resolve() == RUNTIME_PY.resolve()

    # ١) المفسّر
    step(1, "المفسّر")
    say("·", f"{v.major}.{v.minor}.{v.micro} — {sys.executable}")
    if ours and _fix_path_file():
        # التوزيعة المضمّنة تأتي بملفّ مسارٍ مغلق: لا `import site` ولا جذر
        # المشروع — فلا تُرى `Lib\site-packages` ولا تُستورد `core`. ضُبط
        # الملفّ الآن، وهو يُقرأ عند **بدء** المفسّر فقط، فنعيد تشغيل أنفسنا
        # مرّة واحدة بالمفسّر نفسه (بحارس بيئة يمنع أي دورة ثانية).
        say("·", "ضُبط مسار البحث الداخلي — إعادة تشغيل بالمفسّر نفسه…")
        env = dict(os.environ, NQ_SETUP_REEXEC="1")
        return subprocess.run([sys.executable, __file__], env=env).returncode
    if ours:
        say("✓", "مفسّر المشروع نفسه — معزول عن بايثون الجهاز تمامًا")
    elif not os.environ.get("NQ_SETUP_REEXEC"):
        # نعمل الآن ببايثون الجهاز — وهذا مؤقّت بحكم التصميم: نجلب مفسّرنا
        # ثم نُسلّمه الشغل. النسخة المتنقّلة لا تحمله (حكم ٢٠ ميغا)، فيُنزَّل
        # مرّة واحدة ويُتحقَّق من بصمته، ويبقى داخل المجلّد بعدها.
        rc = _build_runtime()
        if rc is not None:
            return rc
        say("⚠", "تعذّر تجهيز مفسّر المشروع — نكمل ببايثون الجهاز هذه المرّة.")
    if (v.major, v.minor) < PINNED_PY:
        fail(f"مطلوب بايثون {PINNED_PY[0]}.{PINNED_PY[1]} فأحدث")
        return _finish()

    # ٢) pip داخل مفسّرنا
    step(2, "أداة التنصيب (pip)")
    if _has_pip():
        say("✓", "موجودة")
    elif GET_PIP.is_file():
        say("·", "تركيبها من نسخة المشروع…")
        r = subprocess.run([sys.executable, str(GET_PIP), "-q",
                            "--no-warn-script-location"])
        if r.returncode != 0 or not _has_pip():
            fail("تعذّر تركيب pip")
            return _finish()
        say("✓", "رُكّبت")
    else:
        fail("pip غير موجودة و get-pip.py مفقود من النسخة")
        return _finish()

    # ٣) الحزم — من مخزننا، بنسخ مقفولة، بلا إنترنت
    step(3, "الحزم")
    wheels = list(WHEELS.glob("*.whl")) if WHEELS.is_dir() else []
    offline = bool(wheels) and LOCK.is_file() and (v.major, v.minor) == PINNED_PY
    if offline:
        say("·", f"من مخزن المشروع: {len(wheels)} حزمة بنسخ مقفولة (بلا إنترنت)")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                            "--no-index", "--find-links", str(WHEELS),
                            "--prefer-binary", "-r", str(LOCK),
                            "--no-warn-script-location"])
    else:
        if wheels and (v.major, v.minor) != PINNED_PY:
            say("⚠", f"المخزن مبنيّ لبايثون {PINNED_PY[0]}.{PINNED_PY[1]} "
                     f"وأنت على {v.major}.{v.minor} — سيُنصَّب من الإنترنت.")
        elif not wheels:
            # ليس تحذيرًا من اختلاف النسخ: القفل يفرض النسخة نفسها. المتغيّر
            # هو المصدر فقط — من الإنترنت بدل مخزن محليّ.
            say("·", "لا مخزن محليّ — تُجلب النسخ المقفولة نفسها من الإنترنت.")
        source = LOCK if LOCK.is_file() else REQS
        if not source.is_file():
            fail("لا requirements.lock.txt ولا requirements.txt في النسخة")
            return _finish()
        r = subprocess.run([sys.executable, "-m", "pip", "install",
                            "-r", str(source), "--no-warn-script-location"])
    if r.returncode != 0:
        say("⚠", "بعض الحزم لم تُنصَّب — الفحص التالي يقول أيّها فعلًا ناقص.")
    missing = _check_packages()
    if missing:
        fail("حزم ناقصة: " + ", ".join(missing))
    else:
        say("✓", "كل الحزم المطلوبة موجودة")

    # ٤) مجلّدات التشغيل
    step(4, "مجلّدات التشغيل (var)")
    made = 0
    for d in VAR_DIRS:
        p = ROOT / "var" / d
        if not p.is_dir():
            p.mkdir(parents=True, exist_ok=True)
            made += 1
    say("✓", f"جاهزة ({made} أُنشئ حديثًا من {len(VAR_DIRS)})")

    # ٥) روابط الذرّات
    step(5, "روابط الذرّات المشتركة")
    prep = ROOT / "scripts" / "prepare_unified.py"
    if prep.is_file():
        r = subprocess.run([sys.executable, str(prep)], cwd=str(ROOT))
        say("✓" if r.returncode == 0 else "⚠",
            "تمّت" if r.returncode == 0 else f"رجعت برمز {r.returncode}")
    else:
        say("⚠", "prepare_unified.py مفقود — تُخطّى")

    # ٦) اللوحتان
    step(6, "اللوحتان المبنيّتان")
    for rel, name in (("governance/ui/built/index.html", "الفوركس"),
                      ("crypto_runtime/governance/ui/built/index.html", "الكريبتو")):
        if (ROOT / rel).is_file():
            say("✓", f"لوحة {name} موجودة")
        else:
            fail(f"لوحة {name} مفقودة ({rel}) — النسخة ناقصة")

    return _finish()


def _check_packages() -> list[str]:
    code = (
        "import importlib.metadata as m\n"
        "req=['fastapi','uvicorn','pydantic','jsonschema','packaging','PyYAML',"
        "'psutil','python-dotenv','requests','websockets','pywebview','cryptography',"
        "'argon2-cffi','pytest','pytest-asyncio','deep-translator']\n"
        "miss=[]\n"
        "for r in req:\n"
        "    try: m.version(r)\n"
        "    except Exception: miss.append(r)\n"
        "print(','.join(miss))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return [x for x in (r.stdout or "").strip().split(",") if x]


def _finish() -> int:
    print()
    print("=" * 64)
    if _ok:
        print("  تمّ. افتح زر السوق المطلوب:")
        print("    أزرار التشغيل\\تشغيل الفوركس الموحد.bat")
        print("    أزرار التشغيل\\تشغيل الكريبتو الموحد.bat")
    else:
        print("  لم يكتمل — اقرأ السطور المعلَّمة ✗ أعلاه.")
    print("=" * 64)
    return 0 if _ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
