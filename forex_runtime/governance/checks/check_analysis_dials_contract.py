#!/usr/bin/env python3
"""فحص عقد عيارات التحليل — الحرّاس الأربعة يقولون الشيء نفسه.

**لماذا وُجد هذا الفحص (عطل مقيس ٢٠٢٦-٠٨-٢١):** أمر ضبط العيار يمرّ بأربع
بوّابات متتالية، وكلٌّ منها كانت تحمل **نسخةً محفورة** من قائمة العيارات:

    اللوحة  ←  حارس الخادم  ←  بوّابة الأوامر ٩٠١  ←  مخزن المعايرة

فلمّا خُتمت ثلاث عتبات جديدة (القوّة · الطزاجة · المنطقة الحيادية) قبلها
المخزن، ورفضها الخادم بصمت برسالة «لازم الحساب والأصل…»، وما كان أحد يعرف
أيّ حارسٍ رفض. هذا الفحص يجعل الحرّاس **مصدرًا واحدًا**، ويسقط إن افترق أحدهم.

ويثبت كذلك القاعدة الجوهرية بورقة المالك (اجوبة §٢٦):
    «تغيير إعداد في السريع لا يغيّر إعداد البطيء».

الاستعمال:  python governance/checks/check_analysis_dials_contract.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "governance") not in sys.path:
    sys.path.insert(0, str(ROOT / "governance"))

from shared.live_analysis import (  # noqa: E402
    DIAL_DEFAULTS, PATH_FAST, PATH_SLOW, TUNABLE_SETTINGS,
    AnalysisSettingsStore, LiveAnalyzerKernel, LiveState,
)

LINE = "=" * 78
failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print("  %-52s %s%s" % (label, "✓" if ok else "✗",
                            ("  — " + detail) if detail else ""))


def source_of(path: Path) -> str:
    return path.read_text(encoding="utf-8")


print(LINE)
print("١· الحرّاس الأربعة يقرأون قائمة واحدة")
print(LINE)

expected = set(TUNABLE_SETTINGS)
print("      قائمة المحرّك: %s" % " · ".join(sorted(expected)))

server_src = source_of(ROOT / "governance" / "server.py")
gateway_src = source_of(ATOM_ROOT / "901_بوابة_الأوامر" / "atom.py")
ui_src = source_of(ROOT / "governance" / "ui" / "src" / "sections" / "Analysis.tsx")

# حارسا الخادم والبوّابة يجب أن يستوردا القائمة لا أن ينسخاها.
check("الخادم لا ينسخ القائمة محفورة",
      "_ANALYSIS_TUNABLE" in server_src
      and 'set(settings) - {"required_depth"' not in server_src,
      "حارس /gov/command")
check("بوّابة ٩٠١ لا تنسخ القائمة محفورة",
      "_ANALYSIS_TUNABLE" in gateway_src
      and 'settings) - {"required_depth"' not in gateway_src,
      "تحقّق الحمولة")

# الخادم يستورد القائمة نفسها فعليًّا (لا مجرّد اسمٍ متشابه).
import server as gov_server  # noqa: E402

check("قائمة الخادم = قائمة المحرّك",
      set(gov_server._ANALYSIS_TUNABLE) == expected,
      " · ".join(sorted(set(gov_server._ANALYSIS_TUNABLE) ^ expected)) or "مطابقة")

# اللوحة تعرض كل عتبة ولها اسم عربي.
missing_ui = [name for name in DIAL_DEFAULTS if name not in ui_src]
check("اللوحة تعرض كل العتبات", not missing_ui,
      "غائب: " + " · ".join(missing_ui) if missing_ui else "الخمس ظاهرة")

# نصّ التأكيد يسمّي كل عيار بالعربي.
names_block = server_src.split("setting_names = {", 1)[-1].split("}", 1)[0]
missing_names = [name for name in TUNABLE_SETTINGS if f'"{name}"' not in names_block]
check("نصّ التأكيد يسمّي كل عيار بالعربي", not missing_names,
      "بلا اسم: " + " · ".join(missing_names) if missing_names else "الستّة مسمّاة")

print()
print(LINE)
print("٢· المخزن يقبل الستّة ويرفض ما عداها")
print(LINE)

folder = Path(tempfile.mkdtemp())
store = AnalysisSettingsStore(str(folder / "settings.db"))
row = store.defaults("trend")
check("صفّ الافتراضات يحمل كل عيار",
      all(name in row for name in TUNABLE_SETTINGS),
      " · ".join(f"{k}={row[k]}" for k in DIAL_DEFAULTS))

for name in TUNABLE_SETTINGS:
    try:
        store.update("A", "B", "SYM", "trend", {name: 7.0}, changed_by="check",
                     command_id=f"cmd-{name}", changed_at=1.0, path=PATH_FAST)
        ok = True
    except ValueError as exc:
        ok = False
        detail = str(exc)
    check(f"يقبل «{name}»", ok, "" if ok else detail)

try:
    store.update("A", "B", "SYM", "trend", {"invented_dial": 1.0},
                 changed_by="check", command_id="cmd-bad", changed_at=2.0)
    check("يرفض عيارًا مخترَعًا", False, "مرّ!")
except ValueError:
    check("يرفض عيارًا مخترَعًا", True, "INVALID_ANALYSIS_SETTING")

try:
    store.update("A", "B", "SYM", "trend", {"required_depth": 140.0},
                 changed_by="check", command_id="cmd-range", changed_at=3.0)
    check("يرفض قيمة خارج ٠–١٠٠", False, "مرّت!")
except ValueError:
    check("يرفض قيمة خارج ٠–١٠٠", True, "OUT_OF_RANGE")

print()
print(LINE)
print("٣· استقلال المسارين (ورقة المالك §٢٦)")
print(LINE)

fresh = AnalysisSettingsStore(str(folder / "paths.db"))
fresh.update("A", "B", "SYM", "trend",
             {"strength_threshold": 44.0, "stale_after_s": 11.0},
             changed_by="check", command_id="p-slow", changed_at=4.0, path=PATH_SLOW)
slow = fresh.get("A", "B", "SYM", "trend", PATH_SLOW)
fast = fresh.get("A", "B", "SYM", "trend", PATH_FAST)
check("البطيء استلم التعديل",
      slow["strength_threshold"] == 44.0 and slow["stale_after_s"] == 11.0,
      f"قوّة={slow['strength_threshold']} · طزاجة={slow['stale_after_s']}")
check("السريع لم يُمَسّ",
      fast["strength_threshold"] == DIAL_DEFAULTS["strength_threshold"]
      and fast["stale_after_s"] == DIAL_DEFAULTS["stale_after_s"],
      f"قوّة={fast['strength_threshold']} · طزاجة={fast['stale_after_s']}")

print()
print(LINE)
print("٤· العيار يحجب فعلًا — لا عيار زينة")
print(LINE)


def analyzed(db_name: str, **dials):
    kernel = LiveAnalyzerKernel("trend", "analysis.trend.state")
    kernel.settings = AnalysisSettingsStore(str(folder / db_name))
    kernel.settings_cache.clear()
    if dials:
        kernel.settings.update("A", "B", "SYM", "trend", dials, changed_by="check",
                               command_id=f"k-{db_name}", changed_at=5.0, path=PATH_FAST)
    state = LiveState()
    price = 100.0
    for move in [0.000004] * 26:
        price *= (1.0 + move)
        if state.prices:
            state.returns.append(move)
        state.prices.append(price)
        state.spreads.append(0.00001)
        state.volumes.append(10.0)
        state.timestamps.append(float(len(state.prices)))
        state.movements.append(abs(move))
    return kernel._analyze(("A", "B", "SYM"), state, 0.0)


base = analyzed("k0.db")
check("بالافتراض: لا حجب بالقوّة",
      base["reason"] != "STRENGTH_BELOW_THRESHOLD",
      f"{base['state']} · قوّة={base['strength']:.1f}")

gated = analyzed("k1.db", strength_threshold=99.0)
check("عتبة قوّة ٩٩ تحجب",
      gated["reason"] == "STRENGTH_BELOW_THRESHOLD", gated["state"])

wide = analyzed("k2.db", direction_neutral_band=100.0)
narrow = analyzed("k3.db", direction_neutral_band=0.0)
check("المنطقة الحيادية تغيّر تسمية الاتجاه",
      wide["signal"] == "sideways" and narrow["signal"] != "sideways",
      f"١٠٠⇒{wide['signal']} · ٠⇒{narrow['signal']}")

print()
print(LINE)
if failures:
    print(f"فشل {failures} — عقد العيارات مكسور")
    sys.exit(1)
print("سليم: الحرّاس الأربعة على قائمة واحدة · المساران مستقلّان · العيار يحجب فعلًا.")
