from __future__ import annotations

from datetime import datetime

from app.analysis_service import AnalysisService
from app.calendar_bridge import CalendarBridge
from app.news_service import NewsService
from app.translator import TranslatorService



def build_morning_report() -> str:
    now = datetime.now()
    news_service = NewsService()
    translator = TranslatorService()
    analysis = AnalysisService()
    calendar_service = CalendarBridge()

    raw_news = news_service.fetch_latest_news(limit=6)
    translated_news = translator.translate_news_batch(raw_news)

    bias_summary = analysis.build_bias_summary(translated_news)
    news_digest = analysis.build_news_digest(translated_news[:3])
    top_event = calendar_service.get_top_event_summary()

    return (
        "📊 التقرير الصباحي\n\n"
        f"التاريخ: {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "1) الانحياز الأولي:\n"
        f"- {bias_summary}\n\n"
        "2) أهم حدث اليوم:\n"
        f"{top_event}\n\n"
        "3) أهم الأخبار:\n"
        f"{news_digest}\n\n"
        "4) ملاحظة:\n"
        "- راقب العوائد وVIX والأسهم القيادية خصوصًا وقت الأخبار المهمة."
    )
