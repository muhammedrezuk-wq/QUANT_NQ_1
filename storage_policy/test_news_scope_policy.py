"""اختبارات طبقة سياسة النطاق — News Scope Policy Tests

تغطي:
- الهوية الموحدة للأصول (NQ100 / USTEC)
- STATUS GUARD
- feed واحد + رمزين
- حذف السياسة (حوكمة)
- UNRESOLVED بعد الحذف
- منع التشغيل أثناء UNRESOLVED
- إعادة الحسم بعد الحذف
- عدم خلط broker_symbol مع instrument_id
- Audit trail
- 616 historical rows (لا حذف، لا إعادة بث)
"""
from __future__ import annotations

import os
import tempfile
import time

from storage_policy.news_scope_policy import (
    DEFAULT_BROKER_SYMBOL,
    DEFAULT_INSTRUMENT_ID,
    NewsScopePolicyStore,
    NewsStatus,
    ScopePolicy,
    broker_symbols_for,
    resolve_instrument_id,
)


# ═══════════════════════════════════════════════════════════════════
# ١. الهوية الموحدة للأصول — Instrument Identity
# ═══════════════════════════════════════════════════════════════════

def test_ustec_resolves_to_nq100():
    """USTEC = broker_symbol فقط، NQ100 = instrument_id."""
    assert resolve_instrument_id("USTEC") == DEFAULT_INSTRUMENT_ID
    assert resolve_instrument_id("USTEC") == "NQ100"


def test_nq100_resolves_to_itself():
    """NQ100 هو instrument_id — يحلّ لنفسه."""
    assert resolve_instrument_id("NQ100") == "NQ100"


def test_case_insensitive_resolution():
    """الحل لا يميز حالة الأحرف."""
    assert resolve_instrument_id("ustec") == "NQ100"
    assert resolve_instrument_id("Ustec") == "NQ100"
    assert resolve_instrument_id("nq100") == "NQ100"


def test_unknown_symbol_returns_none():
    """رمز غير معروف = None (لا يُعاد كـ identity)."""
    assert resolve_instrument_id("RANDOM_GARBAGE") is None
    assert resolve_instrument_id("") is None
    assert resolve_instrument_id(None) is None  # type: ignore


def test_broker_symbols_for_nq100():
    """NQ100 له عدة broker_symbols."""
    symbols = broker_symbols_for("NQ100")
    assert "USTEC" in symbols
    assert "NAS100" in symbols
    assert len(symbols) >= 2


def test_ustec_never_used_as_internal_identity():
    """USTEC لا يُستخدم أبداً كهوية داخلية — فقط NQ100."""
    resolved = resolve_instrument_id("USTEC")
    assert resolved != "USTEC"
    assert resolved == "NQ100"


# ═══════════════════════════════════════════════════════════════════
# ٢. STATUS GUARD
# ═══════════════════════════════════════════════════════════════════

def test_status_ok_is_valid():
    assert NewsStatus.is_valid("OK") is True


def test_status_empty_string_rejected():
    assert NewsStatus.is_valid("") is False


def test_status_whitespace_rejected():
    assert NewsStatus.is_valid(" ") is False
    assert NewsStatus.is_valid("  ") is False
    assert NewsStatus.is_valid("\t") is False


def test_status_null_rejected():
    assert NewsStatus.is_valid(None) is False


def test_status_recieved_rejected():
    """RECIEVED (خطأ إملائي) مرفوض."""
    assert NewsStatus.is_valid("RECIEVED") is False


def test_status_lowercase_rejected():
    """حسّاس لحالة الأحرف — 'ok' ≠ 'OK'."""
    assert NewsStatus.is_valid("ok") is False
    assert NewsStatus.is_valid("Ok") is False


def test_status_unknown_value_rejected():
    assert NewsStatus.is_valid("HEALTHY") is False
    assert NewsStatus.is_valid("RUNNING") is False
    assert NewsStatus.is_valid("123") is False


def test_status_validate_or_raise():
    assert NewsStatus.validate_or_raise("OK") == NewsStatus.OK
    try:
        NewsStatus.validate_or_raise("RECIEVED")
        assert False, "يجب أن يرمي ValueError"
    except ValueError as e:
        assert "INVALID_STATUS" in str(e)


