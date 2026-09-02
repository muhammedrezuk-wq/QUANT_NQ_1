"""Contract guard for atom 409 (range strategy) — problem 31, second half.

409 is 405's twin: same shape, same root parent (structure.trend), a binary
detection with a different word (`range` -> `ranging`). So it reuses the SAME
machinery — no second copy of the logic — with 409's own spec.

Proves both halves: the structural barriers (409 carries no buy/sell, 413
keeps range_strategy out of the directional vote, 453 does not declare it)
and end to end (a lone `ranging` ends at WAIT with a zero net).
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "_guard405", Path(__file__).resolve().parent / "check_405_contract.py")
_guard = importlib.util.module_from_spec(_spec)
sys.modules["_guard405"] = _guard
_spec.loader.exec_module(_guard)



# ⏳ م-56/م-58 (ورقة ٤١، 2026-08-28): انهيار AttributeError أُصلح والفحص يعمل،
# لكن عقوده متقادمة — الذرّة صارت تعمل بالتكة الموثّقة (market.tick.validated →
# strategy.*.state) والفحص لا يزال يسبر البنية القديمة (structure.trend.state).
# ترحيله الكامل لنافذة لاحقة — يبقى أحمر صادقًا بلا تلوين.

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(_guard.main_async(_guard.SPEC_409)))
