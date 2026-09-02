from __future__ import annotations

import re
from typing import Dict, List


# ── مطابقة بحدود كلمات، مع اللواحق الشائعة ──────────────────────────
# القياس الذي فرض هذا: المطابقة كانت `k in text` — احتواءً داخل الكلمة —
# فعنوان «US imposes tariffs against China» يحمل «gain» داخل **against**
# فيُصنَّف إيجابيًّا، وهو خبر رسوم جمركية. (مقيس فعليًّا، لا افتراضًا.)
#
# والحدود وحدها تكسر التصريف: «gains» و«dropped» و«surging» لن تطابق
# أصولها. لذلك تُبنى صيغ كل كلمة صراحةً — لاحقة، وحذف ياء الصامت، وتضعيف
# الحرف الأخير — بدل الاتّكال على الاحتواء الأعمى.
_SUFFIXES = ("", "s", "es", "d", "ed", "ing")
_VOWELS = "aeiou"
_PATTERNS: dict[str, re.Pattern] = {}


def _forms(word: str) -> set[str]:
    forms = {word + suffix for suffix in _SUFFIXES}
    if word.endswith("e"):  # surge ⇒ surging · surged
        forms.update(word[:-1] + suffix for suffix in ("ing", "ed"))
    if (len(word) >= 3 and word[-1] not in _VOWELS
            and word[-2] in _VOWELS and word[-3] not in _VOWELS):  # drop ⇒ dropped
        forms.update(word + word[-1] + suffix for suffix in ("ed", "ing"))
    return forms


def has_term(text: str, word: str) -> bool:
    """هل ترد الكلمة (أو أحد تصريفاتها) ككلمة مستقلّة في النصّ؟"""
    pattern = _PATTERNS.get(word)
    if pattern is None:
        alternatives = sorted(_forms(word), key=len, reverse=True)
        pattern = re.compile(
            r"(?<![a-z0-9])(?:%s)(?![a-z0-9])"
            % "|".join(re.escape(form) for form in alternatives)
        )
        _PATTERNS[word] = pattern
    return bool(pattern.search(text))


