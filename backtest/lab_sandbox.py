# -*- coding: utf-8 -*-
"""عتبات المختبر — معزولة عن مسار التداول الحي.

تُحفظ هنا فقط (var/lab/overrides). لا تُكتب في manifest.yaml ولا تُرسل
لـ /api/rescan. جولة المختبر تدمج هذه الطبقة فوق إعداد المانيفست في الذاكرة.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ATOMS_DIR = ROOT / "atoms"
LAB_DIR = ROOT / "var" / "lab" / "overrides"
_LOCK = threading.Lock()

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


def _overlay_path(atom_id: int) -> Path:
    return LAB_DIR / f"{int(atom_id)}.json"


def _manifest_path(atom_id: int) -> Path | None:
    if not ATOMS_DIR.is_dir():
        return None
    needle = f"{int(atom_id)}_"
    for pattern in ("*/manifest.yaml", "*/*/manifest.yaml"):
        for mf in ATOMS_DIR.glob(pattern):
            if mf.parent.name.startswith(needle) or mf.parent.name == str(atom_id):
                return mf
    # بطء احتياط: اقرأ id من الملف
    for pattern in ("*/manifest.yaml", "*/*/manifest.yaml"):
        for mf in ATOMS_DIR.glob(pattern):
            try:
                text = mf.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            m = re.search(r"^\s*id:\s*(\d+)", text, re.M)
            if m and int(m.group(1)) == int(atom_id):
                return mf
    return None


def _live_bundle(atom_id: int) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if yaml is None:
        return None
    mf = _manifest_path(atom_id)
    if mf is None:
        return None
    try:
        data = yaml.safe_load(mf.read_text(encoding="utf-8-sig")) or {}
    except Exception:
        return None
    cfg = data.get("config")
    schema = data.get("config_schema") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    if not isinstance(schema, dict):
        schema = {}
    return cfg, schema


def _read_overlay(atom_id: int) -> dict[str, Any]:
    path = _overlay_path(atom_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    ov = data.get("overrides") if isinstance(data, dict) else None
    return dict(ov) if isinstance(ov, dict) else {}


def _write_overlay(atom_id: int, overrides: dict[str, Any]) -> None:
    LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = _overlay_path(atom_id)
    if not overrides:
        path.unlink(missing_ok=True)
        return
    payload = {
        "atom_id": int(atom_id),
        "updated_at": time.time(),
        "overrides": overrides,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _to_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "true":
            return True
        if text == "false":
            return False
    return None


def _typed(value: object, schema: dict) -> object:
    kind = schema.get("type")
    if kind == "boolean":
        parsed = _to_bool(value)
        if parsed is None:
            raise ValueError("expected boolean")
        return parsed
    if kind == "integer":
        if isinstance(value, bool):
            raise ValueError("expected integer")
        return int(value)
    if kind == "number":
        if isinstance(value, bool):
            raise ValueError("expected number")
        result = float(value)
        if result != result or result in (float("inf"), float("-inf")):
            raise ValueError("finite number required")
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if lo is not None and result < float(lo):
            raise ValueError(f"below minimum {lo}")
        if hi is not None and result > float(hi):
            raise ValueError(f"above maximum {hi}")
        return int(result) if result.is_integer() else result
    if kind in ("array", "object") and isinstance(value, str):
        value = json.loads(value)
    if kind == "array" and not isinstance(value, list):
        raise ValueError("expected array")
    if kind == "object" and not isinstance(value, dict):
        raise ValueError("expected object")
    if kind == "string" and not isinstance(value, str):
        return str(value)
    return value


def overlays_for(atom_ids: list[int] | None = None) -> dict[int, dict[str, Any]]:
    """طبقات المختبر فقط — لا تقرأ مسار التداول."""
    out: dict[int, dict[str, Any]] = {}
    if not LAB_DIR.is_dir():
        return out
    wanted = set(int(x) for x in atom_ids) if atom_ids is not None else None
    for path in LAB_DIR.glob("*.json"):
        try:
            aid = int(path.stem)
        except ValueError:
            continue
        if wanted is not None and aid not in wanted:
            continue
        ov = _read_overlay(aid)
        if ov:
            out[aid] = ov
    return out


def lab_config(atom_id: int) -> dict[str, Any]:
    bundle = _live_bundle(atom_id)
    if bundle is None:
        return {"ok": False, "sandbox": True, "error": "الذرّة غير موجودة أو لا مانيفست"}
    live, schema = bundle
    props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
    overlay = _read_overlay(atom_id)
    settings = []
    for key, val in live.items():
        p = props.get(key, {}) if isinstance(props, dict) else {}
        overridden = key in overlay
        settings.append({
            "key": key,
            "value": overlay[key] if overridden else val,
            "live_value": val,
            "overridden": overridden,
            "type": p.get("type", "string") if isinstance(p, dict) else "string",
            "min": p.get("minimum") if isinstance(p, dict) else None,
            "max": p.get("maximum") if isinstance(p, dict) else None,
        })
    return {
        "ok": True,
        "sandbox": True,
        "id": int(atom_id),
        "settings": settings,
        "override_count": sum(1 for s in settings if s["overridden"]),
    }


def save_lab_config(atom_id: int, updates: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(updates, dict) or not updates:
        return {"ok": False, "sandbox": True, "error": "لا يوجد تعديل صالح"}
    bundle = _live_bundle(atom_id)
    if bundle is None:
        return {"ok": False, "sandbox": True, "error": "الذرّة غير موجودة"}
    live, schema = bundle
    props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
    if not isinstance(props, dict):
        props = {}
    clean: dict[str, Any] = {}
    try:
        for key, value in updates.items():
            if key not in live or key not in props:
                raise ValueError(f"إعداد غير معروف: {key}")
            clean[key] = _typed(value, props[key] if isinstance(props[key], dict) else {})
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "sandbox": True, "error": str(exc)}
    with _LOCK:
        overlay = _read_overlay(atom_id)
        overlay.update(clean)
        # لا نخزّن ما يساوي الحي — الطبقة فارغة = نفس التداول
        for key in list(overlay):
            if key in live and overlay[key] == live[key]:
                overlay.pop(key, None)
        _write_overlay(atom_id, overlay)
    return {
        "ok": True,
        "sandbox": True,
        "id": int(atom_id),
        "override_count": len(overlay),
        "message": "انحفظت للمختبر فقط — مسار التداول الحي ما تغيّر",
    }


def reset_lab_config(atom_id: int) -> dict[str, Any]:
    with _LOCK:
        path = _overlay_path(atom_id)
        existed = path.is_file()
        path.unlink(missing_ok=True)
    return {
        "ok": True,
        "sandbox": True,
        "id": int(atom_id),
        "cleared": existed,
        "message": "رجعت عتبة هالذرّة بالمختبر لأصل التداول — الحي ما تلمس",
    }


def reset_all_lab_configs() -> dict[str, Any]:
    n = 0
    with _LOCK:
        if LAB_DIR.is_dir():
            for path in LAB_DIR.glob("*.json"):
                try:
                    path.unlink()
                    n += 1
                except OSError:
                    continue
    return {
        "ok": True,
        "sandbox": True,
        "cleared": n,
        "message": f"انمسحت {n} طبقة مختبر — التداول الحي كما هو",
    }
