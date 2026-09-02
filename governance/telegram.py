# -*- coding: utf-8 -*-
"""
٦١٠ — تلغرام: منصّة المالك المتنقّلة.

سطح حوكمة ثالث بجانب اللوحة والنافذة، بنفس القواعد بالضبط:

  * **لا يعرف النواة ولا الذرات.** يقرأ من خادم الحوكمة (:8090) وحده، فما يراه
    المالك على موبايله هو نفسه ما تراه اللوحة — مصدر واحد، فلا تكذب واجهة على أخرى.
  * **لا بوّابة أوامر ثانية.** كل أمر خطِر يمرّ من `POST /gov/command` بخطوتيه
    (طلب → ملخّص عربيّ + رمز ٦٠ث → تأكيد) ثم `commands.db` ثم الذرّة ٩٠١.
    ما في طريق جانبيّ للسوق، ولا سطر شراء/بيع هنا.
  * **قفل على المالك وحده.** أوّل تشغيل يطبع رمز اقتران بنافذته؛ من يرسله يُقفل
    عليه المحادثة ويُحفظ. وأي محادثة غيرها تُتجاهل بصمت وتُعدّ.
  * **التوكن في خزنة الأسرار المشفّرة، لا بملفّ نصّ.** المفتاح `telegram_bot_token`
    من `runtime\\secrets.enc` بنفس سلسلة النواة (`FileSecretProvider` ثمّ
    `EnvSecretProvider`). ولو وُجد توكن مكتوب بملفّ الإعداد **يُرفض ولا يُستعمل** —
    وإلّا صارت الخزنة زينة. وملفّ الإعداد لا يحمل إلّا ما ليس سرًّا (رقم المحادثة).
  * **حذفه لا يغيّر حرفًا** بالنواة ولا بالتداول.

التشغيل:  venv\\Scripts\\python.exe governance\\telegram.py
"""
from __future__ import annotations

import json
import os
import secrets
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SURFACE_ID = 610
LOCK_PORT = 8098          # قفل نسخة واحدة (اللوحة 8099 · الخادم 8090 · النواة 8010)
ROOT = Path(__file__).resolve().parent.parent
# تُشغَّل كملفّ مباشر، فجذر المشروع ليس على المسار وتفشل `import security`
# برسالة «الحزمة غير متاحة» بدل السبب الحقيقيّ. نفس ما يفعله secrets_admin.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONF_PATH = ROOT / "var" / "governance" / "telegram.json"   # إعدادات، لا أسرار
BEAT_PATH = ROOT / "var" / "governance" / "telegram_beat.json"   # نبض حيّ للّوحة
CORE_YAML = ROOT / "config" / "core.yaml"
SECRET_KEY_NAME = "telegram_bot_token"                      # اسمه بالخزنة
TRADE_DB = Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files" / "nq_brain.db"

GOV = "http://127.0.0.1:8090"
API = "https://api.telegram.org/bot%s/%s"

POLL_TIMEOUT_S = 25          # long-poll: نوم عند تلغرام لا حلقة محمومة عندنا
WATCH_EVERY_S = 6.0          # نبضة المراقبة (تنبيهات التحوّل)
HTTP_TIMEOUT_S = 40
CONFIRM_TTL_S = 60           # نفس مهلة الخادم — لا نخترع مهلة ثانية

# ── دروس من ٦١٠ القديمة (قُرئت ولم يُنسخ منها سطر) ──
# سقف الإرسال: تلغرام يخنق البوت مؤقّتًا عند الرشق. القديمة استعملت ٣٠/ث
# لأنّها تخدم عدّة مستخدمين؛ نحن نخدم **محادثة واحدة**، وحدّ تلغرام على
# المحادثة الواحدة ≈ رسالة بالثانية — فالمعايرة هنا لحالتنا لا نسخة عنها.
SEND_RATE_PER_S = 1.0
SEND_BURST = 5
POLL_BACKOFF_BASE_S = 3.0    # تهدئة أُسّية عند سقوط الاتصال، بدل نوم ثابت
POLL_BACKOFF_MAX_S = 60.0
CALLBACK_MAX_LEN = 200       # حدّ حمولة الزرّ — تقوية ضدّ المشوّه
CALLBACK_MAX_PARTS = 10

_STARTED_AT = time.time()


# ════════════════════════════════════════ الإعداد ════════════════════════════

