# -*- coding: utf-8 -*-
"""عقد الإعداد × المخطط — لكل ذرّة، في كل تشغيل.

درس الإقلاع البارد ٢٠٢٦-٠٨-٢٣: ست ذرّات (810–860) كتبت `config` بحقول
يرفضها `config_schema` الذي كتبته هي نفسها (`additionalProperties: false`
بلا `properties`) — فرفضها المُحمِّل عند الإقلاع (scan failures × 6) رغم أن
اختبارات الذرّات كانت خضراء (هي تُهيَّأ بالقيم مباشرة ولا تمرّ عبر المخطط).

هذا الحارس يمنع الصنف كلّه: أي إعداد لا يطابق مخطط صاحبه = فشل هنا
قبل أن يصل إلى إقلاع جهاز المالك.
"""

from __future__ import annotations

import sys

from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)



def _atoms() -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for manifest_path in sorted((ATOM_ROOT).glob("*/manifest.yaml")):
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        out.append((manifest_path.parent.name, manifest))
    return out


ALL_ATOMS = _atoms()
NON_EMPTY = [(name, m) for name, m in ALL_ATOMS if m.get("config")]


def test_there_is_something_to_guard():
    assert len(ALL_ATOMS) >= 200, "جرد الذرّات ناقص بشكل مريب"
    assert NON_EMPTY, "لا إعدادات أصلًا — الحارس بلا عمل"


@pytest.mark.parametrize("name,manifest", [p for p in NON_EMPTY],
                         ids=[p[0] for p in NON_EMPTY])
def test_config_matches_its_own_schema(name: str, manifest: dict):
    schema = manifest.get("config_schema")
    config = manifest.get("config") or {}
    if not isinstance(schema, dict) or not schema:
        pytest.skip(f"{name}: بلا config_schema")
    try:
        jsonschema.validate(config, schema)
    except jsonschema.ValidationError as exc:
        pytest.fail(f"{name}: الإعداد لا يطابق مخططه — {exc.message} "
                    f"(المسار: {list(exc.absolute_path)})")


@pytest.mark.parametrize("name,manifest", [p for p in ALL_ATOMS],
                         ids=[p[0] for p in ALL_ATOMS])
def test_schema_is_not_a_closed_empty_box(name: str, manifest: dict):
    """مخطط مغلق (additionalProperties: false) يجب أن يُعلن خصائص —
    وإلا رفض أي إعداد غير الفارغ (عطل 810–860 حرفيًّا)."""
    schema = manifest.get("config_schema")
    if not isinstance(schema, dict) or not schema:
        return
    closed = schema.get("additionalProperties") is False
    has_properties = bool(schema.get("properties"))
    if closed and not has_properties and (manifest.get("config") or {}):
        pytest.fail(f"{name}: مخطط مغلق بلا properties مع config غير فارغ "
                    "— المُحمِّل سيرفض إعداد الذرّة عند الإقلاع")
