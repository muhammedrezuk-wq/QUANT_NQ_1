# -*- coding: utf-8 -*-
"""
غرفة القيادة — تطبيق سطح المكتب القديم (ليس عقد الإطلاق الرسمي).

العقد الرسمي هو زرا الفوركس والكريبتو المستقلان. هذه الأداة باقية للتشخيص
والتوافق فقط. دبل-كليك واحد → يشغّل النواة (:8010) + خادم الحوكمة (:8090) إن ما كانوا شغّالين،
ثم يفتح اللوحة بنافذة تطبيق أصلية (WebView2). عند إغلاق النافذة يوقف ما شغّله هو فقط.

القاعدة (شفافية): سجلّ النواة والخادم يظهر بنافذة هذا المشغّل — لا شيء مخفي.
"""
import ctypes
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # جذر المشروع
# نفرض مفسّر المشروع لتشغيل النواة/الخادم — فيه المكتبات. بايثون النظام يُسقِط النواة.
# ٢٠٢٦-٠٩-٠١: كان يعرف `venv/` وحدها. والنسخة المتنقّلة على جهاز جديد لا تبني
# `venv` أصلًا — تبني `vendor/python/runtime/` (مفسّر المشروع المضمّن). فكان
# المشغّل يسقط إلى `sys.executable`، وهو صحيح حين يأتي عبر `scripts\py.bat`
# وخاطئ تمامًا إن شُغّل بأي طريق آخر: بايثون نظامٍ عارٍ ⇒ لا نواة ولا لوحة.
# الترتيب صريح الآن: مفسّرنا المضمّن، ثم venv، ثم الحالي.
_RUNTIME_PY = ROOT / "vendor" / "python" / "runtime" / "python.exe"
_VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
PYEXE = (str(_RUNTIME_PY) if _RUNTIME_PY.exists()
         else str(_VENV_PY) if _VENV_PY.exists() else sys.executable)
CORE = ROOT / "governance" / "scripts" / "run_core.py"
SERVER = ROOT / "governance" / "server.py"
TELEGRAM = ROOT / "governance" / "telegram.py"          # ٦١٠ — المنصّة المتنقّلة
TELEGRAM_CONF = ROOT / "var" / "governance" / "telegram.json"
CORE_PORT, GOV_PORT, TG_PORT = 8010, 8090, 8098

# ٢٠٢٦-٠٩-٠١: السوق الثاني كان خارج هذا المشغّل، فيقلع الفوركس وحده ويبقى
# الكريبتو واقفًا ما لم يُضغط زرّ آخر — والمالك يريد زرًّا واحدًا يقلع كل شيء.
CRYPTO_CORE = ROOT / "scripts" / "run_crypto.py"
CRYPTO_RUNTIME = ROOT / "crypto_runtime"
GOV_RUNNER = ROOT / "scripts" / "run_governance.py"
CRYPTO_CORE_PORT, CRYPTO_GOV_PORT = 8020, 8091
URL = f"http://127.0.0.1:{GOV_PORT}"

_started: list[subprocess.Popen] = []                  # ما شغّلناه نحن فقط (لإيقافه عند الإغلاق)

# ── الإيقاف الصريح: المسار النظيف أوّلًا (البند ٦٢ · حكم المالك ٢٠٢٦-٠٨-١٥) ──
# كان `--stop` يقتل بـ`taskkill /F` فورًا، فلا تُكتب لقطة قطّ — و**١٧ ذرّة**
# تحفظ لقطتها، فيها إيقاف المالك وتجميده (٥٥٢/٥٥٠/٥١٩) وكتاب الاتجاه (٥٨١)
# ودفاتر الحدود (٥٠٦/٥٠٧/٦٥٨/٦٦٦).
#
# والسبب لم يكن اختيار `taskkill`: كل خدمة تُشغَّل بنافذة خاصّة (أدناه)، وحدث
# الكونسول لا يعبر إلى نافذة أخرى — والاستدعاء المباشر **يرجع True ولا يفعل
# شيئًا** (مقيس). فالإرسال يتمّ من عمليّة ساعية قصيرة العمر، لأنّ الحدث يصيب
# كل من على تلك النافذة ومنهم المُرسِل، و`SetConsoleCtrlHandler(NULL, TRUE)`
# يحجب CTRL+C وحده لا CTRL+BREAK (مقيس: ماتت عمليّتا قياس بـ0xC000013A).
#
# المهلة مشتقّة من القياس لا من الهواء: نواة حقيقيّة ٢١٢ ذرّة، ثمانية إيقافات،
# الإرسال 0.08s والموت 0.53–0.98s (الذيل)، و١٧ لقطة/1162 بايت في كل مرّة، ولا
# يطول بطول العمر (10s/45s/120s ⇒ 0.59/0.67/0.53). فالسقف عشرة أضعاف الذيل:
# الانتظار ثوانيَ زائدة لا يكلّف شيئًا، والقتل المبكّر يفقد اللقطة كلّها.
STOP_TIMEOUT_S = 10.0
_COURIER_TIMEOUT_S = 5.0
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED = 0x1000
_CREATE_NO_WINDOW = 0x08000000
SNAPSHOTS_DIR = ROOT / "var" / "snapshots"