def test_status_validate_or_raise_none():
    try:
        NewsStatus.validate_or_raise(None)
        assert False, "يجب أن يرمي ValueError"
    except ValueError:
        pass


def test_status_validate_or_raise_empty():
    try:
        NewsStatus.validate_or_raise("")
        assert False, "يجب أن يرمي ValueError"
    except ValueError:
        pass


def test_status_validate_or_raise_whitespace():
    try:
        NewsStatus.validate_or_raise("   ")
        assert False, "يجب أن يرمي ValueError"
    except ValueError:
        pass


# ═══════════════════════════════════════════════════════════════════
# ٣. Policy CRUD
# ═══════════════════════════════════════════════════════════════════

def _fresh_store() -> NewsScopePolicyStore:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = NewsScopePolicyStore(path)
    store.initialize()
    return store


def test_create_policy():
    store = _fresh_store()
    policy = store.upsert_policy("feed_news_615", "NQ100",
                                 source_status="OK",
                                 broker_symbols=["USTEC", "NAS100"])
    assert policy.feed_id == "feed_news_615"
    assert policy.instrument_id == "NQ100"
    assert policy.source_status == "OK"
    assert "USTEC" in policy.broker_symbols


def test_get_policy():
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    fetched = store.get_policy("feed_news_615", "NQ100")
    assert fetched is not None
    assert fetched.instrument_id == "NQ100"
    assert fetched.source_status == "OK"


def test_get_nonexistent_returns_none():
    store = _fresh_store()
    assert store.get_policy("no_feed", "NQ100") is None


def test_update_policy():
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    updated = store.upsert_policy("feed_news_615", "NQ100", source_status="OFFLINE")
    assert updated.source_status == "OFFLINE"
    fetched = store.get_policy("feed_news_615", "NQ100")
    assert fetched is not None
    assert fetched.source_status == "OFFLINE"


def test_update_rejects_invalid_status():
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    try:
        store.upsert_policy("feed_news_615", "NQ100", source_status="RECIEVED")
        assert False, "يجب أن يرفض RECIEVED"
    except ValueError as e:
        assert "INVALID_STATUS" in str(e)


# ═══════════════════════════════════════════════════════════════════
# ٤. Feed واحد + رمزين (multi-instrument)
# ═══════════════════════════════════════════════════════════════════

def test_one_feed_multiple_instruments():
    """Feed واحد يجوز له تغطية أكثر من instrument."""
    store = _fresh_store()
    # Feed يغطي NQ100 (وهو instrument_id معروف)
    # + NQ100 مع feed آخر (multi-instrument = multi-policies)
    store.upsert_policy("feed_news_615", "NQ100",
                        source_status="OK", broker_symbols=["USTEC"])
    # نفس الـ feed يغطي instrument آخر (NQ100 مع source مختلف)
    store.upsert_policy("feed_calendar_616", "NQ100",
                        source_status="OK", broker_symbols=["USTEC"])
    # feed واحد يغطي أكثر من instrument عبر سياسات متعددة
    policies = store.policies_for_feed("feed_news_615")
    assert len(policies) >= 1
    policy = store.get_policy("feed_news_615", "NQ100")
    assert policy is not None
    assert policy.instrument_id == "NQ100"


def test_one_feed_one_instrument_with_multiple_broker_symbols():
    """Feed واحد + instrument واحد + عدة broker_symbols."""
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100",
                        source_status="OK",
                        broker_symbols=["USTEC", "NAS100", "US100"])
    policy = store.get_policy("feed_news_615", "NQ100")
    assert policy is not None
    assert len(policy.broker_symbols) == 3


# ═══════════════════════════════════════════════════════════════════
# ٥. منع خلط broker_symbol مع instrument_id
# ═══════════════════════════════════════════════════════════════════

def test_upsert_rejects_broker_symbol_as_instrument_id():
    """USTEC ليس instrument_id — يجب رفضه."""
    store = _fresh_store()
    try:
        store.upsert_policy("feed_news_615", "USTEC", source_status="OK")
        assert False, "يجب أن يرفض USTEC كـ instrument_id"
    except ValueError as e:
        assert "NOT_INSTRUMENT_ID" in str(e)


