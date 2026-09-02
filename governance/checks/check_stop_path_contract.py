"""Contract guard for problem 62 — an explicit stop must try the CLEAN path
first, and must say out loud when it could not.

Owner's ruling 2026-08-15, verbatim:

    "(b).  But before implementing, we do not put a timeout out of thin air."
    "1. guard first on the current app.py: prove --stop uses taskkill /F, prove
     the snapshot is not written, prove the proposed courier path really does
     stop it cleanly, and prove a failed courier does not leave the process
     hanging forever."
    "3. the change is in app.py only: --stop -> the CTRL_BREAK courier; wait for
     the core to die up to the derived timeout; clean death = success; timeout
     or courier failure = taskkill /F as a last resort."
    "4. the log must say: clean_stop, forced_stop, the reason for falling to the
     forced path, the PID, the waited time, and the snapshot result."
    "I do not accept taskkill /F as a silent fallback; reaching it means the
     clean snapshot was NOT guaranteed, and that must appear clearly in the log."

Where the number came from (measured, not chosen).  A real 212-atom core booted
in an isolated copy, stopped through the courier, five rounds plus three
different uptimes:

    courier send            0.08s   (stable across all 8 stops)
    send -> process death   0.53s .. 0.98s      <- tail
    snapshots written       17 files, 1162 bytes, EVERY time, exit code 0
    uptime 10s/45s/120s     0.59 / 0.67 / 0.53  -> teardown does not grow

The cost is asymmetric: waiting a few extra seconds when something is stuck
costs nothing, while killing early loses 17 atoms' state including the owner's
own halt and freeze.  So the ceiling is ten times the measured tail.

And two platform facts measured the hard way, both of which the design depends
on:
  * CTRL_BREAK sent to a process on its OWN console returns True and does
    NOTHING -- the API lies, so app.py cannot simply send the event itself.
  * The event reaches every process on that console INCLUDING the sender, and
    SetConsoleCtrlHandler(NULL, TRUE) masks CTRL+C only -- two probe processes
    died with 0xC000013A before the courier was made a separate process.

Exit 1 on any divergence.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

APP = ROOT / "governance" / "app.py"
RUN_CORE = ROOT / "governance" / "scripts" / "run_core.py"

TAIL_S = 0.98          # أطول إيقاف نظيف مقيس
TIMEOUT_S = 10.0       # السقف المشتقّ: عشرة أضعاف الذيل
NEW_CONSOLE = 0x00000010
# CREATE_NO_WINDOW يعطي نافذة **مخفيّة** لا انعدامها، فالساعي يصل إليها بحقّ
# (مقيس: أعطى clean_stop). الهدف الذي لا نافذة له إطلاقًا هو DETACHED_PROCESS.
DETACHED = 0x00000008

# ابن يتصرّف كالنواة: يثبّت مقابض ٥-٢ ويكتب علامة عند الإيقاف النظيف وحده.
OBEDIENT = """
import signal, sys, time
from pathlib import Path
marker = Path(sys.argv[1])
def clean(signum, _f):
    marker.write_text("clean", encoding="utf-8")
    sys.exit(0)
for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
    sig = getattr(signal, name, None)
    if sig is not None:
        try: signal.signal(sig, clean)
        except Exception: pass
Path(str(marker) + ".ready").write_text("1", encoding="utf-8")
while True: time.sleep(0.2)
"""

# ابن عنيد: يتجاهل الإشارة تمامًا — يمثّل نواة معلّقة لا تموت.
STUBBORN = """
import ctypes, signal, sys, time
from pathlib import Path
marker = Path(sys.argv[1])
ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)
for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
    sig = getattr(signal, name, None)
    if sig is not None:
        try: signal.signal(sig, signal.SIG_IGN)
        except Exception: pass
