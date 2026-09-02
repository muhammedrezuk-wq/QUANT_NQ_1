from __future__ import annotations

from typing import Any

STATUS_FAILED = "FAILED"
STATUS_RETRY = "RETRY"
STATUS_EXECUTING = "EXECUTING"
EVENT_PENDING = "asset.extraction.execution_requested"
EVENT_MANAGE = "execution.manage.command"


def ticket(value: Any) -> str:
    return str(value or "").strip()


async def retry(atom: Any, payload: dict[str, Any]) -> None:
    """Republish a failed command only after an explicit retry request."""
    if not atom._running or atom._context is None or not isinstance(payload, dict):
        return
    extraction_id = str(payload.get("extraction_id") or payload.get("request_id") or "")
    requested_ticket = ticket(payload.get("ticket"))
    for pending in list(atom._pending_by_ticket.values()):
        if str(pending.get("extraction_id")) != extraction_id:
            continue
        if requested_ticket and ticket(pending.get("ticket")) != requested_ticket:
            continue
        attempts = int(pending.get("attempts") or 0)
        if pending.get("status") != STATUS_FAILED or attempts >= atom._max_attempts:
            continue
        attempts += 1
        request_id = "%s-%s-a%d" % (extraction_id, ticket(pending.get("ticket")), attempts)
        pending.update({"attempts": attempts, "request_id": request_id,
                        "status": STATUS_RETRY, "failure_reason": "",
                        "last_attempt_at": atom._official_time,
                        "created_at": atom._official_time})
        command = dict(pending.get("command") or {})
        command["request_id"] = request_id
        pending["status"] = STATUS_EXECUTING
        await atom._context.publish(EVENT_PENDING, dict(pending))
        await atom._context.publish(EVENT_MANAGE, command)