_COURIER_SOURCE = '''
import ctypes, sys, time
from pathlib import Path
pid, flag = int(sys.argv[1]), Path(sys.argv[2])
k32 = ctypes.windll.kernel32
k32.FreeConsole()
if not k32.AttachConsole(pid):
    raise SystemExit(2)
flag.write_text("attached", encoding="utf-8")   # نُعلن الوصول قبل أن تقتلنا إشارتنا
k32.GenerateConsoleCtrlEvent(1, 0)              # CTRL_BREAK لكل من على هذه النافذة
time.sleep(2.0)
'''


def _alive(pid: int) -> bool:
    handle = ctypes.windll.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return bool(ok) and code.value == _STILL_ACTIVE


def _snapshot_state() -> tuple[int, float]:
    try:
        files = list(SNAPSHOTS_DIR.glob("*.json"))
    except OSError:
        return 0, 0.0
    return len(files), max((f.stat().st_mtime for f in files), default=0.0)


def _ring_ctrl_break(pid: int) -> tuple[bool, str]:
    """يوقظ المسار النظيف بالنواة. يرجع (وصل الساعي؟، الشرح)."""
    box = Path(tempfile.mkdtemp(prefix="stop62_"))
    script, flag = box / "courier.py", box / "attached.txt"
    try:
        script.write_text(_COURIER_SOURCE, encoding="utf-8")
        try:
            subprocess.run([PYEXE, str(script), str(pid), str(flag)],
                           creationflags=_CREATE_NO_WINDOW, capture_output=True,
                           timeout=_COURIER_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return flag.exists(), "الساعي تجاوز مهلته"
        if flag.exists():
            return True, ""
        return False, "الساعي لم يصل إلى نافذة العمليّة"
    except OSError as exc:
        return False, "تعذّر تشغيل الساعي: %s" % exc
    finally:
        try:
            for item in box.iterdir():
                item.unlink()
            box.rmdir()
        except OSError:
            pass


def _stop_process(pid: int, name: str) -> dict:
    """إيقاف عمليّة واحدة: النظيف أوّلًا، والقسريّ **معلَنًا** لا صامتًا.

    حكمه الصريح: «لا أقبل جعل `taskkill /F` مجرّد fallback صامت؛ إذا وصلنا إليه
    فهذا يعني أنّ اللقطة النظيفة لم تُضمَن، ويجب أن يظهر ذلك بوضوح في السجلّ».
    """
    started = time.monotonic()
    before_count, before_stamp = _snapshot_state()
    record = {"name": name, "pid": pid, "result": "", "reason": "",
              "waited_s": 0.0, "snapshots": None}
    if not _alive(pid):
        record.update(result="already_gone", reason="العمليّة غير موجودة أصلًا")
        return record

    reached, why = _ring_ctrl_break(pid)
    if reached:
        deadline = time.monotonic() + STOP_TIMEOUT_S
        while time.monotonic() < deadline:
            if not _alive(pid):
                after_count, after_stamp = _snapshot_state()
                record.update(result="clean_stop", waited_s=round(time.monotonic() - started, 2),
                              snapshots="%d ملفّ%s" % (after_count,
                                                       "" if after_stamp <= before_stamp
                                                       else " (كُتبت الآن)"))
                return record
            time.sleep(0.1)
        record["reason"] = ("لم تمت خلال %.0f ثانية — **اللقطة النظيفة غير مضمونة**"
                            % STOP_TIMEOUT_S)
    else:
        record["reason"] = "%s — **اللقطة النظيفة غير مضمونة**" % why

    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    after_count, after_stamp = _snapshot_state()
    record.update(result="forced_stop", waited_s=round(time.monotonic() - started, 2),
                  snapshots="%d ملفّ%s" % (after_count,
                                           "" if after_stamp <= before_stamp else " (كُتبت الآن)"))
    return record


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_port(port: int, name: str, timeout: float = 40.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _port_open(port):
            return True
        time.sleep(0.5)
    print(f"[مشغّل] ⚠ انتهت مهلة انتظار {name} (منفذ {port})", flush=True)
    return False


def _spawn(script: Path, name: str, args: tuple[str, ...] = ()) -> None:
    env = dict(os.environ, PYTHONUTF8="1")
    print(f"[مشغّل] ▶ تشغيل {name} …", flush=True)
    # نافذة أوامر خاصّة لكل خدمة: تبقى حيّة بعد إغلاق نافذة اللوحة، ويبقى
    # سجلّها ظاهرًا للمالك — تشغيل مستمرّ بلا إخفاء.
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
    p = subprocess.Popen([PYEXE, str(script), *args], cwd=str(ROOT), env=env,
                         creationflags=flags)
    _started.append(p)


def _ensure(port: int, script: Path, name: str, args: tuple[str, ...] = ()) -> None:
    if _port_open(port):
        print(f"[مشغّل] ✓ {name} شغّال أصلًا (منفذ {port}) — إعادة استخدامه", flush=True)
        return
    _spawn(script, name, args)
    _wait_port(port, name)


def _ensure_crypto() -> None:
    """السوق الثاني — نواته (:8020) ولوحته (:8091).

    ٢٠٢٦-٠٩-٠١: يُتخطّى بهدوء إن كانت شجرة الكريبتو غير موجودة، فالمشغّل
    لا يسقط لأجل سوق غير منصَّب. وكلّ خدمة بنافذتها كبقيّة الخدمات."""
    if not (CRYPTO_CORE.is_file() and CRYPTO_RUNTIME.is_dir()):
        print("[مشغّل] ○ شجرة الكريبتو غير موجودة — تخطّي", flush=True)
        return
    _ensure(CRYPTO_CORE_PORT, CRYPTO_CORE, "نواة الكريبتو")
    if GOV_RUNNER.is_file():
        _ensure(CRYPTO_GOV_PORT, GOV_RUNNER, "لوحة الكريبتو",
                ("--market", "crypto"))


def _ensure_telegram() -> None:
    """٦١٠ — تشغيل المنصّة المتنقّلة، وفقط إن كان توكنها بخزنة الأسرار.

    بلا توكن لا تُشغَّل أصلًا: منصّة بلا مفتاح ضجيج، لا ميزة. وسطران بالنافذة
    يقولان للمالك ما الناقص بالضبط — لا صمت ولا خطأ مزعج كل إقلاع.
    نسأل المنصّة نفسها عن مصدر توكنها بدل تكرار المنطق هنا: مصدر حكم واحد.
    """
    if _port_open(TG_PORT):
        print("[مشغّل] ✓ تلغرام (٦١٠) شغّال أصلًا — إعادة استخدامه", flush=True)
        return
    ok, why = False, "تعذّر فحص الخزنة"
    try:
        sys.path.insert(0, str(ROOT))
        from governance.telegram import token_from_vault
        token, why = token_from_vault()
        ok = bool(token)
        del token                       # لا يبقى بذاكرة المشغّل
    except Exception as exc:            # noqa: BLE001
        why = str(exc)
    if not ok:
        print(f"[مشغّل] ○ تلغرام (٦١٠) غير مفعّل — {why}", flush=True)
        print("        الخطوات كاملة داخل نافذته، أو بزرّ «فحص منصّة تلغرام» باللوحة.", flush=True)
        return
    _spawn(TELEGRAM, "تلغرام (٦١٠)")


def _ensure_news_bot() -> None:
    """بوت الأخبار (مشروع QUANT_NQ_NEWS المجاور) — من نفس المشغّل.

    أمر المالك ٢٠٢٦-٠٨-٢٠: «خلّي بوت الأخبار يشتغل من بوت لوحة القيادة —
    كلهم من مكان واحد». مشروع مستقلّ ببيئته الخاصّة، فيُشغَّل ببايثونه هو
    وبمجلّده هو — لا نخلط بيئتين.

    بلا توكن لا يُشغَّل: نسخة بلا مفتاح تموت فورًا وتترك نافذة خطأ بلا فائدة.
    والنسخة المكرّرة يمنعها قفل البوت نفسه (`app/single_instance.py`).
    """
    news_root = ROOT.parent / "QUANT_NQ_NEWS"
    news_py = news_root / ".venv" / "Scripts" / "python.exe"
    news_main = news_root / "main.py"
    if not (news_main.is_file() and news_py.exists()):
        print("[مشغّل] ○ بوت الأخبار غير موجود — تخطّي", flush=True)
        return
    env_file = news_root / ".env"
    if not env_file.is_file():
        print("[مشغّل] ○ بوت الأخبار: ما في ملفّ .env — لم يُشغَّل", flush=True)
        print("        افتح «بوت الأخبار.bat» بمجلّده مرّة واحدة لتحطّ التوكن.", flush=True)
        return
    try:
        raw = env_file.read_text(encoding="utf-8", errors="ignore")
        line = next((l for l in raw.splitlines() if l.startswith("BOT_TOKEN=")), "")
        token_len = len(line.split("=", 1)[1].strip()) if "=" in line else 0
    except OSError:
        token_len = 0
    if token_len < 20:
        print("[مشغّل] ○ بوت الأخبار: التوكن غير مضبوط داخل .env — لم يُشغَّل", flush=True)
        return
    print("[مشغّل] ▶ تشغيل بوت الأخبار …", flush=True)
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
    p = subprocess.Popen([str(news_py), str(news_main)], cwd=str(news_root),
                         env=dict(os.environ, PYTHONUTF8="1"), creationflags=flags)
    _started.append(p)


def _shutdown() -> None:
    """إغلاق النافذة يغلق النافذة وحدها. النواة تبقى تعمل.

    كان هنا terminate() لكل ما شُغّل، فكان إغلاق اللوحة يقتل النواة ومعها
    التداول والمراقبة — والمالك لا يقصد الإطفاء حين يغلق نافذة. النظام
    الدائم لا يُوقفه إغلاق شاشة؛ الإيقاف صار فعلًا صريحًا وحده.
    """
    if _started:
        print("[مشغّل] أُغلقت النافذة — النواة وخادم الحوكمة ما زالا يعملان.", flush=True)
        print("[مشغّل] لإيقافهما صراحةً:  غرفة القيادة.bat --stop", flush=True)


def _stop_all() -> int:
    """إيقاف صريح بأمر المالك — لا يقع أبدًا كأثر جانبيّ لإغلاق نافذة."""
    import urllib.request
    stopped = []
    for port, name in ((TG_PORT, "تلغرام (٦١٠)"), (GOV_PORT, "خادم الحوكمة"), (CORE_PORT, "النواة")):
        if not _port_open(port):
            print(f"[مشغّل] {name} متوقّف أصلًا (منفذ {port}).", flush=True)
            continue
        seen = set()
        for line in subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                                   text=True, shell=False).stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
                seen.add(int(parts[4]))
        if not seen:
            print(f"[مشغّل] {name}: ما لقيت عمليّة تستمع على {port}.", flush=True)
            stopped.append(f"{name} (تعذّر)")
            continue
        for pid in sorted(seen):
            record = _stop_process(pid, name)
            mark = "🟢" if record["result"] == "clean_stop" else (
                "⚪" if record["result"] == "already_gone" else "🔴")
            print("[مشغّل] %s %s · %s · PID %d · انتظار %.2fs · لقطات: %s%s"
                  % (mark, name, record["result"], record["pid"], record["waited_s"],
                     record["snapshots"] or "—",
                     ("" if not record["reason"] else " · السبب: " + record["reason"])),
                  flush=True)
            stopped.append("%s %s" % (name, record["result"]))
    print("[مشغّل] أُوقف: " + (" · ".join(stopped) if stopped else "لا شيء"), flush=True)
    return 0


def main() -> None:
    if os.name == "nt":
        os.system("chcp 65001 >nul")        # عرض العربي بنافذة الأوامر (من بايثون لتفادي لغبطة الـbat)
    if "--stop" in sys.argv:
        raise SystemExit(_stop_all())
    # قفل نسخة واحدة: لو التطبيق شغّال مسبقًا، لا نفتح نسخة ثانية (يمنع التكرار عند الدبل-كليك المزدوج)
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", 8099))
        lock.listen(1)
    except OSError:
        print("[مشغّل] التطبيق شغّال مسبقًا — لن تُفتح نسخة ثانية.", flush=True)
        return
    print("=" * 54, flush=True)
    print("  غرفة القيادة — QUANT_NQ  (تطبيق سطح المكتب)", flush=True)
    print("=" * 54, flush=True)
    _ensure(CORE_PORT, CORE, "النواة")
    _ensure(GOV_PORT, SERVER, "خادم الحوكمة")
    _ensure_crypto()
    _ensure_telegram()
    _ensure_news_bot()

    try:
        import webview  # pywebview
    except Exception as e:  # noqa: BLE001
        print(f"[مشغّل] ✗ pywebview غير متاح: {e}", flush=True)
        print(f"        افتح يدويًّا: {URL}", flush=True)
        input("اضغط Enter للخروج…")
        _shutdown()
        return

    # الأداة القديمة تعرض نافذتين مستقلتين بدل دمج السوقين. هذا السلوك
    # للتشخيص فقط؛ عقد الإطلاق الرسمي هو زرا السوق المنفصلان.
    stamp = int(time.time())        # رابط فريد كل تشغيل → يتخطّى أي نسخة WebView مخزّنة
    windows = [("غرفة القيادة — الفوركس", f"{URL}/?v={stamp}")]
    if _port_open(CRYPTO_GOV_PORT):
        windows.append(("غرفة القيادة — الكريبتو",
                        f"http://127.0.0.1:{CRYPTO_GOV_PORT}/?v={stamp}"))
    else:
        print("[مشغّل] ○ لوحة الكريبتو غير متاحة — تُفتح نافذة الفوركس وحدها",
              flush=True)
    for title, win_url in windows:
        print(f"[مشغّل] ✓ فتح النافذة على {win_url}", flush=True)
        webview.create_window(
            title,
            win_url,
            width=1600, height=950, min_size=(1024, 640),
            background_color="#0d1119",
            text_select=True,  # المكتبة بتعطّل تحديد النص افتراضيًّا — المالك بدو يحدّد وينسخ (خطأ/رقم/سجل)
        )
    try:
        webview.start()          # يحجب حتى إغلاق النافذة
    except Exception as exc:     # noqa: BLE001
        # ٢٠٢٦-٠٩-٠١: كان الفشل هنا يسقط في `finally` فيُوقف كل الخدمات
        # ويخرج المشغّل **بلا نافذة وبلا كلمة**. أشهر سببٍ على جهاز جديد:
        # WebView2 Runtime غير منصَّب — `pywebview` يستورد بنجاح ثم يفشل عند
        # فتح النافذة. حارسٌ لا يقول لماذا سقط ليس حارسًا: يُشرح السبب،
        # وتبقى الخدمات شغّالة، ويُفتح المتصفّح بدل النافذة فلا يضيع اليوم.
        print("=" * 62, flush=True)
        print(f"[مشغّل] ✗ تعذّر فتح نافذة سطح المكتب: {exc}", flush=True)
        print("        الأرجح أن WebView2 Runtime غير منصَّب على هذا الجهاز.", flush=True)
        print("        نزّله من موقع مايكروسوفت باسم:", flush=True)
        print("          Microsoft Edge WebView2 Runtime (Evergreen Standalone)", flush=True)
        print("        والخدمات تعمل الآن، فتُفتح اللوحتان بالمتصفّح بدلها:", flush=True)
        for _title, _url in windows:
            print(f"          {_url}", flush=True)
        try:
            import webbrowser
            for _title, _url in windows:
                webbrowser.open(_url)
        except Exception:  # noqa: BLE001 — المتصفّح ترفٌ لا شرط
            pass
        print("=" * 62, flush=True)
        input("اضغط Enter لإيقاف الخدمات والخروج…")
        _shutdown()
        return
    _shutdown()


if __name__ == "__main__":
    main()
