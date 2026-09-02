# -*- coding: utf-8 -*-
"""اعتماد المُعامِلات الستّة المعلنة — قرار المالك 2026-08-28 (سجلّ الختم)
في قاعدة المعايرة الحيّة (var/forex/analysis_settings.db).

التشغيل (من داخل مجلد المشروع — والنظام موقوف):
    py tools\approve_scalp_params.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _find_project_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "shared" / "parameter_registry.py").is_file():
            return candidate
    return start


ROOT = _find_project_root(_HERE)
sys.path.insert(0, str(ROOT))

from shared.parameter_registry import ParameterRegistry, DECLARED  # noqa: E402

configured = os.environ.get("QUANT_ANALYSIS_SETTINGS_DB")
db = Path(configured) if configured else ROOT / "var" / "forex" / "analysis_settings.db"
if not db.is_absolute():
    db = ROOT / db
print(f"[1/2] قاعدة المعايرة: {db}")

reg = ParameterRegistry(str(db))
command_id = f"SCALP-PHASEC-APPROVE-{time.strftime('%Y%m%d-%H%M%S')}"
approved: list[str] = []
for name, spec in DECLARED.items():
    try:
        reg.approve(
            name,
            value=float(spec["value"]),
            source="OWNER",            # قرار المالك — لا اجتهاد كود
            approved_by="NQ",          # ختم NQ (سجلّ 2026-08-28 18:30)
            command_id=command_id,
            approved_at=time.time(),
            scope=spec["scope"],
        )
        approved.append(name)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠ {name}: {exc}")

print(f"[2/2] ✅ اعتُمدت ({len(approved)}): " + ", ".join(approved))
left = reg.unapproved()
print("      غير معتمدة بعد: " + (", ".join(left) if left else "لا شيء — كلها معتمدة"))
if left:
    print("⚠ راجع الأخطاء أعلاه.")
    raise SystemExit(1)
print("أعد تشغيل النظام — ستصبح الأقسام READY فور استيفاء العمق.")