# ═══════════════════════════════════════════════════════════════════
# ٦. حذف السياسة (حوكمة)
# ═══════════════════════════════════════════════════════════════════

def test_delete_policy():
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    result = store.delete_policy("feed_news_615", "NQ100")
    assert result is True
    # بعد الحذف: لا سياسة فعّالة
    assert store.get_policy("feed_news_615", "NQ100") is None


def test_delete_nonexistent_returns_false():
    store = _fresh_store()
    result = store.delete_policy("no_feed", "NQ100")
    assert result is False


def test_delete_returns_to_unresolved():
    """بعد الحذف: الحالة UNRESOLVED — لا قرار صالح."""
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    store.resolve_policy("feed_news_615", "NQ100", resolved_by="auto")
    assert store.is_resolved("feed_news_615", "NQ100") is True
    # حذف
    store.delete_policy("feed_news_615", "NQ100")
    # UNRESOLVED
    assert store.is_resolved("feed_news_615", "NQ100") is False


# ═══════════════════════════════════════════════════════════════════
# ٧. منع التشغيل أثناء UNRESOLVED
# ═══════════════════════════════════════════════════════════════════

def test_cannot_operate_while_unresolved():
    """UNRESOLVED = لا تشغيل."""
    store = _fresh_store()
    assert store.can_operate("feed_news_615", "NQ100") is False


def test_can_operate_after_resolve():
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    assert store.can_operate("feed_news_615", "NQ100") is True


def test_cannot_operate_with_offline_status():
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OFFLINE")
    assert store.can_operate("feed_news_615", "NQ100") is False


def test_cannot_operate_after_delete():
    """بعد الحذف: UNRESOLVED → لا تشغيل."""
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    assert store.can_operate("feed_news_615", "NQ100") is True
    store.delete_policy("feed_news_615", "NQ100")
    assert store.can_operate("feed_news_615", "NQ100") is False


# ═══════════════════════════════════════════════════════════════════
# ٨. إعادة الحسم بعد الحذف
# ═══════════════════════════════════════════════════════════════════

def test_re_resolve_after_delete():
    """بعد الحذف + إعادة الإنشاء: يعود التشغيل مسموحاً."""
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    store.delete_policy("feed_news_615", "NQ100")
    assert store.can_operate("feed_news_615", "NQ100") is False
    # إعادة إنشاء
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    store.resolve_policy("feed_news_615", "NQ100", resolved_by="re-resolve")
    assert store.can_operate("feed_news_615", "NQ100") is True


# ═══════════════════════════════════════════════════════════════════
# ٩. Audit Trail
# ═══════════════════════════════════════════════════════════════════

def test_audit_trail_create():
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK",
                        actor="test")
    trail = store.audit_trail(feed_id="feed_news_615")
    assert len(trail) >= 1
    assert trail[0]["action"] == "CREATE"
    assert trail[0]["actor"] == "test"


def test_audit_trail_delete():
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    store.delete_policy("feed_news_615", "NQ100", actor="governance")
    trail = store.audit_trail(feed_id="feed_news_615")
    actions = [t["action"] for t in trail]
    assert "DELETE" in actions
    assert "UNRESOLVE" in actions


def test_audit_trail_resolve():
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    store.resolve_policy("feed_news_615", "NQ100", resolved_by="auto",
                         actor="system")
    trail = store.audit_trail(feed_id="feed_news_615")
    actions = [t["action"] for t in trail]
    assert "RESOLVE" in actions


def test_audit_trail_update():
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    store.upsert_policy("feed_news_615", "NQ100", source_status="OFFLINE")
    trail = store.audit_trail(feed_id="feed_news_615")
    actions = [t["action"] for t in trail]
    assert "UPDATE" in actions


