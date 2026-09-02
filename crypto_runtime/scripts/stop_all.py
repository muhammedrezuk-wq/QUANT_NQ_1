"""إيقاف كل خدمات QUANT_NQ — إغلاق نظيف ثمّ تحقّق بالقياس.

سبب إعادة الكتابة (٢٠٢٦-٠٩-٠١، مقيس):
النسخة السابقة كانت تعرف ستّة منافذ فقط `{8010, 8020, 8090, 8092, 8093,
8098}` — ينقصها **8091** (لوحة الكريبتو) و**8099** (قفل المشرف
`governance/app.py`). فبقيت عمليّتان تعملان بعد «تمّ الإيقاف»، واحتاجتا
إطفاءً يدويًّا. وكانت تعلن النجاح بلا تحقّق: تطبع «تمّ» ولو بقي المنفذ
مشغولًا — وهذا أسوأ من الفشل الصريح لأنّه يخفيه.

ما تغيّر:
1. المنافذ الثمانية كاملة.
2. مسحة ثانية بالمسار: أي عمليّة بايثون تنفّذ ملفًّا داخل جذر المشروع
   تُلتقط حتّى لو لم تستمع على منفذ (مشغّل · عامل · بقايا).
3. تحقّق بعد الإيقاف: تُعاد قراءة المنافذ، ولا يُعلَن النجاح إلّا إذا
   تحرّرت كلّها فعلًا. وإلّا خرج بالرمز 1 وسمّى ما بقي بالرقم.
4. لا تلمس `QUANT_NQ_NEWS` — مشروع شقيق ببيئته وزرّه. تُبلّغ عنه فقط.
"""
from __future__ import annotations
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEWS_ROOT = ROOT.parent / "QUANT_NQ_NEWS"

#: كل منافذ النظام. 8091 و8099 كانتا الناقصتين.
PORTS = {
    8010: "نواة الفوركس",
    8020: "نواة الكريبتو",
    8090: "لوحة الفوركس",
    8091: "لوحة الكريبتو",
    8092: "حوكمة الفوركس",
    8093: "حوكمة الكريبتو",
    8098: "منصّة تلغرام",
    8099: "قفل المشرف",
}


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.20)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _ancestors(proc, psutil) -> set[int]:
    """أنا وآبائي — لا نقتل أنفسنا ولا الطرفيّة التي أطلقتنا."""
    out = {proc.pid}
    try:
        for p in proc.parents():
            out.add(p.pid)
    except Exception:
        pass
    return out


def _send_ctrl_break(pid: int) -> bool:
    """أرسل CTRL_BREAK_EVENT على ويندوز — يُلتقط بمقبض SIGBREAK."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # CTRL_BREAK_EVENT = 1 (ويندوز)
        # signal.SIGBREAK = 21 (بايثون) — لكن الصحيح هو CTRL_BREAK_EVENT = 1
        PROCESS_ALL_ACCESS = 0x1F0FFF
        handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not handle:
            return False
        try:
            import signal
            # CTRL_BREAK_EVENT = 1 (ويندوز) — ليس SIGBREAK = 21
            os.kill(pid, signal.CTRL_BREAK_EVENT)
            return True
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def main() -> int:
    try:
        import psutil
    except ImportError:
        print("psutil غير مثبت — شغّل زرّ التهيئة.")
        return 1

    me = psutil.Process()
    protected = _ancestors(me, psutil)
    targets: dict[int, str] = {}

    # ── مسحة ١: بالمنافذ ────────────────────────────────────────────
    for c in psutil.net_connections(kind="tcp"):
        if c.laddr and c.laddr.port in PORTS and c.pid and c.pid not in protected:
            targets.setdefault(c.pid, PORTS[c.laddr.port])

    # ── مسحة ٢: بالمسار — تلتقط ما لا يستمع على منفذ ───────────────
    root_s = str(ROOT).lower()
    news_s = str(NEWS_ROOT).lower()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        pid = p.info["pid"]
        if pid in protected or pid in targets:
            continue
        if not (p.info["name"] or "").lower().startswith("python"):
            continue
        cmd = " ".join(p.info["cmdline"] or []).lower()
        if news_s in cmd:          # المشروع الشقيق — لا يُلمس
            continue
        if root_s in cmd:
            targets[pid] = "عمليّة بالمشروع"

    if not targets:
        print("لا توجد خدمات QUANT_NQ عاملة.")
    else:
        procs = []
        # ── ويندوز: أرسل CTRL_BREAK_EVENT أولاً (يُلتقط بمقبض SIGBREAK) ──
        if os.name == "nt":
            for pid, label in sorted(targets.items()):
                try:
                    proc = psutil.Process(pid)
                    print(f"  إرسال CTRL_BREAK إلى PID={pid:<7} {label}")
                    if _send_ctrl_break(pid):
                        procs.append(proc)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            # انتظر ١٠ ثوانٍ للإغلاق النظيف (يُنفَّذ snapshot_all)
            _, alive = psutil.wait_procs(procs, timeout=10)
            if alive:
                print(f"  {len(alive)} عمليات لم تستجب لـ CTRL_BREAK — إرسال terminate()")
                for proc in alive:
                    try:
                        proc.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                _, alive = psutil.wait_procs(alive, timeout=5)
        else:
            # ── لينكس/ماك: terminate() كالمعتاد ─────────────────────
            for pid, label in sorted(targets.items()):
                try:
                    proc = psutil.Process(pid)
                    print(f"  إيقاف PID={pid:<7} {label}")
                    proc.terminate()
                    procs.append(proc)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            _, alive = psutil.wait_procs(procs, timeout=8)
        
        # ── إنهاء قسريّ لمن لم يستجب ────────────────────────────────
        for proc in alive:
            try:
                print(f"  لم يستجب — إنهاء قسريّ PID={proc.pid}")
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        psutil.wait_procs(alive, timeout=4)

    # ── التحقّق: لا نُعلن النجاح إلّا بالقياس ────────────────────────
    print()
    still = {port: name for port, name in PORTS.items() if listening(port)}
    if still:
        print("  ✗ منافذ ما زالت مشغولة:")
        for port, name in sorted(still.items()):
            owner = ""
            for c in psutil.net_connections(kind="tcp"):
                if c.laddr and c.laddr.port == port and c.pid:
                    owner = f" (PID {c.pid})"
                    break
            print(f"      {port}  {name}{owner}")
        return 1

    print("  ✓ المنافذ الثمانية كلّها حرّة — النظام متوقّف.")

    # ── المشروع الشقيق: تبليغ لا إطفاء ──────────────────────────────
    news = [p.info["pid"] for p in psutil.process_iter(["pid", "cmdline"])
            if news_s in " ".join(p.info["cmdline"] or []).lower()]
    if news:
        print()
        print(f"  ملاحظة: بوت الأخبار يعمل ({len(news)} عمليّة) ولم يُلمس —")
        print("  مشروع شقيق ببيئته الخاصّة. إطفاؤه من نافذته أو من مجلّده.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
