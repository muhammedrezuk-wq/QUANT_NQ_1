"""
Core.lifecycle — سياسة دورة حياة الذرة الموحّدة (فتحة النواة V2.0 — ورقة ٠٦).

الجذر: نداء دورة حياة ذرة (`initialize/start/stop/shutdown`) غير موثوق — قد يعلّق
للأبد فيجمّد المحرّك كلّه (المحرّك محميّ بمهلة أثناء الإقلاع فقط، ومكشوف في التحميل
الحار وإعادة التشغيل وزرّ الإيقاف). الحل من الجذر: **مهلة موحّدة + دالة لفّ واحدة**
تستعملها كل المسارات خارج الإقلاع، فلا تتكرّر السياسة ولا تفلت جهة رابعة مستقبلًا.

عند التجاوز يُرفع `LifecycleTimeout` حاملًا اسم الطور — والمستدعي يسم الذرة FAILED
بسبب مميّز (`LIFECYCLE_TIMEOUT:<phase>`) فتميّز طبقة ٢ «مات بمهلة» عن «انهار» بلا
حالة مختومة جديدة، ثم يكمل المحرّك جولته.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable

LIFECYCLE_TIMEOUT_S = 30.0


class LifecycleTimeout(Exception):
    """تجاوز نداء دورة حياة المهلة المسموحة — سبب مميّز لطبقة ٢."""

    def __init__(self, phase: str) -> None:
        self.phase = phase
        super().__init__(f"LIFECYCLE_TIMEOUT:{phase}")


async def call_lifecycle(
    awaitable: Awaitable, phase: str, *, timeout: float = LIFECYCLE_TIMEOUT_S
) -> None:
    """ينفّذ نداء دورة حياة بسقف زمني. تجاوز المهلة = `LifecycleTimeout(phase)`.
    أي استثناء آخر من الذرة يمرّ كما هو ليعالجه المستدعي."""
    try:
        await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise LifecycleTimeout(phase) from exc
