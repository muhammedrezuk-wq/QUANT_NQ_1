from __future__ import annotations

import json

import pytest

from core.api.app import _DroppingQueue, _encode_ws


@pytest.mark.asyncio
async def test_queue_reports_sequence_and_overflow_gap() -> None:
    # عقد 1.11.0 كما هو (تسلسل لكل عميل + بيان الفجوة عند الإسقاط)، مع دلالة
    # 1.16.0: الصفّ يحمل نصوص JSON جاهزة للإرسال (ترميز جسم الحدث مرة واحدة
    # للمذيع، والحقول الختامية إلحاق نصّي) — فالحارس يفكّ النصّ ويفحص المعنى.
    queue = _DroppingQueue(maxsize=2)
    await queue.put({"value": 1})
    await queue.put({"value": 2})
    await queue.put({"value": 3})
    second = json.loads(await queue.get())
    third = json.loads(await queue.get())
    assert second["value"] == 2 and second["stream_sequence"] == 2
    assert third["value"] == 3 and third["stream_sequence"] == 3
    assert third["stream_gap"] is True
    assert third["dropped_total"] == 1
    assert queue.dropped == 1


@pytest.mark.asyncio
async def test_prebuilt_body_and_dict_paths_produce_identical_wire_shape() -> None:
    # مسار المذيع (جسم مبني مسبقًا) ومسار القاموس يجب أن يخرجا نفس شكل السلك.
    via_dict = _DroppingQueue()
    await via_dict.put({"type": "event", "name": "n", "payload": {"a": [1, 2]}})
    via_body = _DroppingQueue()
    open_body = '{"type":"event","name":' + _encode_ws("n") + ',"payload":' + _encode_ws({"a": [1, 2]})
    await via_body.put_body(open_body)
    assert json.loads(await via_dict.get()) == json.loads(await via_body.get())
