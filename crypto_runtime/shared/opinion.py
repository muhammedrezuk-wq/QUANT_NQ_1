"""العقد الموحّد — ورقة ١٥ §٣ · §٤.

ما تُرسله كل ذرّة عاملة في مسار القرار:

    الهوية  : account_id · broker · symbol · section_id · atom_id
    الرأي    : direction (-100..+100) · strength (0..100) · confidence (0..100)
               · weight (0..100) · ratio (0..100)
    النضج    : current_depth · required_depth (0..100)
    الحالة  : ANALYZING | NOT_READY | READY | STALE | INVALID | ERROR | DORMANT
    الزمن    : timestamp · sequence · source_timestamp

القواعد المقفولة:
  * الوزن لا يُطبَّق قبل READY.
  * العمق لا يغيّر الاتجاه.
  * الثقة نضج أدلة — ليست احتمال ربح.
  * الحمولة بلا هوية كاملة (account_id + broker + symbol) تُرفض وتُعلَن —
    لا حساب افتراضي (§٩).
"""
from __future__ import annotations

import math
from typing import Any

# ── الحالات السبع (§٤) ─────────────────────────────────────────────────────
STATE_ANALYZING = "ANALYZING"
STATE_NOT_READY = "NOT_READY"
STATE_READY = "READY"
STATE_STALE = "STALE"
STATE_INVALID = "INVALID"
STATE_ERROR = "ERROR"
STATE_DORMANT = "DORMANT"

# إعلان النقص المالي (§٦ — اختبار ١١): صاحب الحقيقة معطّل أو قيمته ناقصة ⇒
# المستهلك يُعلن النقص ولا يحسب الرقم بنفسه.
EVENT_FINANCIAL_SHORTAGE = "financial.truth.shortage"

ALL_STATES = frozenset({
    STATE_ANALYZING, STATE_NOT_READY, STATE_READY,
    STATE_STALE, STATE_INVALID, STATE_ERROR, STATE_DORMANT,
})

# حالة الجاهزية الوحيدة التي تدخل التجميع (§٤).
READY_STATES = frozenset({STATE_READY})

OPINION_FIELDS = ("direction", "strength", "confidence", "weight", "ratio")
MATURITY_FIELDS = ("current_depth", "required_depth")
IDENTITY_FIELDS = ("account_id", "broker", "symbol", "section_id", "atom_id")
TIME_FIELDS = ("timestamp", "sequence", "source_timestamp")

CONTRACT_FIELDS = IDENTITY_FIELDS + OPINION_FIELDS + MATURITY_FIELDS + ("state",) + TIME_FIELDS


def _finite(value: Any, fallback: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if math.isfinite(result) else fallback


def clip(value: Any, low: float = 0.0, high: float = 100.0,
         fallback: float = 0.0) -> float:
    number = _finite(value, fallback)
    return max(low, min(high, number))


def direction(value: Any, fallback: float = 0.0) -> float:
    """الاتجاه -100..+100 — بيع قوي ← حياد → شراء قوي. ليس احتمال نجاح."""
    return clip(value, -100.0, 100.0, fallback)


def text(value: Any) -> str:
    return str(value or "").strip()


def identity_of(payload: dict[str, Any]) -> tuple[str, str, str]:
    """(account_id, broker, symbol) — أو ثلاثة فراغات إن ناقص أيّ عنصر.

    الهوية الناقصة تُرفض وتُعلَن؛ لا حساب افتراضي (§٩).
    """
    account = text(payload.get("account_id"))
    broker = text(payload.get("broker"))
    symbol = text(payload.get("symbol") or payload.get("asset"))
    return account, broker, symbol


def has_identity(payload: dict[str, Any]) -> bool:
    account, broker, symbol = identity_of(payload)
    return bool(account and broker and symbol)


def stamp(payload: dict[str, Any], *, section_id: str, atom_id: Any,
          state: str | None = None, direction: Any = None,
          strength: Any = None, confidence: Any = None,
          weight: Any = None, ratio: Any = None,
          current_depth: Any = None, required_depth: Any = None,
          timestamp: Any = None, sequence: Any = None,
          source_timestamp: Any = None) -> dict[str, Any]:
    """يطبّع حمولة على العقد الموحّد — يُبقي كل الحقول الأصلية ويملأ الناقص.

    لا يُغيَّر اتجاه ولا ثقة ولا وزن بقيم مفتعلة: أي حقل غير مُمرَّر يحتفظ
    بقيمته إن وُجدت في الحمولة، وإلا يُملأ صفرًا صريحًا (لا افتراضيات خفية).
    """
    out = dict(payload)
    out.setdefault("account_id", text(payload.get("account_id")))
    out.setdefault("broker", text(payload.get("broker")))
    out.setdefault("symbol", text(payload.get("symbol") or payload.get("asset")))
    out["section_id"] = section_id
    out["atom_id"] = text(atom_id)
    out["direction"] = direction(out.get("direction")) if direction is None else direction
    out["strength"] = clip(strength if strength is not None else out.get("strength"))
    out["confidence"] = clip(confidence if confidence is not None else out.get("confidence"))
    out["weight"] = clip(weight if weight is not None else out.get("weight"))
    out["ratio"] = clip(ratio if ratio is not None else out.get("ratio"))
    out["current_depth"] = clip(current_depth if current_depth is not None else out.get("current_depth"))
    out["required_depth"] = clip(required_depth if required_depth is not None else out.get("required_depth"))
    if state is not None:
        out["state"] = state
    elif "state" not in out:
        out["state"] = STATE_ANALYZING
    out.setdefault("timestamp", timestamp)
    out.setdefault("sequence", sequence)
    out.setdefault("source_timestamp",
                   source_timestamp if source_timestamp is not None
                   else out.get("source_timestamp", out.get("timestamp")))
    return out
