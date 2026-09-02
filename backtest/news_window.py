# -*- coding: utf-8 -*-
"""أخبار/تقويم على نافذة تاريخية — قراءة فقط، بلا عناوين مخترَعة.

المصدر: جسر الدماغ (nq_brain / TRADE_DB) إن وُجدت جداول news/calendar.
بهالاستخراج غالبًا فاضي — نعلن الفراغ، ما منلفّق خبر.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _candidate_dbs() -> list[Path]:
    env = os.environ.get("NQ_NEWS_DB") or os.environ.get("NQ_BRIDGE_DB")
    out: list[Path] = []
    if env:
        p = Path(env)
        if os.name == "nt" or (p.is_absolute() and not str(p).startswith("C:") and "AppData" not in p.name):
            out.append(p)
    out.append(ROOT / "forex_runtime" / "var" / "bridge.db")
    appdata = os.environ.get("APPDATA", "")
    if os.name == "nt" and appdata:
        out.append(Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / "nq_brain.db")
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p)
        if key in seen or not p.is_file():
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def news_on_range(start_ts: float | None, end_ts: float | None, limit: int = 80) -> dict[str, Any]:
    """صفوف الأخبار والتقويم داخل [start, end] إن وُجد الجسر."""
    start = float(start_ts or 0.0)
    end = float(end_ts or 1e18)
    limit = max(1, min(int(limit), 200))
    news: list[dict[str, Any]] = []
    calendar: list[dict[str, Any]] = []
    used = None
    error = None
    for path in _candidate_dbs():
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
            con.row_factory = sqlite3.Row
            tabs = {str(r[0]) for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "news" in tabs:
                cols = {str(r[1]) for r in con.execute("PRAGMA table_info(news)")}
                extra = "".join(f",{c}" for c in ("summary", "relevance", "symbols") if c in cols)
                rows = con.execute(
                    "SELECT id,headline,link,source,sentiment_score,impact_level,"
                    f"published_at,written_at{extra} FROM news "
                    "WHERE published_at >= ? AND published_at <= ? "
                    "ORDER BY published_at DESC LIMIT ?",
                    (start, end, limit),
                ).fetchall()
                for r in rows:
                    news.append({k: r[k] for k in r.keys()})
            if "calendar" in tabs:
                rows = con.execute(
                    "SELECT id,title,country,currency,impact_level,scheduled_at,"
                    "actual,forecast,previous FROM calendar "
                    "WHERE scheduled_at >= ? AND scheduled_at <= ? "
                    "ORDER BY scheduled_at ASC LIMIT ?",
                    (start, end, limit),
                ).fetchall()
                for r in rows:
                    calendar.append({k: r[k] for k in r.keys()})
            con.close()
            used = str(path)
            break
        except sqlite3.Error as exc:
            error = type(exc).__name__
            continue
    return {
        "ok": True,
        "source": used,
        "news": news,
        "calendar": calendar,
        "empty": not news and not calendar,
        "note": (
            "ما في أخبار بهالفترة بهالاستخراج — الجسر فاضي. "
            "على ويندوز بتقرأ من nq_brain اللي بيكتبه الإكسبرت."
            if not news and not calendar else
            "صفوف الجسر بهالنافذة — بلا اختراع عنوان."
        ),
        "error": error,
    }
