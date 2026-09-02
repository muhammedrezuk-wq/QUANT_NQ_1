from __future__ import annotations

import math
from typing import Any

class PulseGuard:
    """Monotonic idempotency guard for calendar pulses, snapshot-friendly."""
    def __init__(self, event_name: str) -> None:
        self.event_name = event_name; self.last_pulse_id = ""; self.last_bucket: float | None = None
        self.duplicates = 0; self.invalid = 0

    def accept(self, payload: Any) -> bool:
        if not isinstance(payload, dict): self.invalid += 1; return False
        pulse_id = str(payload.get("pulse_id") or "")
        try: bucket = float(payload.get("bucket_start"))
        except (TypeError, ValueError): self.invalid += 1; return False
        expected_id = f"{self.event_name}|{int(bucket)}" if math.isfinite(bucket) else ""
        if not math.isfinite(bucket) or bucket != int(bucket) or pulse_id != expected_id:
            self.invalid += 1; return False
        if pulse_id == self.last_pulse_id or (self.last_bucket is not None and bucket <= self.last_bucket):
            self.duplicates += 1; return False
        self.last_pulse_id = pulse_id; self.last_bucket = bucket; return True

    def snapshot(self) -> dict[str, Any]:
        return {"event_name": self.event_name, "last_pulse_id": self.last_pulse_id,
                "last_bucket": self.last_bucket, "duplicates": self.duplicates,
                "invalid": self.invalid}

    def restore(self, state: Any) -> None:
        if not isinstance(state, dict) or state.get("event_name") != self.event_name:
            raise ValueError("INVALID_PULSE_GUARD_STATE")
        pulse_id = str(state.get("last_pulse_id") or ""); bucket = state.get("last_bucket")
        if bucket is not None:
            bucket = float(bucket)
            if not math.isfinite(bucket) or bucket != int(bucket):
                raise ValueError("INVALID_PULSE_BUCKET")
        if pulse_id and (bucket is None or pulse_id != f"{self.event_name}|{int(bucket)}"):
            raise ValueError("INVALID_PULSE_ID")
        self.last_pulse_id = pulse_id; self.last_bucket = bucket
        self.duplicates = int(state.get("duplicates") or 0); self.invalid = int(state.get("invalid") or 0)
