"""RoutingService: decides whether one news item is worth sending to Telegram.

Owner order 2026-08-20 (stamp NQ): the bot must not stay Nasdaq-only.

What was wrong: this gate carried its own Nasdaq/tech keyword list
("nasdaq", "tech", "chip", "nvidia", "apple"...) and ran *after*
NewsService had already filtered by relevance. Two narrowing filters in a
row meant a Fed statement about rates, a gold move or a sterling story
could pass the first gate and be silently dropped by this one -- so the
bot published Nasdaq-flavoured items only.

What it does now: NewsService already decides relevance and records *why*
(the owner symbol or the macro term that matched). This gate trusts that
decision and only enforces what it alone can check -- that the item is
actually sendable (has a headline). The fallback keyword list is kept for
items that arrive without a `why` (older callers), and it now covers the
owner's seven symbols and macro terms, not tech names.
"""

from __future__ import annotations

from typing import Dict

# Fallback only -- used when the item carries no `why` from NewsService.
# Covers the owner's symbols (indices, gold, crypto, majors) and macro.
FALLBACK_KEYWORDS = (
    "fed", "federal reserve", "fomc", "powell", "rate", "inflation", "cpi",
    "pce", "payroll", "jobless", "unemployment", "gdp", "treasury", "yield",
    "tariff", "dollar", "recession", "stimulus", "debt",
    "nasdaq", "s&p", "dow", "gold", "bullion", "bitcoin", "btc", "ether",
    "euro", "ecb", "sterling", "pound", "boe", "oil", "crude",
    "stock", "market", "earnings",
)


class RoutingService:
    """Filters news items down to the ones actually worth a Telegram push."""

    def __init__(self) -> None:
        self.relevance_keywords = list(FALLBACK_KEYWORDS)

    def get_routing_decision(self, item: Dict[str, object]) -> Dict[str, bool]:
        """Decide whether one news item should be sent.

        Args:
            item: A news item dict; `title` is required. `why` (set by
                NewsService) marks an item whose relevance was already
                established -- it is trusted and not re-filtered.

        Returns:
            ``{"send_to_telegram": bool}``.
        """
        title = str(item.get("title") or "").strip()
        if not title:
            return {"send_to_telegram": False}

        # Relevance already decided upstream -- do not narrow it again.
        if str(item.get("why") or "").strip():
            return {"send_to_telegram": True}

        text = f"{title} {item.get('summary', '')}".lower()
        return {"send_to_telegram": any(k in text for k in self.relevance_keywords)}
