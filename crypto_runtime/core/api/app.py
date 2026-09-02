"""
Core.api.app
=============
طبقة REST + WebSocket خام للنواة — **بلا أي لوحة HTML** (اللوحة بطبقة ٢).
البروتوكولات: REST API · WebSocket · JSON.

تكشف حقائق النواة الخام للمراقبة (Registry/EventBus/Metrics/Journal +
firehose لكل الأحداث على /ws/events)، وتقدّم خُطّافات تحكّم ميكانيكية
(إيقاف/تشغيل/إعادة فحص) تستدعيها طبقة ٢ — القرار فوق والميكانيكا هنا
(Article 30). أفعال التحكّم تُسجَّل بالجورنال (الأثر يبقى بالنواة، فطبقة ٢
بلا ذاكرة). لا اسم ولا رقم ذرة مكتوب هنا؛ كل ما يُعرض من Registry ديناميكيًا.
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
import logging
import uuid
from dataclasses import asdict
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from core.__version__ import CORE_VERSION
from core.bootloader import BootReport
from core.event_bus import EventBus
from core.lifecycle import call_lifecycle, LifecycleTimeout
from core.journal import Journal
from core.metrics import Metrics
from core.registry import AtomRecord, Registry
from core.logger import current_trace_id  # ⚠️ استيراد حاوية التتبع السياقية
from core.contracts.atom import AtomState

_log = logging.getLogger("quant_nq.core.api")


def _encode_ws(message: dict) -> str:
    """ترميز مضغوط موحّد لرسائل قناة المراقبة (أي قيمة عصيّة تتحوّل نصًّا)."""
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"), default=str)


class _DroppingQueue:
    """صفّ نصوص جاهزة للإرسال: العنصر جسم JSON **بلا** قوس الإغلاق الأخير،
    والصفّ يُلحق حقول التدفّق (تسلسل لكل عميل + بيان الفجوة عند الإسقاط —
    عقد نسخة 1.11.0) إلحاقًا نصّيًا رخيصًا. هكذا يُرمَّز جسم الحدث الكبير
    **مرة واحدة** للمذيع مهما تعدّد العملاء (مقيس py-spy‏ 2026-08-19:
    الترميز لكل عميل لكل حدث كان يأكل ~19% من الحلقة الرئيسية)."""

    def __init__(self, maxsize: int = 500) -> None:
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._sequence = 0
        self._dropped = 0

    async def put_body(self, open_body: str) -> None:
        """`open_body`: نصّ JSON لكائن الرسالة منزوع قوس الإغلاق الأخير."""
        self._sequence += 1
        text = f'{open_body},"stream_sequence":{self._sequence}}}'
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait(); self._dropped += 1
            except asyncio.QueueEmpty: pass
            text = (f'{open_body},"stream_sequence":{self._sequence}'
                    f',"dropped_total":{self._dropped},"stream_gap":true}}')
            try: self._queue.put_nowait(text)
            except asyncio.QueueFull: self._dropped += 1

    async def put(self, item: dict) -> None:
        await self.put_body(_encode_ws(item)[:-1])

    async def get(self) -> str:
        return await self._queue.get()

    def qsize(self) -> int: return self._queue.qsize()
    @property
    def dropped(self) -> int: return self._dropped


def _serialize_atom(record: AtomRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.manifest.name,
        "version": record.manifest.version,
        "critical": record.critical,
        "state": record.state.value,
        "health": (
            {"state": record.last_health.state.value, "message": record.last_health.message}
            if record.last_health else None
        ),
        "restart_count": record.restart_count,
        "last_error": record.last_error,
        "metadata": record.manifest.metadata,
    }


class TraceIdMiddleware(BaseHTTPMiddleware):
    """⚠️ تطبيق المادة 30 و 35: حقن معرّف التتبع لكل طلب API لربط سجلات النواة بالطلبات الخارجية"""
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
        token = current_trace_id.set(trace_id)
        try:
            response = await call_next(request)
            response.headers["x-trace-id"] = trace_id
            return response
        finally:
            current_trace_id.reset(token)


def create_app(
    registry: Registry,
    event_bus: EventBus,
    metrics: Metrics,
    journal: Journal,
    get_boot_report: Callable[[], BootReport | None] = lambda: None,
    api_key: str | None = None,
    health_manager: Any = None,
    control_event_publisher: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
) -> FastAPI:
    
    app = FastAPI(title="QUANT_NQ Core", version=CORE_VERSION)
    app.add_middleware(TraceIdMiddleware)

    async def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
        if api_key is None:
            return
        if x_api_key is None or not secrets.compare_digest(x_api_key, api_key):
            # ⚠️ تطبيق المادة 89: أخطاء مهيكلة برموز معيارية
            raise HTTPException(401, detail={"code": 4001, "message": "مفتاح API غير صحيح أو مفقود (ترويسة X-API-Key)"})

    _auth = Depends(_require_api_key)

    @app.get("/api/health", dependencies=[_auth])
    async def health() -> dict:
        return {"status": "ok", "core_version": CORE_VERSION, "atom_count": len(registry)}

    @app.get("/api/boot-report", dependencies=[_auth])
    async def boot_report() -> dict:
        report = get_boot_report()
        if report is None:
            raise HTTPException(404, detail={"code": 1000, "message": "لم يكتمل الإقلاع بعد"})
        data = asdict(report)
        data["scan_failures"] = [
            {"path": str(f.path), "error": f.error} for f in report.scan_failures
        ]
        return data

    @app.get("/api/atoms", dependencies=[_auth])
    async def list_atoms() -> list[dict]:
        return [_serialize_atom(r) for r in registry.all()]

    @app.get("/api/atoms/{atom_id}", dependencies=[_auth])
    async def get_atom(atom_id: int) -> dict:
        record = registry.find(atom_id)
        if record is None:
            raise HTTPException(404, detail={"code": 3201, "message": f"لا توجد ذرة بالمعرّف {atom_id}"})
        return _serialize_atom(record)

    @app.get("/api/metrics", dependencies=[_auth])
    async def get_metrics() -> dict:
        return metrics.snapshot()

    @app.get("/api/bus-stats", dependencies=[_auth])
    async def get_bus_stats() -> dict:
        """عدّادات الناقل الخام لكل اسم حدث — قراءة فقط، بلا منطق.

        ٢٠٢٦-٠٨-٣١ (فتح نواة بإذن المالك الصريح «NQ — افتح النواة»):
        `EventBus.stats()` موجودة منذ ٠٢/العيون وفيها `published/delivered/
        dropped/timeout/…` لكل حدث، لكنّها لم تكن معروضة إطلاقًا — فكان
        إثبات إسقاط حدث بعينه مستحيلًا من الخارج، وهو ما احتاجه تشخيص
        تجمّد مرجع الوقت. العرض قراءة محضة ولا يغيّر أي سلوك.
        """
        return event_bus.stats()

    @app.get("/api/journal", dependencies=[_auth])
    async def get_journal(n: int = 100) -> list[dict]:
        n = max(1, min(n, 1000))
        return [asdict(e) for e in journal.tail(n)]

    @app.post("/api/atoms/{atom_id}/stop", dependencies=[_auth])
    async def stop_atom(atom_id: int) -> dict:
        record = registry.find(atom_id)
        if record is None:
            raise HTTPException(404, detail={"code": 3201, "message": f"لا توجد ذرة بالمعرّف {atom_id}"})
        
        if record.state != AtomState.RUNNING:
            raise HTTPException(409, detail={
                "code": 3003,
                "message": f"الذرة {atom_id} ليست في حالة running (الحالة: {record.state.value})",
            })

        # المادة 11: إيقاف الذرة يجب أن يوقف مراقبتها في نفس اللحظة —
        # وإلا رأى Health Manager ذرة غير RUNNING فأنهى حلقته، أو أسوأ:
        # حاول إعادة تشغيل ذرة أوقفها المشغّل عمدًا.
        if health_manager is not None:
            health_manager.unwatch(atom_id)
        try:
            await call_lifecycle(record.instance.stop(), "stop")
        except LifecycleTimeout as exc:  # ورقة ٠٦: تعلّقت الذرة — لا نجمّد الطلب للأبد
            registry.set_state(atom_id, AtomState.FAILED)
            registry.set_error(atom_id, str(exc))
            journal.record(atom_id, "stop_failed", {"error": str(exc), "via": "command"})
            await event_bus.publish(
                "core.atom.failed", {"atom_id": atom_id, "error": str(exc)}, publisher="core.api"
            )
            raise HTTPException(
                504, detail={"code": 3003, "message": f"تعلّقت الذرة في الطور '{exc.phase}' وتجاوزت المهلة"}
            ) from exc
        except Exception as exc:  # noqa: BLE001 — كود ذرة خارجي
            registry.set_state(atom_id, AtomState.FAILED)
            registry.set_error(atom_id, str(exc))
            journal.record(atom_id, "stop_failed", {"error": str(exc), "via": "command"})
            await event_bus.publish(
                "core.atom.failed", {"atom_id": atom_id, "error": str(exc)}, publisher="core.api"
            )
            raise HTTPException(
                500, detail={"code": 3003, "message": f"فشل إيقاف الذرة: {exc}"}
            ) from exc

        registry.set_state(atom_id, AtomState.STOPPED)
        journal.record(atom_id, "stopped", {"via": "command"})  # ورقة ١١: أثر الأمر بالنواة
        await event_bus.publish("core.atom.stopped", {"atom_id": atom_id}, publisher="core.api")
        return {"status": "stopped", "atom_id": atom_id}

    @app.post("/api/atoms/{atom_id}/start", dependencies=[_auth])
    async def start_atom(atom_id: int) -> dict:
        """خُطّاف رجوع الذرة الموقّفة (ورقة ٠٦-ج): يعيد تشغيل ذرة STOPPED. النواة
        تقدّم الآلية فقط؛ الزرّ نفسه بطبقة ٢ (لوحة الحوكمة)."""
        record = registry.find(atom_id)
        if record is None:
            raise HTTPException(404, detail={"code": 3201, "message": f"لا توجد ذرة بالمعرّف {atom_id}"})
        if record.state != AtomState.STOPPED:
            raise HTTPException(409, detail={
                "code": 3003,
                "message": f"الذرة {atom_id} ليست STOPPED (الحالة: {record.state.value})",
            })
        registry.set_state(atom_id, AtomState.STARTING)
        try:
            await call_lifecycle(record.instance.start(), "start")
        except Exception as exc:  # noqa: BLE001 — كود ذرة خارجي (يشمل LifecycleTimeout)
            registry.set_state(atom_id, AtomState.FAILED)
            registry.set_error(atom_id, str(exc))
            journal.record(atom_id, "start_failed", {"error": str(exc), "via": "command"})
            await event_bus.publish(
                "core.atom.failed", {"atom_id": atom_id, "error": str(exc)}, publisher="core.api"
            )
            raise HTTPException(500, detail={"code": 3003, "message": f"فشل تشغيل الذرة: {exc}"}) from exc
        registry.set_state(atom_id, AtomState.RUNNING)
        journal.record(atom_id, "started", {"via": "command"})  # ورقة ١١: أثر الأمر بالنواة
        if health_manager is not None:
            health_manager.watch(atom_id)
        await event_bus.publish("core.atom.started", {"atom_id": atom_id}, publisher="core.api")
        return {"status": "started", "atom_id": atom_id}

    @app.post("/api/rescan", dependencies=[_auth])
    async def trigger_rescan() -> dict:
        # الفحص اليدوي يتجاوز بوّابة بصمة القرص دائمًا (الدوري وحده يوفَّر).
        await event_bus.publish("core.system.rescan_requested", {"force": True},
                                publisher="core.api")
        return {"status": "rescan_requested"}

    @app.post("/api/events", dependencies=[_auth])
    async def publish_control_event(body: dict[str, Any]) -> dict:
        """Generic control ingress with policy supplied by an outer adapter."""
        if control_event_publisher is None:
            raise HTTPException(503, detail={"code": 4101, "message": "control adapter unavailable"})
        name = str(body.get("name") or "").strip()
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        try:
            await control_event_publisher(name, payload)
        except PermissionError as exc:
            raise HTTPException(403, detail={"code": 4103, "message": str(exc) or "event not allowed"}) from exc
        return {"status": "accepted", "event": name}

    # ورقة ١١: firehose — **مذيع واحد** لكل العملاء بدل اشتراك لكل عميل:
    # جسم الحدث يُرمَّز مرة واحدة مهما تعدّدت اللوحات، وكل عميل يُلحق تسلسله
    # نصّيًا في صفّه (عقد 1.11.0 محفوظ). المذيع يقرأ ولا يعدّل الحمولة —
    # فيستلمها بلا نسخة (عهد isolate_payload=False الصريح في الناقل).
    ws_clients: set[_DroppingQueue] = set()

    async def _broadcast(event_name: str, payload: dict) -> None:
        if not ws_clients:
            return
        open_body = ('{"type":"event","name":' + _encode_ws(event_name)
                     + ',"payload":' + _encode_ws(payload))
        for client_queue in list(ws_clients):
            await client_queue.put_body(open_body)

    event_bus.subscribe_all(_broadcast, subscriber="api.websocket",
                            isolate_payload=False)

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        selected_protocol = None
        supplied = websocket.headers.get("x-api-key")
        protocols = [part.strip() for part in websocket.headers.get("sec-websocket-protocol", "").split(",")]
        if "quant-nq" in protocols: selected_protocol = "quant-nq"
        if supplied is None:
            for protocol in protocols:
                if not protocol.startswith("quant-nq-key."): continue
                try:
                    encoded = protocol.split(".", 1)[1]
                    supplied = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
                except (ValueError, UnicodeDecodeError): supplied = None
                break
        if api_key is not None and (supplied is None or not secrets.compare_digest(supplied, api_key)):
            await websocket.close(code=4401, reason="مفتاح API غير صحيح أو مفقود")
            return
        await websocket.accept(subprotocol=selected_protocol)
        queue = _DroppingQueue(maxsize=500)

        async def snapshot_message() -> dict:
            return {
                "type": "snapshot",
                "atoms": [_serialize_atom(r) for r in registry.all()],
                "metrics": metrics.snapshot(),
            }

        try:
            await websocket.send_text(_encode_ws(await snapshot_message()))
        except Exception:  # noqa: BLE001 — العميل انقطع قبل أول رسالة
            return

        # ٠٣: المنضمّ المتأخّر يستلم آخر حالة مخزَّنة لكل تدفّق فورًا (كان
        # اشتراكُ كلِّ عميل يعيدها له؛ مع المذيع الواحد نعيدها له هنا صراحة).
        for event_name, payload in event_bus.last_states():
            await queue.put({"type": "event", "name": event_name, "payload": payload})

        ws_clients.add(queue)

        async def periodic_snapshots() -> None:
            while True:
                await asyncio.sleep(3)
                await queue.put(await snapshot_message())

        snapshot_task = asyncio.create_task(periodic_snapshots())
        try:
            while True:
                await websocket.send_text(await queue.get())
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001 — انقطاع مفاجئ للقناة
            # المادة 81: انقطاع عميل مراقبة لا يجوز أن يترك مهامًا أو
            # اشتراكات معلّقة في الناقل.
            _log.debug("انتهت قناة WebSocket بخطأ: %s", exc)
        finally:
            snapshot_task.cancel()
            try:
                await snapshot_task
            except asyncio.CancelledError:
                pass
            ws_clients.discard(queue)

    return app