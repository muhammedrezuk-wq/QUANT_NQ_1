"""خدمة الترجمة — ختم المالك ٢٠٢٦-٠٨-٢٠.

القياس الذي فرض إعادة الكتابة (زمن ردّ `/news` كان ~١٧ ثانية):

  • ترجمة العنوان الواحد عند جوجل ≈ ٠٫٧ ثانية — نداء شبكة، لا حساب محلّي.
  • كانت تُترجَم **٢٠ خبرًا** قبل إرسال أوّل رسالة، ثمّ يُرسَل **٥ فقط**
    (`break` عند الخامس) ⇒ خمس عشرة ترجمة تُرمى.
  • ولكل خبر كان يُترجَم **العنوان والملخّص** — و`summary_ar` **لا يُستعمل في
    أي موضع بالمشروع** (فُحص) ⇒ نصف النداءات هدر كامل.
  • ولا ذاكرة: العنوان نفسه يُترجَم من جديد كل مرّة، ولكل مستخدم.

ما صار: ذاكرة على القرص (العنوان يُترجَم مرّة واحدة بالعمر ويستفيد منها كل
المستخدمين)، وترجمة العناوين فقط، ودالّة تُنادى لكل خبر عند إرساله لا دفعةً.

وإن غابت المكتبة أو سقطت الشبكة: يُعاد النصّ الأصلي كما هو — لا ترجمة مخترعة.
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Dict, List

from app.jsonio import write_json_atomic

try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None

try:
    from deep_translator import MyMemoryTranslator
except Exception:
    MyMemoryTranslator = None

CACHE_PATH = os.path.join("data", "translations_ar.json")
_MAX_CHARS = 4500

# ── حارس المخرج ─────────────────────────────────────────────────────
# مقيس يوم ٢٠٢٦-٠٨-٢٥: جوجل ردّ بصفحة خطأ نصًّا عاديًّا
#   «Error 500 (Server Error)!!1500. That’s an error…»
# فقبلها الكود ترجمةً (غير فارغة، ومختلفة عن الأصل) **وخزّنها على القرص** —
# فصار الخطأ يصل الطالب مكان الخبر، ويُعاد من الذاكرة إلى الأبد.
# القاعدة: الهدف عربي، فمخرج بلا حرف عربي واحد ليس ترجمة مهما بدا نصًّا.
_ARABIC = re.compile(r"[؀-ۿ]")
_ERROR_MARKERS = (
    "that’s an error", "that's an error", "server error", "error 500",
    "try again later", "<html", "<!doctype",
)


def looks_translated(text: str) -> bool:
    low = text.lower()
    if any(marker in low for marker in _ERROR_MARKERS):
        return False
    return bool(_ARABIC.search(text))


class TranslatorService:
    _lock = threading.Lock()
    _cache: Dict[str, str] | None = None

    def __init__(self) -> None:
        # سلسلة محرّكات لا محرّكًا واحدًا — مقيس ٢٠٢٦-٠٨-٢٥ الساعة ٠٣:٠٠:
        # جوجل ردّ `Error 500` على **كل** طلب (نقطة المكتبة translate.google.com/m
        # محجوبة)، والنقطة المجانية الأخرى ردّت 429. فبقي الخبر إنجليزيًّا أمام
        # طالب عربي. البديل المجاني (MyMemory) جُرّب وأعطى عربيًّا سليمًا:
        #   «الذهب يرتفع قبل بيانات التضخم الأمريكية».
        # الترتيب مقصود: جوجل أدقّ حين يعمل، والبديل يمسك حين يسقط.
        self.engines = []
        if GoogleTranslator is not None:
            try:
                self.engines.append(("google", GoogleTranslator(source="auto", target="ar")))
            except Exception:
                pass
        if MyMemoryTranslator is not None:
            for pair in (("en-US", "ar-SA"), ("en", "ar")):
                try:
                    self.engines.append(
                        ("mymemory", MyMemoryTranslator(source=pair[0], target=pair[1])))
                    break
                except Exception:
                    continue
        self.last_engine = ""
        # يبقى للتوافق مع ما ينادي `self.translator` مباشرة (الاختبارات مثلًا).
        self.translator = self.engines[0][1] if self.engines else None

    # ── الذاكرة ──────────────────────────────────────────────────────
    @classmethod
    def _load(cls) -> Dict[str, str]:
        if cls._cache is None:
            try:
                with open(CACHE_PATH, "r", encoding="utf-8") as fh:
                    stored = json.load(fh)
            except Exception:
                stored = {}
            # تنظيف ذاتي: مداخل ملوّثة من قبل الحارس تُسقَط عند أوّل تحميل.
            cls._cache = {k: v for k, v in stored.items()
                          if isinstance(v, str) and looks_translated(v)}
        return cls._cache

    @classmethod
    def _save(cls) -> None:
        try:
            write_json_atomic(CACHE_PATH, cls._load())
        except OSError:
            pass

    @classmethod
    def cache_size(cls) -> int:
        return len(cls._load())

    # ── الترجمة ──────────────────────────────────────────────────────
    def translate_text(self, text: str) -> str:
        if not text:
            return ""
        cache = self._load()
        hit = cache.get(text)
        if hit:
            return hit

        out = ""
        # المحرّك الأوّل الذي يعطي عربيًّا سليمًا يفوز؛ وسقوطه ينقل الدور لا يوقفه.
        for name, engine in (self.engines or [(("direct"), self.translator)]):
            if engine is None:
                continue
            try:
                candidate = engine.translate(text[:_MAX_CHARS])
            except Exception:
                continue
            candidate = (candidate or "").strip()
            if not candidate or candidate == text.strip():
                continue
            if not looks_translated(candidate):
                # صفحة خطأ أو مخرج غير عربي: لا يُقبل ولا يُخزَّن — نجرّب التالي.
                continue
            out, self.last_engine = candidate, name
            break

        if not out:
            return text
        with self._lock:
            cache[text] = out
            self._save()
        return out

    def translate_news_batch(self, news_items: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """يترجم العناوين فقط.

        `summary_ar` أُزيل عمدًا: كان يُحسب ولا يُقرأ في أي موضع — نداء شبكة
        لكل خبر بلا مستهلك. الملخّص الأصلي يبقى بالحقل `summary` كما وصل.
        """
        return [{**item, "title_ar": self.translate_text(item.get("title", ""))}
                for item in news_items]
