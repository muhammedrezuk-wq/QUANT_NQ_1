from __future__ import annotations

import time
from typing import Any

EVENT_ROOM = "decision.room.state"
STATE_AGG_SECTIONS = ("150", "200", "250", "300", "350", "400")
STATUS_OK = "ok"
ID_AGG = "decision_aggregator"


async def emit_room(atom: Any, account: str, broker: str, symbol: str) -> None:
    if atom._context is None:
        return
    sections = atom._room.get((account, broker, symbol), {})
    rows = sorted(sections.values(), key=lambda row: row["section_id"])

    def inclusive(field: str) -> tuple[float | None, float]:
        total = 0.0
        weight_total = 0.0
        for row in rows:
            value = row.get(field)
            weight = row.get("weight")
            if value is None or weight is None or weight <= 0:
                continue
            total += value * weight
            weight_total += weight
        value = round(total / weight_total, 4) if weight_total > 0 else None
        return value, weight_total

    room_direction, direction_weight = inclusive("direction")
    room_strength, _ = inclusive("strength")
    room_confidence, confidence_weight = inclusive("confidence")
    room_readiness, _ = inclusive("readiness_pct")
    room_ratio, ratio_weight = inclusive("ratio")
    now = time.monotonic()
    await atom._context.publish(EVENT_ROOM, {
        "account_id": account,
        "broker": broker,
        "symbol": symbol,
        "id": ID_AGG,
        "room": True,
        "status": STATUS_OK,
        "direction": room_direction,
        "direction_defined": direction_weight > 0,
        "strength": room_strength,
        "confidence": room_confidence,
        "confidence_defined": confidence_weight > 0,
        "readiness_pct": room_readiness,
        "ratio": room_ratio,
        "ratio_defined": ratio_weight > 0,
        "signal": (
            "up" if room_direction is not None and room_direction > 0
            else "down" if room_direction is not None and room_direction < 0
            else "sideways" if room_direction is not None
            else "unknown"
        ),
        "sections_present": [row["section_id"] for row in rows],
        "sections_missing": [
            section_id for section_id in STATE_AGG_SECTIONS
            if section_id not in sections
        ],
        "sections": [
            {
                "section_id": row["section_id"],
                "state": row["state"],
                "direction": row["direction"],
                "direction_sign": row["direction_sign"],
                "strength": row["strength"],
                "confidence": row["confidence"],
                "current_depth": row["current_depth"],
                "required_depth": row["required_depth"],
                "weight": row["weight"],
                "ratio": row["ratio"],
                "unknown_fields": list(row["unknown_fields"]),
                "readiness_pct": row["readiness_pct"],
                "timeframe": row["timeframe"],
                "period_start": row["period_start"],
                "age_s": round(now - row["received_mono"], 1),
            }
            for row in rows
        ],
        "metadata": {
            "method": "decision_room_inclusive_weighted",
            "section_count": len(rows),
            "contract_fields": [
                "direction", "strength", "confidence", "current_depth",
                "required_depth", "weight", "ratio", "state",
            ],
        },
    })
    atom._room_emitted += 1
