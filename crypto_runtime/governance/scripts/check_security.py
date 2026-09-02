#!/usr/bin/env python3
"""فحص أمان وخزنة الأسرار من طبقة الحوكمة.

الفحص لا يطبع أي قيمة سرية ولا يطبع أسماء المفاتيح. عند وجود خزنة، يحاول
فتحها بلا طلب تفاعلي ثم يمسح الذاكرة قبل الخروج. غياب الخزنة ليس تلفًا في
الكود، لكنه يعني أن الأسرار غير جاهزة.
"""
from __future__ import annotations

from pathlib import Path
import sys

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print(f"❌ PyYAML مفقودة: {exc}")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _rooted(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    problems: list[str] = []
    for rel in (
        "security/__init__.py",
        "security/interfaces.py",
        "security/providers.py",
        "security/keys.py",
        "governance/scripts/secrets_admin.py",
    ):
        if not (ROOT / rel).is_file():
            problems.append(f"ملف أمان مفقود: {rel}")

    config_path = ROOT / "config" / "core.yaml"
    try:
        core = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        cfg = core.get("secrets") or {}
    except Exception as exc:  # noqa: BLE001
        print(f"❌ إعداد الأمان غير قابل للقراءة: {type(exc).__name__}")
        return 1

    enabled = bool(cfg.get("enabled", True))
    allow_prompt = bool(cfg.get("allow_prompt", True))
    vault = _rooted(str(cfg.get("vault_path", "runtime/secrets.enc")))
    dpapi_blob = cfg.get("dpapi_blob")
    dpapi = _rooted(str(dpapi_blob)) if dpapi_blob else None

    state = "DISABLED"
    key_count = 0
    if enabled:
        try:
            from security.providers import FileSecretProvider

            provider = FileSecretProvider(
                vault,
                dpapi_blob=dpapi,
                allow_prompt=False,
                auto_open=True,
            )
            raw_state = provider.state.value
            key_count = len(provider.available_keys())
            if raw_state == "available":
                state = "READY"
            elif raw_state == "locked":
                state = "LOCKED"
                problems.append("الخزنة موجودة لكن مفتاحها غير متاح")
            elif raw_state == "unavailable" and not vault.exists():
                state = "NOT_CONFIGURED"
            else:
                state = "UNAVAILABLE"
                problems.append("تعذّر التحقق من خزنة الأسرار")
            provider.clear()
        except ModuleNotFoundError as exc:
            state = "DEPENDENCY_MISSING"
            print(f"⚠ حزمة أمان ناقصة: {exc.name} — شغّل Repair_Tests.bat")
        except Exception as exc:  # noqa: BLE001
            state = "UNAVAILABLE"
            problems.append(f"تعذّر تحميل طبقة الأمان: {type(exc).__name__}")

    print("فحص الأمان وخزنة الأسرار")
    print(f"SECURITY_STATE={state}")
    print(f"security.enabled={enabled}")
    print(f"allow_prompt={allow_prompt}")
    print(f"vault_present={vault.is_file()}")
    print(f"key_count={key_count}")
    print("secret_values=NOT_DISPLAYED")
    if not enabled:
        print("⚠ طبقة الأسرار معطّلة في الإعداد — لا يُسمح بهذا قبل التشغيل المالي.")
    elif state == "READY":
        print("✅ طبقة الأمان سليمة والخزنة فُتحت بلا عرض للقيم.")
    elif state == "NOT_CONFIGURED":
        print("⚠ الكود سليم، لكن الخزنة لم تُنشأ بعد؛ لا توجد أسرار جاهزة.")
    else:
        for problem in problems:
            print("❌ " + problem)
    # غياب الخزنة حالة تحذير مفهومة، أما التلف/القفل/التعطيل ففشل أمني.
    return 0 if state in {"READY", "NOT_CONFIGURED", "DEPENDENCY_MISSING"} and enabled and not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
