# -*- coding: utf-8 -*-
"""دفتر اللوحات (1.md §٩) — البنود المقيسة ٢٠٢٦-٠٨-٢٣ وأقفالها الدائم.

البند ٤ · «أربعة أقسام بلا بطاقة موحّدة» — الحقيقة المقيسة هنا: مدراء
200/250/300 يحملون ‎@section_live‎ ويصدرون بطاقاتهم الموحدة فور وصول حالة
وحدة واحدة + تكة. عرَض الجهاز الحيّ (لا بطاقات) سببه صمت الوحدات نفسه
(REQUIRED_UNITS — تحقيق ٩٠-٣١ المعلّق بقرار المالك)، لا غياب منطق البطاقة.
هذا الاختبار يحرس البنية: لو كُسر الديكوريتور أو البطاقة سقط فورًا.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from core.contracts.atom import AtomContext

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)


SECTIONS = {
    200: ("200_مدير_البنية", "structure.section.live", "structure.swing.state"),
    250: ("250_مدير_السيولة", "liquidity.section.live", "liquidity.pool.state"),
    300: ("300_مدير_الإحصاء", "stats.section.live", "stats.mean.state"),
}


class _Log:
    def __getattr__(self, _n: str):
        return lambda *a, **k: None


class _Ctx:
    def __init__(self, cfg):
        self.config = cfg
        self.handlers: list[tuple[str, Any]] = []
        self.events: list[tuple[str, dict]] = []

    def subscribe(self, event, handler):
        self.handlers.append((event, handler))

    async def publish(self, event, payload):
        self.events.append((event, payload))


def _load(folder: str):
    path = ATOM_ROOT / folder / "atom.py"
    spec = importlib.util.spec_from_file_location(f"dash_{folder.split('_')[0]}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize("atom_id", [200, 250, 300])
async def test_section_managers_publish_unified_cards(atom_id: int):
    import yaml
    folder, live_event, unit_event = SECTIONS[atom_id]
    module = _load(folder)
    manifest = yaml.safe_load((ATOM_ROOT / folder / "manifest.yaml").read_text())
    ctx = _Ctx(manifest.get("config") or {})
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id, ctx.config, _Log(),
                                      ctx.publish, ctx.subscribe))
    await atom.start()
    handlers = dict(ctx.handlers)

    await handlers[unit_event]({
        "account_id": "A", "broker": "BR", "symbol": "NQ", "id": "unit",
        "cycle_id": "c1", "status": "ok", "signal": "up", "score": 60,
        "confidence": 70, "quality": "good", "weight": 20, "sequence": 1})
    await handlers["market.tick.validated"]({
        "account_id": "A", "broker": "BR", "symbol": "NQ",
        "timeframe": "tick", "sequence": "1", "timestamp": 1.0, "price": 100})
    await handlers["platform.account.state"]({"account_id": "A", "broker": "BR"}) \
        if "platform.account.state" in handlers else None

    cards = [p for e, p in ctx.events if e == live_event]
    assert cards, f"القسم {atom_id} لم ينشر بطاقته الموحدة"
    card = cards[-1]
    # الحقول الموحدة الأساسية موجودة — والمجهول يُعلن لا يُخترع
    for field in ("symbol", "ready", "current_depth", "required_depth",
                  "confidence", "unified"):
        assert field in card, (atom_id, field)
    unified = card.get("unified")
    assert isinstance(unified, dict) and "state" in unified
