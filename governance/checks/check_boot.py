#!/usr/bin/env python3
"""Read-only Atom Boot check with dynamic expectations from Build Registry."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_registry import BuildRegistry  # noqa: E402

BOOT_PATTERN = re.compile(r"بدأت=\[(.*?)\] فشلت=\[(.*?)\] استُبعدت=\[(.*?)\]")


def _reader(stream, output: queue.Queue[str]) -> None:
    try:
        for line in iter(stream.readline, ""):
            output.put(line)
    finally:
        stream.close()


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            process.kill()
            process.wait(timeout=5)
        except Exception:
            pass


def _numbers(text: str) -> list[int]:
    return [int(x.strip()) for x in text.split(",") if x.strip()]


def main() -> int:
    registry = BuildRegistry(ROOT).refresh()
    # إصلاح م-28 (ورقة ٤١، بأمر المالك 2026-08-28): عاد التوقع **المستقل الثابت**
    # إلى جانب توقّع Registry — فلو عُبث بالمانيفستات صمتًا (حذف ذرّة أو قلب
    # startup_mode) اكتشفه الفحص حتى لو ظل السجلّ متسقًا مع نفسه.
    EXPECTED_FOREX_ROOT = 233
    # 2026-08-29: مطابقة شجرة أحمد المُسلَّمة رفعت العدد 77 ← 84 (ورقة
    # ASMAR_كريبتو 16-20). الثابت مقصود: أي تغيّر صامت بالمانيفستات يُفشِل.
    # 2026-09-01: 84 ← 80. المالك حذف أربع ذرّات بيده («4 زرات انا مسحتهم
    # مالهم شغل»)، فصار الفحص يصرخ على قرارٍ مقصود كل مرّة — وحارسٌ يصرخ
    # على الصواب يُعلَّم تجاهله، فيصمت يوم يقع خطأ حقيقيّ. الثابت يُحدَّث مع
    # قرار المالك لا يُلتَفّ عليه. (`tests/test_unified_layout.py` حُدّث معه.)
    EXPECTED_CRYPTO_ROOT = 80
    MIN_STARTED_FLOOR = 220
    # م-44 (ورقة ٤١، 2026-08-28): تثبيت أوضاع الذرّات الحرجة — انقلاب startup_mode
    # صمتًا (مثل تحويل 901 أو 2901) كان يمرّ ما دام العدد الكلي مطابقًا.
    CRITICAL_PINNED = {
        901: "auto", 516: "auto", 550: "auto", 552: "auto", 576: "auto",
        578: "auto", 601: "auto", 613: "auto", 701: "auto", 703: "auto",
        2901: "manual", 625: "manual", 626: "manual",
    }
    expected_auto = {
        record.atom_id
        for record in registry.forex_all
        if record.atom_id is not None and record.startup_mode == "auto"
    }
    if registry.integrity.missing_roots or registry.integrity.discovery_failures:
        print("❌ Registry discovery غير صالح قبل فحص الإقلاع")
        return 1
    by_id = {r.atom_id: r for r in (*registry.forex_all, *registry.crypto_all) if r.atom_id is not None}
    flipped = {aid: (by_id[aid].startup_mode, want) for aid, want in CRITICAL_PINNED.items()
               if aid in by_id and by_id[aid].startup_mode != want}
    if flipped:
        print(f"❌ انقلاب وضع ذرّة حرجة صمتًا: {flipped} — قرار مالك مطلوب")
        return 1
    if len(registry.forex_all) != EXPECTED_FOREX_ROOT or len(registry.crypto_all) != EXPECTED_CRYPTO_ROOT:
        print(f"❌ توقع مستقل مخالف: forex={len(registry.forex_all)} (المتوقع {EXPECTED_FOREX_ROOT}) · "
              f"crypto={len(registry.crypto_all)} (المتوقع {EXPECTED_CRYPTO_ROOT}) — تغيير صامت بالمانيفستات؟")
        return 1

    temp = tempfile.TemporaryDirectory(prefix="quant_nq_boot_")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    env["NQ_BRIDGE_DB"] = str(Path(temp.name) / "bridge.db")
    env["NQ_CTRADER_FEED"] = str(Path(temp.name) / "ctrader.jsonl")
    command = [sys.executable, str(ROOT / "governance" / "scripts" / "run_core.py"), "--no-api"]
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    lines: queue.Queue[str] = queue.Queue()
    thread = threading.Thread(target=_reader, args=(process.stdout, lines), daemon=True)
    thread.start()
    deadline = time.monotonic() + 60
    boot_line = ""
    recent: list[str] = []
    try:
        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            recent.append(line.rstrip())
            recent = recent[-20:]
            if "انتهى الإقلاع:" in line:
                boot_line = line
                break
    finally:
        _stop(process)
        temp.cleanup()

    print("فحص الإقلاع الفعلي عبر Build Registry")
    print(f"المتوقع تلقائيًا من Registry: {len(expected_auto)}")
    if not boot_line:
        print("❌ لم يصل تقرير الإقلاع خلال 60 ثانية")
        for line in recent[-8:]:
            print(line)
        return 1

    match = BOOT_PATTERN.search(boot_line)
    if not match:
        print("❌ تقرير الإقلاع وصل لكن صيغته غير قابلة للقراءة")
        print(boot_line.rstrip())
        return 1

    started = _numbers(match.group(1))
    failed = _numbers(match.group(2))
    excluded = _numbers(match.group(3))
    missing = sorted(expected_auto - set(started))
    unexpected = sorted(set(started) - expected_auto)
    print(f"بدأت فعليًا: {len(started)}")
    print(f"فشلت: {failed or 'لا شيء'}")
    print(f"استُبعدت: {excluded or 'لا شيء'}")
    if len(started) < MIN_STARTED_FLOOR:
        print(f"❌ أرضية مستقلة: بدأت {len(started)} < {MIN_STARTED_FLOOR} — انكماش صامت في الإقلاع")
        return 1
    if missing or unexpected or failed:
        print(f"❌ اختلاف الإقلاع: missing={missing or 'لا شيء'} unexpected={unexpected or 'لا شيء'}")
        return 1
    # 2026-09-01: الرسالة كانت تطبع «233/77» نصًّا ثابتًا بينما الثابت الفعليّ
    # صار 84 ثم 80 — أي حارس يعلن رقمًا غير الذي فحص به. تُشتقّ من الثابتين.
    print(f"✅ Atom Boot يطابق auto المكتشفة + التوقع المستقل "
          f"{EXPECTED_FOREX_ROOT}/{EXPECTED_CRYPTO_ROOT} + أوضاع الذرّات الحرجة مثبّتة (م-44).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