class AnalysisService:
    def __init__(self) -> None:
        self.positive_keywords = ["beat", "growth", "surge", "gain", "optimism", "rally"]
        self.negative_keywords = ["drop", "fall", "selloff", "fear", "inflation", "hawkish", "yield"]
        # Terms whose presence marks a headline as directly market-moving,
        # used only to set `layer` (a rough urgency tier), not sentiment.
        self.high_impact_keywords = ["fed", "cpi", "nfp", "rate decision", "fomc", "powell"]

    def score_headline(self, title: str, summary: str = "") -> str:
        text = f"{title} {summary}".lower()
        pos = sum(1 for k in self.positive_keywords if has_term(text, k))
        neg = sum(1 for k in self.negative_keywords if has_term(text, k))
        # أمر المالك ٢٠٢٦-٠٨-٢٠: البوت ليس لناسداك وحده — الوصف يشمل السوق كلّه.
        # وهذه **قاعدة كلمات**، لا تحليلًا: تُوصَف بذلك صراحةً كي لا تُقرأ حكمًا.
        if pos > neg:
            return "إيجابي (قاعدة كلمات)"
        if neg > pos:
            return "سلبي (قاعدة كلمات)"
        return "محايد أو يحتاج تأكيد من السوق"

    def analyze_news_item(self, item: Dict[str, str]) -> Dict[str, object]:
        """Full per-item analysis: was missing entirely (telegram_bot.py
        called this and crashed) -- this is the real implementation, built
        directly on the existing score_headline() logic rather than a new
        scoring scheme.

        Args:
            item: A (already-translated) news item dict.

        Returns:
            A dict with title_ar, title, layer, credibility, impact,
            reason, link -- exactly what telegram_bot.py's
            _format_news_message() reads.
        """
        title = item.get("title", "")
        title_ar = item.get("title_ar", title)
        summary = item.get("summary", "")
        # score_headline's keyword list is English (beat, growth, drop...);
        # it must be checked against the English title/summary, not the
        # Arabic translation -- calling it with title_ar silently returned
        # "neutral" for every single item, since no English keyword can
        # ever match Arabic text. Same fix applied to build_bias_summary()
        # and build_news_digest() below.
        text = f"{title} {summary}".lower()

        impact = self.score_headline(title, summary)

        matched_positive = [k for k in self.positive_keywords if has_term(text, k)]
        matched_negative = [k for k in self.negative_keywords if has_term(text, k)]
        # كان السبب يُبنى بـ`if matched_positive` أوّلًا **مهما كان الحكم**،
        # فيظهر «انحياز سلبي» وتحته «كلمات إيجابية: gain» — تناقض على شاشة
        # الطالب. الآن السبب يشرح الحكم نفسه ويعرض الجانبين حين يتنازعان.
        if matched_positive and matched_negative:
            reason = ("إيجابية: %s · سلبية: %s"
                      % (", ".join(matched_positive), ", ".join(matched_negative)))
        elif matched_positive:
            reason = "كلمات إيجابية ظاهرة بالخبر: %s" % ", ".join(matched_positive)
        elif matched_negative:
            reason = "كلمات سلبية ظاهرة بالخبر: %s" % ", ".join(matched_negative)
        else:
            reason = "لا توجد كلمات مفتاحية واضحة الاتجاه؛ التقييم افتراضي محايد"

        is_high_impact = any(has_term(text, k) for k in self.high_impact_keywords)
        layer = 1 if is_high_impact else 3

        # كانت الموثوقيّة مكتوبة «مصدر عام (Yahoo Finance RSS)» **دائمًا** —
        # وصارت المصادر سبعة، منها رسميّ. الادّعاء الثابت كذبة بعد اليوم.
        source = str(item.get("source") or "")
        official = source in ("federal_reserve", "bea")
        credibility = "مصدر رسمي" if official else ("مصدر صحفي" if source else "مصدر غير معروف")

        return {
            "title_ar": title_ar,
            "title": title,
            "layer": layer,
            "credibility": credibility,
            "impact": impact,
            "reason": reason,
            "link": item.get("link", ""),
        }

    def build_bias_summary(self, news_items: List[Dict[str, str]]) -> str:
        """Aggregate sentiment across a batch of items into one summary
        line -- was missing (reporting.py's build_morning_report() called
        this and crashed).

        Args:
            news_items: A list of (already-translated) news item dicts.

        Returns:
            A short Arabic summary of the overall positive/negative/neutral split.
        """
        if not news_items:
            return "لا توجد أخبار كافية لتقدير الانحياز العام حاليًا."

        positive = negative = neutral = 0
        for item in news_items:
            # English text in, matching score_headline's English keyword list.
            impact = self.score_headline(item.get("title", ""), item.get("summary", ""))
            if "إيجابي" in impact:
                positive += 1
            elif "سلبي" in impact:
                negative += 1
            else:
                neutral += 1

        if positive > negative:
            lean = "الانحياز العام إيجابي مبدئيًا"
        elif negative > positive:
            lean = "الانحياز العام سلبي مبدئيًا"
        else:
            lean = "الانحياز العام متوازن/غير واضح"

        return f"{lean} ({positive} إيجابي، {negative} سلبي، {neutral} محايد من أصل {len(news_items)} خبر)"

    def build_news_digest(self, news_items: List[Dict[str, str]]) -> str:
        if not news_items:
            return "لا توجد أخبار متاحة حاليًا."

        lines = ["📰 أهم أخبار السوق الآن:\n"]
        for idx, item in enumerate(news_items[:8], start=1):
            # Same fix as analyze_news_item(): English text in, matching
            # score_headline's English keyword list (previously passed the
            # Arabic translation here, which silently always scored neutral).
            impact = self.score_headline(item.get("title", ""), item.get("summary", ""))
            lines.append(
                f"{idx}) {item.get('title_ar', item.get('title', ''))}\n"
                f"- التقييم: {impact}\n"
                f"- الرابط: {item.get('link', '')}\n"
            )
        return "\n".join(lines)