def load_conf() -> dict[str, Any]:
    if not CONF_PATH.is_file():
        return {}
    try:
        data = json.loads(CONF_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_conf(conf: dict[str, Any]) -> None:
    CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONF_PATH.write_text(json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")


def beat(paired: bool, ignored: int, waited: int) -> None:
    """نبض كل دورة — هو ما تقرأه اللوحة لتعرف أنّ المنصّة حيّة.

    **لا يُفحَص المنفذ باتصال:** المنصّة تمسك منفذ القفل ولا تردّ عليه أبدًا
    (مشغولة تسمع تلغرام)، فطابوره يمتلئ ثمّ يفشل كل اتصال — فيبدو الحيّ ميّتًا.
    والنبض يقول أكثر: عمليّة واقفة تمسك المنفذ لكن نبضها يتوقّف.
    """
    try:
        BEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        BEAT_PATH.write_text(json.dumps({
            "at": time.time(), "pid": os.getpid(), "paired": paired,
            "ignored": ignored, "throttled": waited}, ensure_ascii=False),
            encoding="utf-8")
    except OSError:
        pass          # فشل كتابة النبض لا يوقف المنصّة


def valid_token(value: Any) -> bool:
    """توكن تلغرام شكله ثابت: أرقام ثمّ نقطتان ثمّ سلسلة طويلة.

    نتحقّق من الشكل بدل مقارنة النصّ بعبارة نائبة — فلا يكسرها ترميز ملفّ
    ولا تعديل يدويّ للعبارة، ويبقى الحكم واحدًا هنا وفي المشغّل.
    """
    return bool(re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", str(value or "").strip()))


# ══════════════════════════ التوكن من خزنة الأسرار ═══════════════════════════

def secrets_config() -> dict[str, Any]:
    """قسم `secrets` من `config/core.yaml` — نفس ما تقرأه النواة."""
    try:
        import yaml
        data = yaml.safe_load(CORE_YAML.read_text(encoding="utf-8")) or {}
        cfg = data.get("secrets")
        return cfg if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001 — غياب الإعداد ليس عطلًا
        return {}


def token_from_vault() -> tuple[str, str]:
    """يفتح الخزنة ويأخذ التوكن. يرجع (التوكن، سبب الفشل إن فشل).

    نبني نفس سلسلة `run_core.py` بدل `get_secret_provider()` لأنّ المزوّد
    مُفرد **على مستوى العملية**، والمنصّة عملية أخرى — فلو ناديناه هنا
    لأرجع مزوّدًا فارغًا دائمًا وبدا وكأنّ الخزنة فاضية.
    """
    cfg = secrets_config()
    if not cfg.get("enabled", True):
        return "", "خزنة الأسرار مطفأة بـ config/core.yaml"
    try:
        from security.providers import (ChainSecretProvider, EnvSecretProvider,
                                        FileSecretProvider)
        from security.interfaces import SecretProviderState
    except ImportError as exc:
        return "", "حزمة الأمان غير متاحة (%s)" % exc

    vault = Path(cfg.get("vault_path", "runtime/secrets.enc"))
    if not vault.is_absolute():
        vault = ROOT / vault
    if not vault.exists():
        return "", "لا خزنة بعد في %s — أنشئها بـ secrets_admin.py init" % vault

    store = FileSecretProvider(vault, dpapi_blob=cfg.get("dpapi_blob"),
                               allow_prompt=bool(cfg.get("allow_prompt", False)))
    provider = ChainSecretProvider(
        store, EnvSecretProvider(prefix=str(cfg.get("env_prefix", "QUANT_SECRET_"))))
    try:
        if store.state is SecretProviderState.LOCKED and not provider.has_secret(SECRET_KEY_NAME):
            return "", "الخزنة مقفولة — عيّن QUANT_MASTER_KEY=\"pass:<عبارتك>\" قبل التشغيل"
        value = str(provider.get_secret(SECRET_KEY_NAME) or "").strip()
        if not value:
            return "", "الخزنة مفتوحة وما فيها مفتاح '%s'" % SECRET_KEY_NAME
        if not valid_token(value):
            return "", "قيمة '%s' بالخزنة ليست بشكل توكن تلغرام" % SECRET_KEY_NAME
        return value, ""
    finally:
        provider.clear()          # لا يبقى أثر بالذاكرة بعد أخذ ما نحتاجه


# ════════════════════════════════ نداءات تلغرام ══════════════════════════════

def tg(token: str, method: str, **params: Any) -> dict[str, Any] | None:
    """نداء واحد لواجهة تلغرام. يرجع None عند أي فشل — ولا يرمي أبدًا."""
    body = urllib.parse.urlencode(
        {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
         for k, v in params.items() if v is not None}).encode("utf-8")
    req = urllib.request.Request(API % (token, method), data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
            out = json.loads(r.read().decode("utf-8"))
        return out if out.get("ok") else None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


class TokenBucket:
    """سقف إرسال بسيط: يملأ بمعدّل ثابت ويسمح بدفعة صغيرة.

    بلا سقف، عاصفة تحوّلات ترسل عشرات الرسائل بثوانٍ فيخنق تلغرام البوت
    مؤقّتًا — فنخسر التنبيه وقت ما نحتاجه. الانتظار هنا أرخص من الحظر هناك.
    """

    def __init__(self, rate: float, capacity: int) -> None:
        self.rate, self.capacity = rate, float(capacity)
        self.tokens = float(capacity)
        self.stamp = time.monotonic()
        self.waited = 0

    def take(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens < 1.0:
            delay = (1.0 - self.tokens) / self.rate
            self.waited += 1
            time.sleep(delay)
            self.tokens = 0.0
            self.stamp = time.monotonic()
            return
        self.tokens -= 1.0


_BUCKET = TokenBucket(SEND_RATE_PER_S, SEND_BURST)


def send(token: str, chat: int, text: str, inline: list | None = None,
         keyboard: list | None = None) -> None:
    """`inline` = أزرار ملتصقة بالرسالة · `keyboard` = لوحة ثابتة تحت الكتابة."""
    _BUCKET.take()
    markup = None
    if inline is not None:
        markup = {"inline_keyboard": inline}
    elif keyboard is not None:
        markup = {"keyboard": keyboard, "resize_keyboard": True,
                  "is_persistent": True, "input_field_placeholder": "اضغط زرًّا"}
    tg(token, "sendMessage", chat_id=chat, text=text[:4000],
       parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup)


# ═══════════════════════════ قراءة من خادم الحوكمة ═══════════════════════════

def gov_get(path: str) -> Any:
    try:
        with urllib.request.urlopen(GOV + path, timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def gov_post(path: str, payload: dict) -> tuple[int, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(GOV + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, {"error": "الخادم رفض الأمر"}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        return 0, {"error": "خادم الحوكمة لا يردّ (%s)" % type(e).__name__}


def atoms() -> list[dict]:
    data = gov_get("/gov/atoms")
    if isinstance(data, dict):
        data = data.get("atoms") or data.get("items")
    return data if isinstance(data, list) else []


def atom_by_id(items: list[dict], atom_id: int) -> dict:
    for a in items:
        try:
            if int(a.get("id")) == atom_id:
                return a
        except (TypeError, ValueError):
            continue
    return {}


def alerts_rows() -> dict:
    """التنبيهات النشطة من المُنذِر (831) — عبر خادم الحوكمة وحده (القاعدة
    الواحدة: تلغرام ما يلمس النواة). يقرأ من /gov/alerts ولا يفسّر إلا
    ما يصله: اسم الحدث + رقم ذرة الناشر + العدد."""
    data = gov_get("/gov/alerts")
    return data if isinstance(data, dict) else {}


# ═══════════════════ الترجمة للعربي (المالك لا يقرأ إنكليزيّة) ═══════════════
#
# رسائل الذرات مكتوبة إنكليزيّة بحكم دستور الذرة (لا حرف عربيّ داخل atom.py).
# فالترجمة وظيفة طبقة الحوكمة، لا الذرّة. والقاموس مبنيّ على **الرسائل الحيّة
# المقيسة فعلًا** من الـ212 ذرّة، لا على تخمين.

_CODE_AR = {
    "DISABLED": "مطفأ",
    "KILL_SWITCH_ACTIVE": "قاطع الأمان مفعّل",
    "NEVER_RAN": "ما اشتغلت ولا مرّة بعد",
    "NEWS_UNAVAILABLE": "ما في مصدر أخبار",
    "NOTHING_STORED_YET": "ما انخزن شي بعد",
    "NO_ACTIVITY_YET": "ما في حركة بعد",
    "NO_ACTUAL_SNAPSHOT": "ما في لقطة فعليّة بعد",
    "NO_BACKUP_YET": "ما في نسخة احتياطيّة بعد",
    "NO_COMMANDS_YET": "ما وصلها أمر بعد",
    "NO_INPUT_YET": "ما وصلها مدخل بعد",
    "NO_TRADES_YET": "ما في صفقات بعد",
    "NO_USABLE_REFERENCE": "ما في سعر مرجع صالح",
    "ORDER_FLOW_UNAVAILABLE": "ما في شريط صفقات — المصدر غير متوفّر",
    "PREVIEW_ONLY_NO_INPUT": "مقفول · وما وصلها طلب",
    "UNAVAILABLE_NO_TRADE_SOURCE": "ما في مصدر صفقات",
    "NOT_STARTED": "ما بدأت",
    "BRIDGE_UNREADABLE": "ما عم يقرا الجسر",
    "MISSING_INPUTS": "ناقصها مدخلات",
    "HALTED": "موقوفة بإيقاف طارئ",
    "LIVE_NO_INPUT": "مفتوحة وما وصلها طلب",
}

_PHRASE_AR = {
    "ledger_active": "الدفتر شغّال",
    "stop_targets_ready": "أهداف الوقف جاهزة",
    "reference_available": "سعر المرجع متوفّر",
    "bridge writable": "الجسر قابل للكتابة",
    "account_state": "حالة الحساب",
    "disk usage": "استعمال القرص",
    "source items match baseline": "ملفّات المصدر مطابقة للبصمة",
    "forwarding": "بيمرّر",
    "symbols via": "رمزًا عبر",
    "routes": "مسار",
    "streaming": "بيبثّ",
    "symbols": "رمزًا",
    "warmup": "إحماء",
    "via pool": "عبر مجموعة",
    "feed_live": "التغذية حيّة",
}

_COUNTER_AR = {
    "emitted": "أُرسل", "tracked": "متابَع", "candles": "شمعة", "seen": "وصلها",
    "published": "نشر", "opened": "فتح", "inputs": "مدخل", "open": "مفتوح",
    "forwarded": "مرّر", "signals": "إشارة", "blocked": "محجوب", "stored": "مخزَّن",
    "symbols": "رمز", "accounts": "حساب", "buffered": "بالذاكرة", "failed": "فشل",
    "swings": "قمّة/قاع", "validated": "تحقّق", "active": "مفعّل", "received": "استلم",
    "checked": "فحص", "dropped": "أُسقط", "events": "حدث", "breaks": "كسر",
    "issued": "أصدر", "approved": "وافق", "skipped": "تخطّى", "recorded": "سجّل",
    "confirmed": "مؤكَّد", "pending": "بالانتظار", "rejected": "رفض", "resolved": "حلّ",
    "closed": "أغلق", "updates": "تحديث", "synced": "متزامن", "processed": "عالج",
    "normalized": "موحَّد", "alerts": "تنبيه", "fused": "دمج", "pools": "بركة",
    "sweeps": "كنس", "fvgs": "فجوة", "extremes": "طرف", "outliers": "شاذّ",
    "outcomes": "نتيجة", "unmatched": "غير مطابَق", "decisions": "قرار",
    "samples": "عيّنة", "features": "ميزة", "candidates": "مرشّح",
    "comparisons": "مقارنة", "selected": "مختار", "evidence": "دليل",
    "entries": "دخول", "exits": "خروج", "cycles": "دورة",
    "evidence_sources": "مصدر دليل", "stale": "متأخّر", "evaluated": "قُيّم",
    "eligible": "مؤهَّل", "scored": "احتُسب", "passed": "مرّ", "waits": "انتظار",
    "buy": "شراء", "sell": "بيع", "neutral": "محايد", "wait": "انتظار",
    "conflicts": "تعارض", "reads": "قراءة", "dispatched": "أرسل",
    "allowed": "مسموح", "sized": "حُجّم", "equity": "الحقوق", "reported": "بلّغ",
    "dials": "عيار", "emits": "بثّ", "stops": "وقف", "computable": "قابل للحساب",
    "quality_scopes": "نطاق جودة", "realized": "محقّق", "plans": "خطّة",
    "breakevens": "تعادل", "trails": "تتبّع", "partials": "جزئيّ",
    "modify_sent": "تعديل مُرسَل", "delta_sent": "فرق مُرسَل", "pairs": "زوج",
    "tilts": "ميل", "divergence_updates": "تحديث انحراف", "snapshots": "لقطة",
    "legal": "قانونيّ", "released": "أُفرج", "written": "مكتوب", "offset": "إزاحة",
    "floating": "عائم", "news": "خبر", "calendar": "حدث تقويم", "ticks": "تكّة",
    "depth": "عمق", "components": "مكوّن", "flushed": "أُفرغ", "cpu": "المعالج",
    "mem": "الذاكرة", "portfolios": "محفظة", "targets": "هدف", "session": "جلسة",
    "validations": "تحقّق", "rejections": "رفض", "expired": "انتهت مهلته",
    "halt": "إيقاف طارئ", "reset": "تصفير", "activate": "تفعيل",
    "sys_tick": "نبضة", "heartbeat": "نبض", "minute": "دقيقة",
    "max_drift": "أقصى انحراف", "unified_published": "نشر موحَّد",
    "last_spread": "آخر سبريد", "drift": "انحراف", "bos": "كسر بنية",
    "choch": "انقلاب بنية", "shifts": "تحوّل", "mss": "تحوّل هيكل",
    "buyside": "سيولة شراء", "sellside": "سيولة بيع", "drift_updates": "تحديث انحراف",
    "balance_updates": "تحديث رصيد", "equity_updates": "تحديث حقوق",
    "margin_updates": "تحديث هامش", "free_margin_updates": "تحديث هامش حرّ",
    "margin_level_updates": "تحديث مستوى هامش", "positions_updates": "تحديث مراكز",
    "capital_updates": "تحديث رأس مال", "risk_updates": "تحديث مخاطر",
    "loss_limit_updates": "تحديث حدّ خسارة", "profit_limit_updates": "تحديث حدّ ربح",
    "comparison_updates": "تحديث مقارنة", "overview_parts": "جزء ملخّص",
    "pnl_updates": "تحديث ربح/خسارة", "performance_updates": "تحديث أداء",
}

_STATE_AR = {"healthy": "سليمة", "degraded": "متعثّرة",
             "unhealthy": "واقفة", "unknown": "غير معروفة"}

# عائلة التعلّم ٣٦٠ مجلّداتها بأسماء إنكليزيّة، فاسمها العربيّ يسقط للإنكليزيّ.
# الترجمة هنا للعرض فقط — ولا تُمَسّ أسماء المجلّدات (ترقيم المشروع ثابت).
_NAME_AR = {
    360: "مدير التعلّم", 361: "تسجيل النتائج", 362: "تجهيز العيّنات",
    363: "تدريب النموذج", 364: "التحقّق من النموذج", 365: "مقارنة النماذج",
    366: "اختيار النموذج", 367: "سجلّ النماذج", 368: "انحراف النموذج",
}
_BOOL_AR = {"True": "نعم", "False": "لا", "true": "نعم", "false": "لا",
            "None": "لا شيء", "null": "لا شيء"}


def name_ar(a: dict) -> str:
    try:
        fixed = _NAME_AR.get(int(a.get("id")))
    except (TypeError, ValueError):
        fixed = None
    return fixed or str(a.get("name_ar") or a.get("name") or "")

_TRADE_AR = {"OPENED": "فتح", "CLOSED": "إغلاق", "PARTIAL": "إغلاق جزئيّ",
             "REJECTED": "مرفوض"}


def arabize(msg: str) -> str:
    """يحوّل رسالة الذرّة الإنكليزيّة إلى عربيّة مفهومة.

    وما لا نعرف ترجمته يبقى كما هو — **لا نُخفي حقيقة لأنّنا لم نترجمها**.
    """
    text = str(msg or "").strip()
    if not text:
        return "—"
    if text in _CODE_AR:
        return _CODE_AR[text]
    if text in _PHRASE_AR:
        return _PHRASE_AR[text]
    pairs = re.findall(r"([A-Za-z_]+)=([^\s,]+)", text)
    if pairs:
        body = " · ".join("%s %s" % (_COUNTER_AR.get(k, k), _BOOL_AR.get(v, v))
                          for k, v in pairs)
        head = re.sub(r"[A-Za-z_]+=[^\s,]+", "", text).strip(" ,·")
        for en, ar in _PHRASE_AR.items():
            if head and en in head:
                head = head.replace(en, ar)
        return ("%s · %s" % (head, body)) if head else body
    out = text
    for en, ar in _PHRASE_AR.items():
        out = out.replace(en, ar)
    return out


def health_msg(a: dict) -> str:
    """الرسالة بالعربي — وهي ما يراه المالك."""
    return arabize((a.get("health") or {}).get("message") or "")


def health_raw(a: dict) -> str:
    return str((a.get("health") or {}).get("message") or "")


def health_state(a: dict) -> str:
    return str((a.get("health") or {}).get("state") or "")


def state_ar(a: dict) -> str:
    return str(a.get("label_ar") or _STATE_AR.get(health_state(a), health_state(a)))


# ══════════════════════════ قراءة جسر التداول (قراءة فقط) ════════════════════

def trade_db(query: str, args: tuple = ()) -> list[sqlite3.Row]:
    if not TRADE_DB.is_file():
        return []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % TRADE_DB.as_posix(), uri=True, timeout=3)
        con.row_factory = sqlite3.Row
        rows = list(con.execute(query, args))
        con.close()
        return rows
    except sqlite3.Error:
        return []


def account_row() -> dict:
    rows = trade_db("SELECT * FROM account LIMIT 1")
    return dict(rows[0]) if rows else {}


# ════════════════════════════ العرض بالعربي ══════════════════════════════════

def money(v: Any, suffix: str = "$") -> str:
    try:
        return "%s%s" % (("%.2f" % float(v)), suffix)
    except (TypeError, ValueError):
        return "—"


def ago(stamp: Any) -> str:
    try:
        d = time.time() - float(stamp)
    except (TypeError, ValueError):
        return "—"
    if d < 90:
        return "قبل %d ثانية" % int(d)
    if d < 5400:
        return "قبل %d دقيقة" % int(d / 60)
    if d < 172800:
        return "قبل %d ساعة" % int(d / 3600)
    return "قبل %d يوم" % int(d / 86400)


def view_overview() -> str:
    items = atoms()
    if not items:
        return "⚠️ <b>خادم الحوكمة لا يردّ.</b>\nشغّل «غرفة القيادة» على الجهاز."
    sick = [a for a in items if health_state(a) not in ("healthy", "")]
    gate = health_raw(atom_by_id(items, 552))       # القرار على الرمز الخام
    mgmt = health_raw(atom_by_id(items, 575))       # والعرض بالعربي
    acc = account_row()
    beat = acc.get("bridge_beat")
    lines = [
        "🏛 <b>QUANT_NQ — الحالة</b>",
        "",
        "الذرات: <b>%d</b> · مريضة: <b>%d</b>" % (len(items), len(sick)),
        "بوّابة التنفيذ: <b>%s</b>" % ("مفتوحة ✅" if "LIVE" in gate else "مقفولة 🔒"),
        "مرسل الإدارة: <b>%s</b>" % ("مفتوح ✅" if "DISABLED" not in mgmt.upper() else "مطفأ 🔒"),
        "",
        "الحساب: <b>%s</b> · %s" % (acc.get("account_id") or "—", acc.get("broker") or "—"),
        "الرصيد: <b>%s</b> · الهامش الحرّ: %s" % (money(acc.get("balance")), money(acc.get("free_margin"))),
        "المراكز المفتوحة: <b>%s</b>" % (acc.get("open_count") if acc.get("open_count") is not None else "—"),
        "التداول الآليّ: <b>%s</b>" % ("مسموح ✅" if acc.get("trade_allowed") else "موقوف ⛔"),
        "الإكسبرت: <b>%s</b>" % (ago(beat) if beat else "لا نبض ⛔"),
    ]
    if sick:
        lines += ["", "🟡 <b>المريضة:</b>"]
        for a in sick[:8]:
            lines.append("• <code>%s</code> %s — %s" % (a.get("id"), name_ar(a), health_msg(a)))
        if len(sick) > 8:
            lines.append("… و%d غيرها" % (len(sick) - 8))
    return "\n".join(lines)


def view_account() -> str:
    a = account_row()
    if not a:
        return "⚠️ جسر التداول غير مقروء — تأكّد أنّ الإكسبرت شغّال."
    return "\n".join([
        "💼 <b>الحساب</b>",
        "",
        "الرقم: <b>%s</b>" % (a.get("account_id") or "—"),
        "الوسيط: %s" % (a.get("broker") or "—"),
        "السيرفر: %s" % (a.get("account_server") or "—"),
        "",
        "الرصيد: <b>%s</b>" % money(a.get("balance")),
        "الحقوق: <b>%s</b>" % money(a.get("equity")),
        "الهامش المستعمَل: %s" % money(a.get("margin")),
        "الهامش الحرّ: <b>%s</b>" % money(a.get("free_margin")),
        "الرافعة: %s" % (a.get("leverage") or "—"),
        "",
        "المراكز: <b>%s</b>" % (a.get("open_count") if a.get("open_count") is not None else "—"),
        "متّصل: %s · تداول آليّ: %s" % ("نعم ✅" if a.get("connected") else "لا ⛔",
                                        "مسموح ✅" if a.get("trade_allowed") else "موقوف ⛔"),
        "نبض الإكسبرت: <b>%s</b>" % ago(a.get("bridge_beat")),
    ])


def view_asset() -> str:
    items = atoms()
    ledger = atom_by_id(items, 518)
    engine = atom_by_id(items, 581)
    port = atom_by_id(items, 519)
    perp = atom_by_id(items, 576)
    rows = trade_db("SELECT ROUND(SUM(profit),2) p FROM trade_events "
                    "WHERE event_type IN ('CLOSED','PARTIAL')")
    realized = rows[0]["p"] if rows and rows[0]["p"] is not None else 0.0
    return "\n".join([
        "📊 <b>الأصل</b>",
        "",
        "الخسارة/الربح المحقّق: <b>%s</b>" % money(realized),
        "",
        "دفتر المخاطر (٥١٨): %s" % (health_msg(ledger) or "—"),
        "محفظة الأصل (٥١٩): %s" % (health_msg(port) or "—"),
        "محرّك المركز (٥٨١): %s" % (health_msg(engine) or "—"),
        "المحرّك الدائم (٥٧٦): %s" % (health_msg(perp) or "—"),
        "",
        "<i>للتفصيل الكامل: اللوحة على الجهاز.</i>",
    ])


def view_gates() -> str:
    items = atoms()
    def line(i: int, label: str) -> str:
        a = atom_by_id(items, i)
        return "• <b>%s</b> (%d): %s" % (label, i, health_msg(a) or "—")
    return "\n".join([
        "🔐 <b>الحرّاس والبوّابات</b>",
        "",
        line(552, "بوّابة التنفيذ"),
        line(575, "مرسل الإدارة"),
        line(516, "قاطع الأمان"),
        line(585, "حارس الهامش"),
        line(584, "شرعيّة الستوب"),
        line(586, "بوّابة الرموز"),
        line(578, "منفّذ الفرق"),
        line(901, "بوّابة الأوامر"),
    ])


def view_decision() -> str:
    items = atoms()
    def line(i: int, label: str) -> str:
        return "• %s (%d): %s" % (label, i, health_msg(atom_by_id(items, i)) or "—")
    return "\n".join([
        "🧠 <b>القرار</b>",
        "",
        line(451, "التجميع"),
        line(453, "الدرجة"),
        line(458, "حلّ التعارض"),
        line(466, "الموافقة"),
        line(581, "المركز المطلوب"),
        "",
        "<i>الأرقام التفصيليّة (القوّة · من نطق) باللوحة.</i>",
    ])


def view_trades(limit: int = 8) -> str:
    rows = trade_db("SELECT event_type, symbol, side, volume, profit, written_at "
                    "FROM trade_events ORDER BY id DESC LIMIT ?", (limit,))
    if not rows:
        return "لا صفقات مسجّلة بعد."
    sides = {"BUY": "شراء", "SELL": "بيع"}
    out = ["🧾 <b>آخر الصفقات</b>", ""]
    for r in rows:
        p = r["profit"]
        out.append("• %s %s %s %s — <b>%s</b> · %s" % (
            _TRADE_AR.get(str(r["event_type"]), r["event_type"]),
            r["symbol"] or "", sides.get(str(r["side"]), r["side"] or ""),
            r["volume"] or "", money(p) if p is not None else "—", ago(r["written_at"])))
    return "\n".join(out)


_OPERATOR_AR = {"dashboard": "اللوحة", "telegram": "تلغرام"}
_ACTION_AR = {
    "halt": "إيقاف طارئ", "kill_switch_reset": "تصفير القاطع",
    "activate_asset": "بدء أصل", "asset_control": "تحكّم بأصل",
}
_STATUS_AR = {"DONE": "نُفِّذ ✅", "PENDING": "بالطابور ⏳", "EXPIRED": "انتهت مهلته ⌛",
              "REJECTED": "مرفوض ⛔", "FAILED": "فشل ⛔", "OK": "تمّ ✅"}


def view_log(limit: int = 10) -> str:
    """السجلّ الموحّد: أوامر المالك + حركة السوق + أخطاء الذرات — من مصدر اللوحة نفسه."""
    data = gov_get("/gov/unified-log")
    if not isinstance(data, dict):
        return "⚠️ خادم الحوكمة لا يردّ — ما قدرت أجيب السجلّ."
    items = data.get("items") or []
    errors = data.get("errors") or []
    gate = [i for i in items if i.get("src") == "gate"][:limit]
    trades = [i for i in items if i.get("src") == "trade"][:limit]

    out = ["📜 <b>السجلّ</b>", ""]
    out.append("<b>أوامرك</b>")
    if not gate:
        out.append("  لا أوامر مسجّلة.")
    for g in gate:
        out.append("• %s — %s · <i>%s</i> · %s" % (
            _ACTION_AR.get(str(g.get("kind")), str(g.get("kind"))),
            _STATUS_AR.get(str(g.get("status")), str(g.get("status"))),
            _OPERATOR_AR.get(str(g.get("operator")), str(g.get("operator") or "—")),
            ago(g.get("ts"))))

    out += ["", "<b>الحركة</b>"]
    if not trades:
        out.append("  لا حركة مسجّلة.")
    sides = {"BUY": "شراء", "SELL": "بيع"}
    for tr in trades:
        kind = str(tr.get("kind"))
        out.append("• %s %s %s %s · %s" % (
            _TRADE_AR.get(kind, _STATUS_AR.get(kind, kind)),
            tr.get("symbol") or "", sides.get(str(tr.get("side")), tr.get("side") or ""),
            tr.get("volume") or "", ago(tr.get("ts"))))

    out += ["", "<b>أخطاء الذرات</b>"]
    if not errors:
        out.append("  ✅ ولا خطأ مسجّل.")
    for e in errors[:6]:
        out.append("• <code>%s</code> %s — %s" % (
            e.get("atom_id"), e.get("name_ar") or "", str(e.get("error"))[:110]))
    return "\n".join(out)


def view_sick() -> str:
    items = atoms()
    if not items:
        return "⚠️ خادم الحوكمة لا يردّ."
    sick = [a for a in items if health_state(a) not in ("healthy", "")]
    if not sick:
        return "✅ كل الذرات سليمة (%d)." % len(items)
    out = ["🟡 <b>الذرات المريضة: %d من %d</b>" % (len(sick), len(items)), ""]
    for a in sick:
        out.append("• <code>%s</code> %s\n   %s — %s" % (
            a.get("id"), name_ar(a), state_ar(a), health_msg(a)))
    return "\n".join(out)


# ═════════════════════════════ الأزرار العربيّة ══════════════════════════════
#
# المالك يضغط، لا يكتب. فاللوحة الثابتة تحمل كل شاشة بزرّ، والأوامر بأزرار
# ملتصقة تسأله الرمز ثمّ الرقم ثمّ التأكيد — بلا صيغة يحفظها ولا خطأ إملائيّ.

BTN_STATE, BTN_ACCOUNT, BTN_ASSET = "🏛 الحالة", "💼 الحساب", "📊 الأصل"
BTN_GUARDS, BTN_DECISION, BTN_TRADES = "🔐 الحرّاس", "🧠 القرار", "🧾 الصفقات"
BTN_SICK, BTN_LOG, BTN_COMMANDS = "🟡 المريضة", "📜 السجلّ", "⚙️ الأوامر"

MAIN_KEYBOARD = [
    [{"text": BTN_STATE}, {"text": BTN_ACCOUNT}],
    [{"text": BTN_ASSET}, {"text": BTN_GUARDS}],
    [{"text": BTN_DECISION}, {"text": BTN_TRADES}],
    [{"text": BTN_SICK}, {"text": BTN_LOG}],
    [{"text": BTN_COMMANDS}],
]

# الأمر → (نصّ الزرّ · يحتاج رمزًا؟ · يحتاج رقمًا؟)
ASSET_ACTIONS = {
    "freeze":    ("🧊 تجميد الأصل", True, None),
    "unfreeze":  ("🔥 فكّ التجميد", True, None),
    "pause":     ("⏸ وقف الأصل", True, None),
    "resume":    ("▶️ تشغيل الأصل", True, None),
    "reconcile": ("🔄 مطابقة مع الوسيط", True, None),
    "budget":    ("💰 تعديل الميزانيّة", True, "المبلغ بالدولار"),
    "dial":      ("🎚 تعديل العيار", True, "العيار ٠–١٠٠"),
    "activate":  ("🚀 بدء أصل جديد", True, "الميزانيّة بالدولار"),
}
BUDGET_CHOICES = [50, 100, 150, 200, 300, 500]
DIAL_CHOICES = [10, 25, 40, 50, 60, 75, 90]

COMMANDS_MENU = [
    [{"text": "🛑 إيقاف طارئ شامل", "callback_data": "c:halt"}],
    [{"text": "🧯 تصفير قاطع الأمان", "callback_data": "c:reset"}],
    [{"text": ASSET_ACTIONS["activate"][0], "callback_data": "a:activate"}],
    [{"text": ASSET_ACTIONS["budget"][0], "callback_data": "a:budget"},
     {"text": ASSET_ACTIONS["dial"][0], "callback_data": "a:dial"}],
    [{"text": ASSET_ACTIONS["freeze"][0], "callback_data": "a:freeze"},
     {"text": ASSET_ACTIONS["unfreeze"][0], "callback_data": "a:unfreeze"}],
    [{"text": ASSET_ACTIONS["pause"][0], "callback_data": "a:pause"},
     {"text": ASSET_ACTIONS["resume"][0], "callback_data": "a:resume"}],
    [{"text": ASSET_ACTIONS["reconcile"][0], "callback_data": "a:reconcile"}],
]

VIEWS = {
    BTN_STATE: "view_overview", BTN_ACCOUNT: "view_account", BTN_ASSET: "view_asset",
    BTN_GUARDS: "view_gates", BTN_DECISION: "view_decision",
    BTN_TRADES: "view_trades", BTN_SICK: "view_sick", BTN_LOG: "view_log",
}

HELP = """🏛 <b>QUANT_NQ — منصّتك المتنقّلة</b>

الأزرار تحت مربّع الكتابة. اضغط، ولا تكتب شيئًا.

<b>🏛 الحالة</b> — نظرة عامّة بسطر واحد لكلّ شيء
<b>💼 الحساب</b> — رصيد وهامش ومراكز ونبض الإكسبرت
<b>📊 الأصل</b> — الميزانيّة والمحقّق ودفتر المخاطر
<b>🔐 الحرّاس</b> — كلّ بوّابة وحالتها
<b>🧠 القرار</b> — سلسلة القرار
<b>🧾 الصفقات</b> — آخر ما نُفّذ
<b>🟡 المريضة</b> — الذرات غير السليمة
<b>📜 السجلّ</b> — أوامرك ومن أين أرسلتها · حركة السوق · أخطاء الذرات
<b>⚙️ الأوامر</b> — إيقاف · تصفير · بدء أصل · ميزانيّة · عيار · تجميد · وقف · مطابقة

كل أمر خطِر يسألك <b>الرمز</b> ثمّ <b>الرقم</b> ثمّ <b>تأكيدًا</b> — ثلاث ضغطات، ولا صيغة تحفظها.

<i>ما فيه زرّ شراء ولا بيع — ولا يمكن أن يوجد. كلّ شيء يمرّ من بوّابة ٩٠١.</i>"""


def symbol_buttons(prefix: str) -> list:
    """أزرار الرموز الحقيقيّة من مواصفات الوسيط — لا قائمة مكتوبة بالكود."""
    rows = trade_db("SELECT DISTINCT symbol FROM symbol_specs ORDER BY symbol LIMIT 8")
    symbols = [str(r["symbol"]) for r in rows if r["symbol"]]
    if not symbols:
        symbols = ["BTCUSD"]
    out, line = [], []
    for s in symbols:
        line.append({"text": s, "callback_data": "%s:%s" % (prefix, s)})
        if len(line) == 2:
            out.append(line); line = []
    if line:
        out.append(line)
    out.append([{"text": "↩️ رجوع", "callback_data": "m:cmd"}])
    return out


def value_buttons(action: str, symbol: str) -> list:
    choices = DIAL_CHOICES if action == "dial" else BUDGET_CHOICES
    unit = "" if action == "dial" else "$"
    out, line = [], []
    for v in choices:
        line.append({"text": "%s%s" % (unit, v), "callback_data": "y:%s:%s:%s" % (action, symbol, v)})
        if len(line) == 3:
            out.append(line); line = []
    if line:
        out.append(line)
    out.append([{"text": "↩️ رجوع", "callback_data": "a:%s" % action}])
    return out


# ═══════════════════════════ الأوامر الخطِرة ═════════════════════════════════

OPERATOR = "telegram"          # يوسم كل أمر بمصدره، فلا يقول السجلّ «اللوحة» عن الموبايل


def request_danger(action: str, payload: dict) -> tuple[str, list | None]:
    """الخطوة الأولى: طلب ملخّص ورمز تأكيد من نفس بوّابة اللوحة."""
    status, obj = gov_post("/gov/command", {"action": action, "payload": payload,
                                            "operator": OPERATOR})
    if status != 200 or not isinstance(obj, dict):
        return "⛔ %s" % (obj or {}).get("error", "تعذّر الطلب"), None
    if obj.get("stage") != "confirm":
        return "⛔ ردّ غير متوقّع من البوّابة.", None
    token = str(obj.get("token"))
    body = "⚠️ <b>تأكيد مطلوب</b>\n\n%s\n\n<i>الرمز صالح %d ثانية.</i>" % (
        obj.get("summary", action), int(obj.get("ttl_s") or CONFIRM_TTL_S))
    keys = [[{"text": "✅ نفّذ", "callback_data": "ok:%s:%s" % (action, token)},
             {"text": "✖️ إلغاء", "callback_data": "no:%s" % token}]]
    return body, keys


def confirm_danger(action: str, token: str, payload: dict) -> str:
    status, obj = gov_post("/gov/command",
                           {"action": action, "payload": payload, "confirm": token,
                            "operator": OPERATOR})
    if status in (200, 201) and isinstance(obj, dict) and not obj.get("error"):
        return "✅ <b>تمّ.</b> الأمر بالطابور — الذرّة ٩٠١ تنفّذه خلال ثانية."
    return "⛔ %s" % (obj or {}).get("error", "فشل التنفيذ")


_ASSET_COMMAND = {"freeze": "FREEZE", "unfreeze": "UNFREEZE", "pause": "PAUSE",
                  "resume": "RESUME", "reconcile": "FORCE_RECONCILE",
                  "budget": "SET_BUDGET", "dial": "CALIBRATE"}


def build_command(action: str, symbol: str = "", value: float | None = None
                  ) -> tuple[str, dict] | None:
    """يبني (action, payload) لبوّابة الحوكمة من ضغطة زرّ — لا من نصّ مكتوب."""
    acc = str(account_row().get("account_id") or "")
    if action == "halt":
        return "halt", {}
    if action == "reset":
        return "kill_switch_reset", {}
    if not symbol:
        return None
    if action == "activate":
        if value is None:
            return None
        return "activate_asset", {"account_id": acc, "symbol": symbol, "budget": value}
    command = _ASSET_COMMAND.get(action)
    if command is None:
        return None
    payload = {"account_id": acc, "symbol": symbol, "command": command}
    if action == "budget":
        if value is None:
            return None
        payload["risk_budget"] = value
    if action == "dial":
        if value is None:
            return None
        payload["dial"] = value
    return "asset_control", payload


# ════════════════════════════ المراقبة والتنبيه ══════════════════════════════

class Watcher:
    """يقارن الحالة بالحالة السابقة ويرسل عند **التحوّل** فقط — لا رشق."""

    def __init__(self) -> None:
        self.prev: dict[str, Any] = {}
        self.armed = False          # لا ينبّه على أوّل قراءة (وإلا أغرق عند الإقلاع)

    def check(self) -> list[str]:
        items = atoms()
        if not items:
            return self._diff({"server": "down"})
        acc = account_row()
        rows = trade_db("SELECT COUNT(*) n FROM trade_events")
        now = {
            "server": "up",
            "gate": "LIVE" if "LIVE" in health_msg(atom_by_id(items, 552)) else "PREVIEW",
            "mgmt": "OFF" if "DISABLED" in health_msg(atom_by_id(items, 575)).upper() else "ON",
            "breaker": health_msg(atom_by_id(items, 516)),
            "trade_allowed": bool(acc.get("trade_allowed")),
            "ea_alive": bool(acc.get("bridge_beat") and time.time() - float(acc["bridge_beat"]) < 120),
            "open_count": acc.get("open_count"),
            "trades": rows[0]["n"] if rows else 0,
            "sick": len([a for a in items if health_state(a) not in ("healthy", "")]),
        }
        # المُنذِر (831): إخفاقات نشطة بلا مستمع سابقًا — ننبّه عند ظهور/اختفاء
        # أي حدث، لا عند كل تكرار (التكرار محجوب بالتهدئة عند الذرة نفسها).
        al = alerts_rows()
        rows = al.get("alerts")
        if not isinstance(rows, dict):
            rows = {}
        names = {}
        for x in items:
            try:
                names[int(x.get("id"))] = x.get("name_ar") or x.get("name") or "?"
            except (TypeError, ValueError):
                continue
        now["alerts"] = tuple(sorted(
            "%s [%s]" % (k, names.get((v or {}).get("source_atom"), "?"))
            for k, v in rows.items()
        ))
        return self._diff(now)

    def _diff(self, now: dict[str, Any]) -> list[str]:
        out: list[str] = []
        prev, self.prev = self.prev, now
        if not self.armed:
            self.armed = True
            return []
        if prev.get("server") != now.get("server"):
            out.append("🔌 خادم الحوكمة: <b>%s</b>" %
                       ("رجع ✅" if now.get("server") == "up" else "توقّف ⛔"))
        if now.get("server") != "up":
            return out
        if prev.get("gate") != now.get("gate"):
            out.append("🚪 بوّابة التنفيذ صارت: <b>%s</b>" %
                       ("مفتوحة — الأوامر تصل السوق ⚠️" if now["gate"] == "LIVE" else "مقفولة 🔒"))
        if prev.get("mgmt") != now.get("mgmt"):
            out.append("🛠 مرسل الإدارة صار: <b>%s</b>" % ("شغّال ⚠️" if now["mgmt"] == "ON" else "مطفأ 🔒"))
        if prev.get("breaker") != now.get("breaker"):
            out.append("🧯 قاطع الأمان: <b>%s</b>" % now.get("breaker"))
        if prev.get("trade_allowed") != now.get("trade_allowed"):
            out.append("🎛 التداول الآليّ: <b>%s</b>" %
                       ("مسموح ✅" if now["trade_allowed"] else "موقوف ⛔"))
        if prev.get("ea_alive") != now.get("ea_alive"):
            out.append("📡 الإكسبرت: <b>%s</b>" % ("رجع ينبض ✅" if now["ea_alive"] else "سكت ⛔"))
        if prev.get("open_count") != now.get("open_count"):
            out.append("📈 المراكز المفتوحة: <b>%s</b> (كانت %s)" %
                       (now.get("open_count"), prev.get("open_count")))
        try:
            if int(now.get("trades") or 0) > int(prev.get("trades") or 0):
                out.append("🧾 صفقة جديدة انسجّلت — /الصفقات")
        except (TypeError, ValueError):
            pass
        try:
            if int(now.get("sick") or 0) > int(prev.get("sick") or 0):
                out.append("🟡 عدد الذرات المريضة ارتفع إلى <b>%s</b> — /المريضة" % now.get("sick"))
        except (TypeError, ValueError):
            pass
        prev_a = set(prev.get("alerts") or ())
        now_a = set(now.get("alerts") or ())
        for k in sorted(now_a - prev_a):
            out.append("🚨 إخفاق نشط: <b>%s</b>" % k)
        for k in sorted(prev_a - now_a):
            out.append("✅ تحسّن: انقضى <b>%s</b>" % k)
        return out


# ═════════════════════════════ الحلقة الرئيسة ════════════════════════════════

def log(msg: str) -> None:
    print("[٦١٠ تلغرام] %s" % msg, flush=True)


class Surface:
    def __init__(self, token: str, conf: dict[str, Any]) -> None:
        self.token = token
        self.owner = int(conf.get("owner_chat_id") or 0)
        self.conf = conf
        self.offset = 0
        self.pair_code = "" if self.owner else "%06d" % secrets.randbelow(1000000)
        self._pair_attempts: dict[int, tuple[int, float]] = {}
        self.pending: dict[str, dict] = {}     # رمز التأكيد → الحمولة
        self.awaiting: tuple[str, str] | None = None   # (الأمر، الرمز) بانتظار رقم
        self.watcher = Watcher()
        self.ignored = 0
        self.next_watch = 0.0

    # ── الاقتران ──
    def announce_pairing(self) -> None:
        log("=" * 56)
        log("لم يُربط أي حساب بعد.")
        log("افتح بوت تلغرام من موبايلك وأرسل له هذا الرمز:")
        log("")
        log("        >>>   %s   <<<" % self.pair_code)
        log("")
        log("أوّل من يرسله يصير المالك، ويُقفل عليه — وغيره يُتجاهل.")
        log("=" * 56)

    def try_pair(self, chat: int, text: str) -> bool:
        if self.owner:
            return False
        now = time.monotonic(); count, started = self._pair_attempts.get(chat, (0, now))
        if now - started > 300: count, started = 0, now
        if count >= 5:
            self.ignored += 1; return False
        if not secrets.compare_digest(text.strip(), self.pair_code):
            self._pair_attempts[chat] = (count + 1, started); self.ignored += 1; return False
        self._pair_attempts.clear()
        self.owner = chat
        self.conf["owner_chat_id"] = chat
        save_conf(self.conf)
        log("✓ اقترن المالك (chat %s) — وحُفظ." % chat)
        send(self.token, chat, "✅ <b>تمّ الربط.</b>\nهذه محادثتك وحدك الآن.\n\n" + HELP,
             keyboard=MAIN_KEYBOARD)
        return True

    # ── الرسائل: ضغطة زرّ من اللوحة الثابتة ──
    def on_text(self, chat: int, text: str) -> None:
        if chat != self.owner:
            if not self.try_pair(chat, text):
                self.ignored += 1
            return
        t = text.strip()
        if t in VIEWS:
            send(self.token, chat, globals()[VIEWS[t]]())
            return
        if t == BTN_COMMANDS:
            send(self.token, chat, "⚙️ <b>الأوامر</b>\nاضغط الأمر — وكلّه بتأكيد قبل التنفيذ.",
                 inline=COMMANDS_MENU)
            return
        # الكتابة تبقى مقبولة (رقم بعد «مبلغ آخر»)، وغير ذلك يردّ باللوحة.
        if self.awaiting and self._take_typed_value(chat, t):
            return
        send(self.token, chat, HELP, keyboard=MAIN_KEYBOARD)

    def _take_typed_value(self, chat: int, text: str) -> bool:
        action, symbol = self.awaiting
        try:
            value = float(text.replace("$", "").replace("٫", ".").strip())
        except ValueError:
            send(self.token, chat, "بدّي رقم فقط. جرّب مرّة تانية أو اضغط ↩️ رجوع.")
            return True
        if value <= 0 or (action == "dial" and value > 100):
            send(self.token, chat, "الرقم خارج الحدّ المسموح.")
            return True
        self.awaiting = None
        self._ask_confirm(chat, action, symbol, value)
        return True

    # ── الأزرار الملتصقة ──
    def on_callback(self, cq: dict) -> None:
        data = str(cq.get("data") or "")
        chat = int(((cq.get("message") or {}).get("chat") or {}).get("id") or 0)
        tg(self.token, "answerCallbackQuery", callback_query_id=cq.get("id"))
        if chat != self.owner:
            self.ignored += 1
            return
        # حمولة الزرّ نصنعها نحن؛ ما جاء أطول أو أكثر أجزاءً منها ليس منّا.
        if len(data) > CALLBACK_MAX_LEN or data.count(":") + 1 > CALLBACK_MAX_PARTS:
            self.ignored += 1
            return
        head, _, rest = data.partition(":")

        if head == "m":                                     # رجوع لقائمة الأوامر
            self.awaiting = None
            send(self.token, chat, "⚙️ <b>الأوامر</b>", inline=COMMANDS_MENU)
            return
        if head == "c":                                     # أمر بلا رمز ولا رقم
            self._ask_confirm(chat, rest, "", None)
            return
        if head == "a":                                     # أمر يحتاج رمزًا
            label = ASSET_ACTIONS.get(rest, ("الأمر",))[0]
            send(self.token, chat, "%s\n\nعلى أيّ أصل؟" % label,
                 inline=symbol_buttons("s:%s" % rest))
            return
        if head == "s":                                     # اخْتير الرمز
            action, _, symbol = rest.partition(":")
            needs = ASSET_ACTIONS.get(action, (None, None, None))[2]
            if needs is None:
                self._ask_confirm(chat, action, symbol, None)
                return
            self.awaiting = (action, symbol)
            send(self.token, chat, "<b>%s</b> · %s\n\n%s؟" % (symbol, ASSET_ACTIONS[action][0], needs),
                 inline=value_buttons(action, symbol)
                 + [[{"text": "✏️ رقم آخر — اكتبه", "callback_data": "t:%s:%s" % (action, symbol)}]])
            return
        if head == "t":                                     # سيكتب الرقم بيده
            action, _, symbol = rest.partition(":")
            self.awaiting = (action, symbol)
            send(self.token, chat, "اكتب الرقم الآن (أرقام فقط).")
            return
        if head == "y":                                     # اخْتير الرقم
            parts = rest.split(":")
            if len(parts) != 3:
                return
            action, symbol, raw = parts
            self.awaiting = None
            try:
                value = float(raw)
            except ValueError:
                return
            self._ask_confirm(chat, action, symbol, value)
            return
        if head == "no":
            self.pending.pop(rest, None)
            send(self.token, chat, "✖️ أُلغي. ما صار شيء.", keyboard=MAIN_KEYBOARD)
            return
        if head == "ok":
            action, _, token = rest.partition(":")
            payload = self.pending.pop(token, {})
            send(self.token, chat, confirm_danger(action, token, payload),
                 keyboard=MAIN_KEYBOARD)

    def _ask_confirm(self, chat: int, action: str, symbol: str,
                     value: float | None) -> None:
        built = build_command(action, symbol, value)
        if built is None:
            send(self.token, chat, "⛔ أمر ناقص — ابدأ من جديد.", inline=COMMANDS_MENU)
            return
        gate_action, payload = built
        body, keys = request_danger(gate_action, payload)
        if keys:
            self.pending[keys[0][0]["callback_data"].split(":", 2)[2]] = payload
            send(self.token, chat, body, inline=keys)
            return
        send(self.token, chat, body, inline=COMMANDS_MENU)

    # ── الحلقة ──
    def run(self) -> None:
        me = tg(self.token, "getMe")
        if not me:
            log("✗ التوكن مرفوض من تلغرام، أو لا إنترنت. صحّح %s وأعد التشغيل." % CONF_PATH)
            return
        log("✓ البوت: @%s" % (me.get("result") or {}).get("username", "?"))
        if not self.owner:
            self.announce_pairing()
        else:
            log("✓ مقترن بالمالك (chat %s)" % self.owner)
            send(self.token, self.owner, "🏛 <b>المنصّة اشتغلت.</b>\nالأزرار تحت.",
                 keyboard=MAIN_KEYBOARD)

        fails = 0
        while True:
            beat(bool(self.owner), self.ignored, _BUCKET.waited)
            upd = tg(self.token, "getUpdates", offset=self.offset,
                     timeout=POLL_TIMEOUT_S, allowed_updates=["message", "callback_query"])
            if upd:
                fails = 0
                for u in upd.get("result", []):
                    self.offset = int(u["update_id"]) + 1
                    try:
                        if "callback_query" in u:
                            self.on_callback(u["callback_query"])
                        elif "message" in u:
                            m = u["message"]
                            text = str(m.get("text") or "")
                            chat = int((m.get("chat") or {}).get("id") or 0)
                            if text and chat:
                                self.on_text(chat, text)
                    except Exception as exc:  # noqa: BLE001
                        log("⚠ خطأ بمعالجة تحديث: %s" % exc)
            else:
                # تلغرام لا يردّ: تهدئة أُسّية تنصفر بأوّل نجاح — الدقّ على باب
                # مسكّر بنفس الإيقاع يستهلك ولا يفيد.
                fails += 1
                delay = min(POLL_BACKOFF_MAX_S, POLL_BACKOFF_BASE_S * (2 ** min(fails - 1, 5)))
                if fails in (1, 4, 8):
                    log("الاتصال بتلغرام متعذّر (%d محاولة) — تهدئة %.0f ثانية" % (fails, delay))
                time.sleep(delay)

            if self.owner and time.time() >= self.next_watch:
                self.next_watch = time.time() + WATCH_EVERY_S
                try:
                    for alert in self.watcher.check():
                        send(self.token, self.owner, alert)
                except Exception as exc:  # noqa: BLE001
                    log("⚠ خطأ بالمراقبة: %s" % exc)


def main() -> int:
    if sys.platform == "win32":
        import os
        os.system("chcp 65001 >nul")
    print("=" * 56, flush=True)
    print("  ٦١٠ — تلغرام: منصّة المالك المتنقّلة", flush=True)
    print("=" * 56, flush=True)

    # نسخة واحدة فقط: نسختان تسحبان نفس الطابور فتضيع رسائل ويتكرّر التنبيه.
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
        lock.listen(1)
    except OSError:
        log("المنصّة شغّالة مسبقًا — لن تُفتح نسخة ثانية.")
        return 0

    conf = load_conf()
    if not CONF_PATH.is_file():
        save_conf({"_شرح": "إعدادات فقط — لا أسرار. التوكن مكانه خزنة الأسرار المشفّرة "
                           "(runtime/secrets.enc) بالمفتاح 'telegram_bot_token'.",
                   "owner_chat_id": 0})
        conf = load_conf()

    # سرّ مكتوب بملفّ نصّ يُرفض ولا يُستعمل — وإلّا صارت الخزنة زينة.
    if conf.get("token"):
        log("✗ وُجد توكن مكتوب داخل %s" % CONF_PATH.name)
        log("  السرّ مكانه الخزنة المشفّرة، لا ملفّ نصّ. انقله:")
        log("     venv\\Scripts\\python.exe governance\\scripts\\secrets_admin.py set telegram_bot_token")
        log("  ثمّ احذف حقل token من الملفّ.")
        return 2

    token, why = token_from_vault()
    if not token:
        log("✗ ما وصلني توكن من الخزنة: %s" % why)
        log("")
        log("  الخطوات (كلّها بيدك، ولا سرّ يمرّ بمحادثة):")
        log("  ١) تلغرام → @BotFather → /newbot → خذ التوكن")
        log("  ٢) أنشئ الخزنة مرّة واحدة (تختار عبارة مرور وتحفظها بنفسك):")
        log("       venv\\Scripts\\python.exe governance\\scripts\\secrets_admin.py init")
        log("  ٣) ضع التوكن داخلها (يُطلب مخفيًّا، ولا يُطبع):")
        log("       venv\\Scripts\\python.exe governance\\scripts\\secrets_admin.py set telegram_bot_token")
        log("  ٤) قبل تشغيل «غرفة القيادة» عيّن مفتاح الفتح بنفس النافذة:")
        log("       $env:QUANT_MASTER_KEY = \"pass:<عبارتك>\"")
        log("     (نفس المفتاح يفتح أسرار النواة كلّها — واحد لكلّ شيء.)")
        return 2

    Surface(token, conf).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