Path(str(marker) + ".ready").write_text("1", encoding="utf-8")
while True: time.sleep(0.2)
"""


def load_app():
    spec = importlib.util.spec_from_file_location("_c62_app", APP)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Child:
    """عمليّة حقيقيّة بنافذة خاصّة — كما يشغّل app.py النواة بالضبط."""

    def __init__(self, source: str, flags: int = NEW_CONSOLE):
        self.dir = Path(tempfile.mkdtemp(prefix="c62_"))
        script = self.dir / "child.py"
        script.write_text(source, encoding="utf-8")
        self.marker = self.dir / "clean.txt"
        self.proc = subprocess.Popen([sys.executable, str(script), str(self.marker)],
                                     creationflags=flags)
        for _ in range(80):
            if (self.dir / "clean.txt.ready").exists():
                break
            time.sleep(0.1)

    @property
    def clean(self) -> bool:
        return self.marker.exists()

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)


def structural(app) -> int:
    print("=" * 94)
    print("أ) الحواجز البنيويّة — المسار النظيف أوّلًا، والقسريّ معلَن")
    print("=" * 94)
    bad = 0
    src = APP.read_text(encoding="utf-8")
    checks = (
        ("دالّة إيقاف عمليّة واحدة قابلة للفحص", hasattr(app, "_stop_process")),
        ("مهلة معلَنة بالكود", hasattr(app, "STOP_TIMEOUT_S")),
        ("وقيمتها %.0f ثانية" % TIMEOUT_S, getattr(app, "STOP_TIMEOUT_S", None) == TIMEOUT_S),
        ("ساعي CTRL_BREAK موجود", "AttachConsole" in src and "GenerateConsoleCtrlEvent" in src),
        ("الساعي عمليّة منفصلة", "_COURIER" in src or "courier" in src.lower()),
        ("taskkill ما زال ملاذًا أخيرًا", "taskkill" in src),
        ("سجلّ صريح: clean_stop", "clean_stop" in src),
        ("سجلّ صريح: forced_stop", "forced_stop" in src),
        ("وسبب الانتقال للقسريّ", "reason" in src.split("_stop_process")[-1][:2500]),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))

    # النواة ومسار إيقافها النظيف ممنوعان من المسّ في هذه الجولة.
    anchor = "def _request_stop_threadsafe(_signum: int, _frame: object) -> None:"
    ok = anchor in RUN_CORE.read_text(encoding="utf-8")
    bad += 0 if ok else 1
    print("      %-38s %s" % ("run_core.py لم يُمَسّ (مرساة ٥-٢)", "✓" if ok else "✗"))
    return bad


def behavioural(app) -> int:
    print("\n" + "=" * 94)
    print("ب) على عمليّات حقيقيّة — والقسريّ لا يقع صامتًا أبدًا")
    print("=" * 94)
    bad = 0
    calls: list[list[str]] = []
    real_run = app.subprocess.run

    def spy(args, *rest, **kw):
        calls.append(list(args) if isinstance(args, (list, tuple)) else [str(args)])
        return real_run(args, *rest, **kw)

    app.subprocess.run = spy
    try:
        print("  ١· الساعي ينجح ⇒ إيقاف نظيف، ولا taskkill إطلاقًا:")
        calls.clear()
        child = Child(OBEDIENT)
        started = time.monotonic()
        report = app._stop_process(child.proc.pid, "محكّ")
        elapsed = time.monotonic() - started
        forced = any("taskkill" in " ".join(c) for c in calls)
        ok = (child.clean and report.get("result") == "clean_stop"
              and not forced and elapsed < TIMEOUT_S)
        bad += 0 if ok else 1
        print("      نظيف=%s · النتيجة=%s · taskkill=%s · %.2fs  %s"
              % (child.clean, report.get("result"), forced, elapsed, "✓" if ok else "✗"))
        print("      السجلّ: %s" % report)
        child.close()

        print("\n  ٢· الساعي يفشل (هدف غير موجود) ⇒ يصل القسريّ ويقول لماذا:")
        calls.clear()
        dead = Child(OBEDIENT)
        pid = dead.proc.pid
        dead.proc.kill()
        dead.proc.wait(timeout=10)
        report = app._stop_process(pid, "محكّ")
        ok = report.get("result") in ("forced_stop", "already_gone") and bool(report.get("reason"))
        bad += 0 if ok else 1
        print("      النتيجة=%s · السبب=%s  %s"
              % (report.get("result"), report.get("reason"), "✓" if ok else "✗"))
        dead.close()

        print("\n  ٢ب· الساعي يفشل والهدف حيّ ⇒ قسريّ فورًا بسببه، بلا انتظار المهلة:")
        calls.clear()
        live = Child(OBEDIENT)
        original = app._COURIER_SOURCE
        app._COURIER_SOURCE = "import sys\nraise SystemExit(2)\n"   # ساعٍ لا يصل
        started = time.monotonic()
        try:
            report = app._stop_process(live.proc.pid, "محكّ")
        finally:
            app._COURIER_SOURCE = original
        elapsed = time.monotonic() - started
        forced = any("taskkill" in " ".join(c) for c in calls)
        ok = (report.get("result") == "forced_stop" and forced
              and elapsed < TIMEOUT_S / 2 and "غير مضمونة" in str(report.get("reason")))
        bad += 0 if ok else 1
        print("      النتيجة=%s · taskkill=%s · %.2fs (بلا انتظار المهلة) · السبب=%s  %s"
              % (report.get("result"), forced, elapsed, report.get("reason"),
                 "✓" if ok else "✗"))
        live.close()

        print("\n  ٢ج· الساعي الحقيقيّ ضدّ هدف بلا نافذة ⇒ علامة الوصول تعني الوصول فعلًا:")
        calls.clear()
        headless = Child(OBEDIENT, flags=DETACHED)    # بلا كونسول ⇒ AttachConsole يفشل
        started = time.monotonic()
        report = app._stop_process(headless.proc.pid, "محكّ")
        elapsed = time.monotonic() - started
        forced = any("taskkill" in " ".join(c) for c in calls)
        # لو كُتبت العلامة قبل نجاح الوصول، لانتظرنا المهلة كاملة بلا داعٍ.
        ok = (report.get("result") == "forced_stop" and forced and elapsed < TIMEOUT_S / 2
              and "غير مضمونة" in str(report.get("reason")))
        bad += 0 if ok else 1
        print("      النتيجة=%s · %.2fs · السبب=%s  %s"
              % (report.get("result"), elapsed, report.get("reason"), "✓" if ok else "✗"))
        headless.close()

        print("\n  ٣· نواة عنيدة لا تموت ⇒ لا تعليق أبديّ، والقسريّ معلَن:")
        calls.clear()
        stubborn = Child(STUBBORN)
        started = time.monotonic()
        report = app._stop_process(stubborn.proc.pid, "محكّ")
        elapsed = time.monotonic() - started
        forced = any("taskkill" in " ".join(c) for c in calls)
        gone = stubborn.proc.poll() is not None
        ok = (report.get("result") == "forced_stop" and forced and gone
              and TIMEOUT_S <= elapsed < TIMEOUT_S + 8.0
              and not stubborn.clean and bool(report.get("reason")))
        bad += 0 if ok else 1
        print("      النتيجة=%s · قُتل قسرًا=%s · ماتت=%s · انتظار %.1fs · لقطة=%s  %s"
              % (report.get("result"), forced, gone, elapsed, stubborn.clean,
                 "✓" if ok else "✗"))
        print("      السجلّ: %s" % report)
        stubborn.close()

        print("\n  ٤· موت الساعي بإشارته لا يمسّ العمليّة الأمّ:")
        alive = True
        try:
            alive = app is not None and Path(APP).is_file()
        except Exception:                                    # noqa: BLE001
            alive = False
        bad += 0 if alive else 1
        print("      الحارس ما زال حيًّا بعد ثلاث عمليّات إيقاف: %s" % ("✓" if alive else "✗"))
    finally:
        app.subprocess.run = real_run
    return bad


def main() -> int:
    app = load_app()
    bad = structural(app)
    if not hasattr(app, "_stop_process"):
        print("\n  ⇒ لا دالّة إيقاف مفردة بعد؛ الشقّ السلوكيّ لا يمكن تشغيله.")
        print("\n" + "=" * 94)
        print("الاختلافات = %d" % bad)
        return 1
    bad += behavioural(app)
    print("\n" + "=" * 94)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الإيقاف يجرّب النظيف أوّلًا، ولا يقع القسريّ صامتًا، ولا تعليق أبديّ.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
