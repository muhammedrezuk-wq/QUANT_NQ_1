"""عقد subscribe_all — الذرات تقدر تشترك على كل حدث خام عبر AtomContext.

قبل هذا الملف: subscribe_all كانت مكشوفة لكود core الداخلي فقط
(core/api/app.py، لطبقة ٢ برّا العملية). الذرات ما كان عندها طريقة تستخدمها
إطلاقاً — AtomContext ما فيها إلا subscribe (اسم حدث واحد بالمرة).
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from core.contracts.atom import AtomContext
from core.event_bus import EventBus


def _build_context_like_bootloader(bus: EventBus, atom_id: int) -> AtomContext:
    """يكرّر حرفياً نمط التوصيل الحقيقي في core/bootloader.py:294-304."""
    return AtomContext(
        atom_id=atom_id,
        config={},
        logger=_NullLogger(),
        publish=lambda name, payload=None, _aid=atom_id: bus.publish(
            name, payload, publisher=str(_aid)),
        subscribe=lambda name, handler, _aid=atom_id: bus.subscribe(
            name, handler, subscriber=str(_aid)),
        subscribe_all=lambda handler, _aid=atom_id: bus.subscribe_all(
            handler, subscriber=str(_aid)),
    )


class _NullLogger:
    def debug(self, *a: Any, **k: Any) -> None: pass
    def info(self, *a: Any, **k: Any) -> None: pass
    def warning(self, *a: Any, **k: Any) -> None: pass
    def error(self, *a: Any, **k: Any) -> None: pass
    def critical(self, *a: Any, **k: Any) -> None: pass


def test_atom_context_declares_a_subscribe_all_field() -> None:
    """العقد نفسه لازم يعلن subscribe_all — لا يكفي أن EventBus يملكها
    داخلياً، الذرة ما بتشوف شي غير حقول AtomContext."""
    field_names = {f.name for f in dataclasses.fields(AtomContext)}
    assert "subscribe_all" in field_names, (
        "AtomContext لازم تعلن subscribe_all — الذرات ما عندها وصول لغيرها")


@pytest.mark.asyncio
async def test_atom_can_receive_every_event_via_context_subscribe_all() -> None:
    """ذرة اشتركت subscribe_all بلا ما تسمي أي حدث بعينه — لازم توصلها كل
    الأحداث، حتى واحد ما اشتركت باسمه إطلاقاً."""
    bus = EventBus()
    received: list[tuple[str, dict]] = []

    async def _firehose(event_name: str, payload: dict) -> None:
        received.append((event_name, payload))

    context = _build_context_like_bootloader(bus, atom_id=1)
    context.subscribe_all(_firehose)

    await bus.publish("some.unrelated.event", {"x": 1}, publisher="99")
    await bus.publish("another.event.nobody.named", {"y": 2}, publisher="100")
    # V3.0: النشر إيداع لا تسليم — الفحص يقيس بعد drain لا بعد الإيداع.
    assert await bus.drain(timeout_s=5.0)

    names = [n for n, _ in received]
    assert "some.unrelated.event" in names
    assert "another.event.nobody.named" in names
    assert len(received) == 2, "لازم توصل كل حدث، بلا اختيار أسماء"
