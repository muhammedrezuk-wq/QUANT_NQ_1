"""ورقة ١٥ §٣ · §٤ · §٥ · §٦ — عقد موحّد ومساعدات العقد لذرّات المجتمع.

كل ذرّة عاملة في مسار القرار تُطبع على حقول العقد الموحّد:

    الهوية    : account_id · broker · symbol · section_id · atom_id
    الرأي     : direction · strength · confidence · weight · ratio
    النضج     : current_depth · required_depth
    الحالة    : ANALYZING  | NOT_READY  | READY  | STALE
                | INVALID   | ERROR     | DORMANT
    الزمن    : timestamp · sequence · source_timestamp
"""
from __future__ import annotations

from typing import Any

# ── الحالات السبع (§٤) ─────────────────────────────────────────────────────
STATE_ANALYZING = "ANALYZING"
STATE_NOT_READY = "NOT_READY"
STATE_READY = "READY"
STATE_STALE = "STALE"
STATE_INVALID = "INVALID"
STATE_ERROR = "ERROR"
STATE_DORMANT = "DORMANT"

ALL_STATES = (
    STATE_ANALYZING, STATE_NOT_READY, STATE_READY, STATE_STALE,
    STATE_INVALID, STATE_ERROR, STATE_DORMANT,
)

# ── حقول العقد الموحّد (§٣) ────────────────────────────────────────────────
OPINION_FIELDS = ("direction", "strength", "confidence", "weight", "ratio")
MATURITY_FIELDS = ("current_depth", "required_depth")
IDENTITY_FIELDS = ("account_id", "broker", "symbol", "section_id", "atom_id")
TIME_FIELDS = ("timestamp", "sequence", "source_timestamp")
CONTRACT_FIELDS = (
    IDENTITY_FIELDS + OPINION_FIELDS + MATURITY_FIELDS + ("state",) + TIME_FIELDS)


# ── أحداث مرجعية (§٦) ─────────────────────────────────────────────────────
# كل ذرّة تسرق رقمًا ماليًّا تُعاد توصيلها إلى صاحب الحقيقة أدناه.
EVENT_BALANCE = "portfolio.balance.state"          # 653
EVENT_EQUITY = "portfolio.equity.state"            # 654
EVENT_MARGIN = "portfolio.margin.state"            # 655
EVENT_FREE_MARGIN = "portfolio.free_margin.state"  # 656
EVENT_MARGIN_LEVEL = "portfolio.margin_level.state"# 657
EVENT_PNL = "portfolio.pnl.state"                  # 658 — الربح/الخسارة
EVENT_OPEN_POSITIONS = "portfolio.open_positions.state"  # 659
EVENT_FINANCIAL_SHORTAGE = "financial.truth.shortage"


# ─────────────────────────────────────────────────────────────────────────────
# مساعدات
# ─────────────────────────────────────────────────────────────────────────────
import math


def _num(value: Any, fallback: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if math.isfinite(result) else fallback


def clip(value: Any, low: float, high: float, fallback: float = 0.0) -> float:
    n = _num(value, fallback)
    return max(low, min(high, n))


def dir_norm(value: Any) -> float:
    """direction: -100 / +100 (0 = حياد) — ليس احتمال نجاح."""
    return clip(value, -100.0, 100.0)


def pct(value: Any) -> float:
    return clip(value, 0.0, 100.0)


def text(value: Any) -> str:
    return str(value or "").strip()


def has_full_identity(payload: dict[str, Any]) -> bool:
    """اختبار وجود الهوية الكاملة — تُرفض الحمولة وتُعلَن إن ناقصة."""
    a = text(payload.get("account_id"))
    b = text(payload.get("broker"))
    s = text(payload.get("symbol") or payload.get("asset"))
    return bool(a and b and s)


def stamp_unified(
    payload: dict[str, Any],
    *,
    section_id: str,
    atom_id: Any,
    state: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """يطبع الحمولة على العقد الموحّد ويُبقي كل الحقول الأصلية — لا يصدر
    حقولًا فارغة ولا يحقن أرقامًا افتراضية في الرأي/النضج.

    الكلام الحاكم: «ما يحسب افتراضيا هو كذب» (§١١ ما لا نفعله).
    """
    out = dict(payload)
    account = text(payload.get("account_id"))
    broker = text(payload.get("broker"))
    symbol = text(payload.get("symbol") or payload.get("asset"))
    if account: out["account_id"] = account
    if broker: out["broker"] = broker
    if symbol: out["symbol"] = symbol
    out["section_id"] = section_id
    out["atom_id"] = atom_id
    out["direction"] = dir_norm(out.get("direction") or 0.0)
    out["strength"] = pct(out.get("strength") or 0.0)
    out["confidence"] = pct(out.get("confidence") or 0.0)
    out["weight"] = pct(out.get("weight") or 0.0)
    out["ratio"] = pct(out.get("ratio") or out.get("weight") or 0.0)
    out["current_depth"] = pct(out.get("current_depth") or 0.0)
    out["required_depth"] = pct(out.get("required_depth") or 100.0)
    final_state = state or out.get("state")
    if final_state:
        out["state"] = final_state
    if overrides:
        for key, value in overrides.items():
            if value is not None: out[key] = value
    return out