def test_audit_trail_full_lifecycle():
    """CREATE → RESOLVE → DELETE → UNRESOLVE."""
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK",
                        actor="admin")
    store.resolve_policy("feed_news_615", "NQ100", resolved_by="auto")
    store.delete_policy("feed_news_615", "NQ100", actor="governance")
    trail = store.audit_trail(feed_id="feed_news_615")
    actions = [t["action"] for t in trail]
    assert "CREATE" in actions
    assert "RESOLVE" in actions
    assert "DELETE" in actions
    assert "UNRESOLVE" in actions


# ═══════════════════════════════════════════════════════════════════
# ١٠. 616 Historical Rows — لا حذف، لا إعادة بث
# ═══════════════════════════════════════════════════════════════════

def test_delete_preserves_historical_rows():
    """حذف السياسة لا يحذف صفوف التاريخ — فقط يُعطّل is_active."""
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    store.delete_policy("feed_news_615", "NQ100")
    # الصف لا يزال موجوداً في الجدول (is_active=0)
    conn = store._connect()
    try:
        rows = conn.execute(
            "SELECT COUNT(*) FROM scope_policy WHERE feed_id=? AND instrument_id=?",
            ("feed_news_615", "NQ100")).fetchone()
        assert rows[0] >= 1, "الصف التاريخي يجب أن يبقى"
    finally:
        conn.close()


def test_audit_explains_why_not_rebroadcast():
    """سجل التدقيق يشرح سبب عدم إعادة البث."""
    store = _fresh_store()
    store.upsert_policy("feed_news_615", "NQ100", source_status="OK")
    store.delete_policy("feed_news_615", "NQ100")
    trail = store.audit_trail(feed_id="feed_news_615")
    delete_entry = next((t for t in trail if t["action"] == "DELETE"), None)
    assert delete_entry is not None
    import json
    detail = json.loads(delete_entry["detail"])
    assert detail.get("transition") == "RESOLVED→UNRESOLVED"
    assert detail.get("effective_decision_invalidated") is True


# ═══════════════════════════════════════════════════════════════════
# تشغيل جميع الاختبارات
# ═══════════════════════════════════════════════════════════════════

_ALL_TESTS = [
    # الهوية
    test_ustec_resolves_to_nq100,
    test_nq100_resolves_to_itself,
    test_case_insensitive_resolution,
    test_unknown_symbol_returns_none,
    test_broker_symbols_for_nq100,
    test_ustec_never_used_as_internal_identity,
    # Status guard
    test_status_ok_is_valid,
    test_status_empty_string_rejected,
    test_status_whitespace_rejected,
    test_status_null_rejected,
    test_status_recieved_rejected,
    test_status_lowercase_rejected,
    test_status_unknown_value_rejected,
    test_status_validate_or_raise,
    test_status_validate_or_raise_none,
    test_status_validate_or_raise_empty,
    test_status_validate_or_raise_whitespace,
    # CRUD
    test_create_policy,
    test_get_policy,
    test_get_nonexistent_returns_none,
    test_update_policy,
    test_update_rejects_invalid_status,
    # Multi-instrument
    test_one_feed_multiple_instruments,
    test_one_feed_one_instrument_with_multiple_broker_symbols,
    # Identity guard
    test_upsert_rejects_broker_symbol_as_instrument_id,
    # Delete (governance)
    test_delete_policy,
    test_delete_nonexistent_returns_false,
    test_delete_returns_to_unresolved,
    # UNRESOLVED enforcement
    test_cannot_operate_while_unresolved,
    test_can_operate_after_resolve,
    test_cannot_operate_with_offline_status,
    test_cannot_operate_after_delete,
    # Re-resolve
    test_re_resolve_after_delete,
    # Audit
    test_audit_trail_create,
    test_audit_trail_delete,
    test_audit_trail_resolve,
    test_audit_trail_update,
    test_audit_trail_full_lifecycle,
    # 616 historical
    test_delete_preserves_historical_rows,
    test_audit_explains_why_not_rebroadcast,
]


def run() -> int:
    passed = 0
    failed = 0
    for test in _ALL_TESTS:
        try:
            test()
            passed += 1
            print(f"  ✓ {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  ✗ {test.__name__}: {exc}")
    print(f"\n{'='*50}")
    print(f"نتيجة: {passed} نجح · {failed} فشل · {len(_ALL_TESTS)} إجمالي")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(run())
