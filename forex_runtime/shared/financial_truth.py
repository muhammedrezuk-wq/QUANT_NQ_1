"""ورقة ١٥ §٦ · §١٢-3 — منع السرقة: قسم المحافظ صاحب الحقيقة المالية.

القاعدة المقفولة (§٦.٤):
    ١ — `653`–`659` أصحاب الحقيقة الوحيدون للرصيد وحقوق الملكية والهامش
        والهامش الحرّ والربح/الخسارة والصفقات المفتوحة.
    ٢ — التسع السارقة تقرأ من هنا بدل `platform.account.state`.
    ٣ — قارئ الهوية فقط (`broker` · `account_id` · `connected`) يبقى على
        `619` — ليست سرقة.

وعند غياب صاحب الحقيقة (اختبار §١٣-11): المستهلك **يُعلن النقص** على
`financial.truth.shortage` ولا يحسب الرقم بنفسه ولا يستنسخه من الخام.
"""
from __future__ import annotations

import math
from typing import Any

EVENT_BALANCE = "portfolio.balance.state"           # 653
EVENT_EQUITY = "portfolio.equity.state"             # 654
EVENT_MARGIN = "portfolio.margin.state"             # 655
EVENT_FREE_MARGIN = "portfolio.free_margin.state"   # 656
EVENT_MARGIN_LEVEL = "portfolio.margin_level.state" # 657
EVENT_PNL = "portfolio.pnl.state"                   # 658
EVENT_OPEN_POSITIONS = "portfolio.open_positions.state"  # 659
EVENT_SHORTAGE = "financial.truth.shortage"

OWNER_OF = {
    "balance": "653", "equity": "654", "margin": "655",
    "free_margin": "656", "margin_level": "657",
    "floating_pnl": "658", "realized_pnl": "658", "net_pnl": "658",
    "open_count": "659",
}

REASON_MISSING = "FINANCIAL_TRUTH_MISSING"


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _text(value: Any) -> str:
    return str(value or "").strip()


class FinancialTruth:
    """مخزن قراءة فقط لأرقام قسم المحافظ — لا يحسب رقمًا ولا يشتقّه.

    مفهرَس بالحساب. و`broker` يُلتقط من حمولة صاحب الحقيقة إن حملته،
    وإلا من دفتر الهوية الذي تملكه الذرّة نفسها (`619` — هوية فقط).
    """

    __slots__ = ("_by_account", "_reader", "_shortages")

    def __init__(self, reader: str) -> None:
        self._by_account: dict[str, dict[str, Any]] = {}
        self._reader = str(reader)
        self._shortages = 0

    # ── استقبال الحقيقة ───────────────────────────────────────────────────
    def absorb(self, field: str, payload: Any) -> str:
        """يخزّن حقلًا واحدًا من صاحبه — ويعيد رقم الحساب أو فراغًا."""
        if not isinstance(payload, dict):
            return ""
        account = _text(payload.get("account_id"))
        if not account:
            return ""
        book = self._by_account.setdefault(account, {})
        value = _num(payload.get(field))
        if value is not None:
            book[field] = value
        broker = _text(payload.get("broker"))
        if broker:
            book["broker"] = broker
        stamp = payload.get("measured_at")
        if stamp is not None:
            book["measured_at_%s" % field] = stamp
        return account

    def absorb_positions(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        account = _text(payload.get("account_id"))
        if not account:
            return ""
        book = self._by_account.setdefault(account, {})
        count = _num(payload.get("open_count"))
        if count is not None:
            book["open_count"] = count
        floating = _num(payload.get("floating_pnl"))
        if floating is not None:
            book["floating_pnl"] = floating
        return account

    # ── القراءة ───────────────────────────────────────────────────────────
    def get(self, account: str, field: str) -> float | None:
        book = self._by_account.get(_text(account))
        if book is None:
            return None
        value = book.get(field)
        return value if isinstance(value, (int, float)) else None

    def broker(self, account: str) -> str:
        book = self._by_account.get(_text(account)) or {}
        return _text(book.get("broker"))

    def has(self, account: str, field: str) -> bool:
        return self.get(account, field) is not None

    @property
    def accounts(self) -> int:
        return len(self._by_account)

    def account_ids(self) -> list[str]:
        return sorted(self._by_account)

    def export(self) -> list[dict[str, Any]]:
        """للقطة (snapshot) — أرقام صاحب الحقيقة كما وردت، بلا اشتقاق."""
        return [{"account_id": account, **book}
                for account, book in sorted(self._by_account.items())]

    def load(self, rows: Any) -> None:
        self._by_account = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            account = _text(row.get("account_id"))
            if account:
                self._by_account[account] = {k: v for k, v in row.items()
                                             if k != "account_id"}

    @property
    def shortages(self) -> int:
        return self._shortages

    def shortage_body(self, account: str, field: str, *, broker: str = "",
                      detail: str = "") -> dict[str, Any]:
        """حمولة إعلان النقص — تُنشَر ولا يُحسب الرقم محلّيًّا."""
        self._shortages += 1
        return {"event_name": EVENT_SHORTAGE, "reader": self._reader,
                "account_id": _text(account),
                "broker": _text(broker) or self.broker(account),
                "field": field, "owner": OWNER_OF.get(field, ""),
                "reason": REASON_MISSING, "detail": _text(detail),
                "read_only": True}


_EVENT_OF_FIELD = {
    "equity": EVENT_EQUITY, "balance": EVENT_BALANCE, "margin": EVENT_MARGIN,
    "free_margin": EVENT_FREE_MARGIN, "margin_level": EVENT_MARGIN_LEVEL,
}


def bind_truth(atom: Any, context: Any, truth: FinancialTruth,
               fields: tuple[str, ...], *, after: Any = None) -> None:
    """يربط الذرّة بأصحاب الحقيقة المطلوبين فقط — لا اشتراك زائد.

    ويُثبّت لكل حقل مُعالِجًا مُسمّى على الذرّة (`_on_truth_equity` …) كي
    يكون قابلًا للاستدعاء المباشر في الفحص، تمامًا كـ`_on_account`.

    `after` — دالة اختيارية `async (account) -> None` تُستدعى بعد كل تحديث،
    كي تعيد الذرّة تقييم حالتها بالقيمة الجديدة بلا انتظار حدث آخر.
    """
    for field in fields:
        def make(field: str = field) -> Any:
            async def handler(payload: Any) -> None:
                if field == "open_positions":
                    account = truth.absorb_positions(payload)
                elif field == "pnl":
                    account = ""
                    for name in ("floating_pnl", "realized_pnl", "net_pnl"):
                        account = truth.absorb(name, payload) or account
                else:
                    account = truth.absorb(field, payload)
                if account and after is not None:
                    await after(account)
            return handler

        handler = make()
        setattr(atom, "_on_truth_%s" % field, handler)
        event = (EVENT_OPEN_POSITIONS if field == "open_positions"
                 else EVENT_PNL if field == "pnl" else _EVENT_OF_FIELD[field])
        context.subscribe(event, handler)
