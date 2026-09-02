import asyncio
import inspect
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402
from pathlib import Path as _AtomPath  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom613", _AtomPath(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom613"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
TICK = _mod.EVENT_MARKET_TICK
VOLUME = _mod.EVENT_MARKET_VOLUME
REJECTED = _mod.EVENT_SOURCE_REJECTED
BROKER = _mod.DEFAULT_BROKER_ROUTE

# ٢٠٢٦-٠٨-٣١ (ختم NQ): القاعدة انقلبت. كانت هذه الاختبارات تحرس «ميتاتريدر
# احتياط عند صمت سي‑تريدر» — وهو ما منعه المالك بنصّه: «ما بصير ميتاتريدر
# يغذّي المحلّلين ولا ياخد محل سي‑تريدر». صارت تحرس المنع نفسه.
CFG = {
    "routes": {"feed.ctrader.tick": "market.tick"},
    "provider_timeout_s": 30,
    "max_input_silence_seconds": 60,
}
CFG_BOTH_ROUTES = {
    "routes": {"feed.mt5.tick": "market.tick", "feed.ctrader.tick": "market.tick"},
    "provider_timeout_s": 30,
    "max_input_silence_seconds": 60,
    "preferred_provider": "CTRADER",
}


class _NullLogger:
    def debug(self, *a): pass
    def info(self, *a): pass
    def warning(self, *a): pass
    def error(self, *a): pass
    def critical(self, *a): pass


class FakeEventBus:
    def __init__(self):
        self.published = []
        self._handlers = {}

    def subscribe(self, name, handler):
        self._handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.published.append((name, payload))
        for handler in self._handlers.get(name, []):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result

    def make_context(self, config):
        return AtomContext(atom_id=613, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _ready(bus, cfg=None):
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or CFG))
    await atom.start()
    return atom


async def test_routes_reference_tick_to_market_tick():
    print("\n--- test_routes_reference_tick_to_market_tick ---")
    bus = FakeEventBus()
    await _ready(bus)
    await bus.publish("feed.ctrader.tick",
                      {"provider": "CTRADER", "symbol": "NQ", "bid": 100.0, "ask": 100.5, "timestamp": 1000.0})
    ticks = [p for n, p in bus.published if n == TICK]
    assert len(ticks) == 1
    assert ticks[0]["symbol"] == "NQ" and ticks[0]["provider"] == "CTRADER"
    assert ticks[0]["timestamp"] == 1000.0, "وقت السوق من المصدر (قاعدة 13)"
    print(f"OK — وجّه feed.ctrader.tick → market.tick: {ticks[0]['symbol']}")


async def test_mt5_diverted_to_display_never_to_analysts():
    """حكم المالك ٢٠٢٦-٠٨-٣١: سعر ميتاتريدر لا يصل محلّلًا أبدًا — ولو كان
    سي‑تريدر مقفولًا. ولا يُرمى أيضًا: يذهب لقناة العرض بسبريده ليظهر على
    الشارت كسعر تنفيذ فعليّ."""
    print("\n--- test_mt5_diverted_to_display_never_to_analysts ---")
    bus = FakeEventBus()
    atom = await _ready(bus, CFG_BOTH_ROUTES)
    await bus.publish("feed.mt5.tick",
                      {"provider": "MT5", "symbol": "NQ", "bid": 1.0, "ask": 2.0, "timestamp": 100.0})
    assert not [p for n, p in bus.published if n == TICK], "ممنوع يصل market.tick"
    assert atom._forwarded == 0, "لم يُمرَّر لمسار التحليل"
    shown = [p for n, p in bus.published if n == BROKER]
    assert len(shown) == 1, "ولا يُرمى: لازم يوصل قناة العرض"
    assert shown[0]["provider"] == "MT5" and shown[0]["spread"] == 1.0
    assert shown[0]["analysis"] is False and shown[0]["display_only"] is True
    assert atom._diverted_to_display == 1 and atom._rejected_foreign == 0
    print(f"OK — حُوِّل للعرض بسبريد {shown[0]['spread']} ولم يمسّ محلّلًا")


async def test_foreign_source_rejected_loudly_without_display_route():
    """وإن أُغلقت قناة العرض: يُرفض ويصرخ — لا يسقط بصمت ولا يمرّ."""
    print("\n--- test_foreign_source_rejected_loudly_without_display_route ---")
    bus = FakeEventBus()
    atom = await _ready(bus, dict(CFG_BOTH_ROUTES, broker_display_route=""))
    await bus.publish("feed.mt5.tick",
                      {"provider": "MT5", "symbol": "NQ", "bid": 1, "ask": 2, "timestamp": 100.0})
    assert not [p for n, p in bus.published if n == TICK]
    assert atom._rejected_foreign == 1 and atom._diverted_to_display == 0
    rejects = [p for n, p in bus.published if n == REJECTED]
    assert len(rejects) == 1, "لازم يصرخ بحدث معلن"
    assert rejects[0]["provider"] == "MT5" and rejects[0]["reason"] == "FOREIGN_SOURCE_ON_ANALYST_PATH"
    assert "ميتاتريدر" in rejects[0]["message_ar"]
    status = await atom.health_check()
    assert status.state == HealthState.UNHEALTHY, status.state
    assert "MT5" in status.message, status.message
    print(f"OK — رُفض ورفع الصوت: {status.message[:60]}")


async def test_no_failover_when_reference_dies():
    """الأهمّ: سقوط سي‑تريدر **لا** يفتح الباب لميتاتريدر. التحليل يصمت."""
    print("\n--- test_no_failover_when_reference_dies ---")
    bus = FakeEventBus()
    atom = await _ready(bus, CFG_BOTH_ROUTES)
    await bus.publish("kernel.clock.heartbeat", {"official_time": 100.0})
    await bus.publish("feed.ctrader.tick",
                      {"provider": "CTRADER", "symbol": "NQ", "bid": 1.1, "ask": 2.1, "timestamp": 100.0})
    assert len([p for n, p in bus.published if n == TICK]) == 1
    # المرجع يصمت فوق المهلة — كان ميتاتريدر يتدفّق هنا قبل الختم.
    await bus.publish("kernel.clock.heartbeat", {"official_time": 200.0})
    await bus.publish("feed.mt5.tick",
                      {"provider": "MT5", "symbol": "NQ", "bid": 1.3, "ask": 2.3, "timestamp": 200.0})
    ticks = [p for n, p in bus.published if n == TICK]
    assert len(ticks) == 1, "لا تِكّة جديدة: المرجع ساقط والبديل ممنوع"
    assert ticks[-1]["provider"] == "CTRADER"
    assert atom._diverted_to_display == 1 and atom._rejected_foreign == 0
    print("OK — لا بديل احتياطي: التحليل صمت بدل أن يتغذّى من مصدر غير موثوق")


async def test_propagates_account_id_identity_key():
    """قاعدة 22 — يمرّر account_id لو المصدر حطّه."""
    print("\n--- test_propagates_account_id_identity_key ---")
    bus = FakeEventBus()
    await _ready(bus)
    await bus.publish("feed.ctrader.tick",
                      {"provider": "CTRADER", "symbol": "NQ", "bid": 1, "ask": 2, "timestamp": 5.0, "account_id": "ACC-1"})
    tick = [p for n, p in bus.published if n == TICK][-1]
    assert tick["account_id"] == "ACC-1", "لازم يمرّر account_id (قاعدة 22)"
    print("OK — مرّر account_id=ACC-1")


async def test_incomplete_payload_dropped_without_crash():
    """حالة فشل (قاعدة 9) — تكّة ناقصة تُسقَط بلا انهيار ولا نشر."""
    print("\n--- test_incomplete_payload_dropped_without_crash ---")
    bus = FakeEventBus()
    atom = await _ready(bus)
    await bus.publish("feed.ctrader.tick", {"provider": "CTRADER", "symbol": "NQ", "timestamp": 1.0})  # لا bid/ask
    assert not [p for n, p in bus.published if n == TICK], "ما ينشر عند نقص"
    assert atom._dropped == 1
    print("OK — تكّة ناقصة أُسقطت (dropped=1) بلا انهيار")


async def test_health_check_surfaces_single_dead_provider():
    """عطل حي مقيس 2026-08-19: ساعة صمت واحدة مشتركة كانت تخفي موت مصدر
    بينما مصدر آخر حيّ. يُختبر هنا بمصدرين **مسموحين** كلاهما، فالسياسة
    (منع ميتاتريدر) شيء وكشف المصدر الميت شيء آخر — لا يُخلطان."""
    print("\n--- test_health_check_surfaces_single_dead_provider ---")
    bus = FakeEventBus()
    atom = Atom()
    cfg = {"routes": {"feed.mt5.tick": "market.tick", "feed.ctrader.tick": "market.tick"},
           "provider_timeout_s": 30, "max_input_silence_seconds": 60,
           "preferred_provider": "CTRADER",
           "analyst_sources": ["CTRADER", "BACKUP"]}
    await atom.initialize(bus.make_context(cfg))
    await atom.start()
    await bus.publish("kernel.clock.heartbeat", {"official_time": 100.0})
    await bus.publish("feed.mt5.tick",
                      {"provider": "BACKUP", "symbol": "NQ", "bid": 1, "ask": 2, "timestamp": 100.0})
    await bus.publish("feed.ctrader.tick",
                      {"provider": "CTRADER", "symbol": "NQ", "bid": 1.1, "ask": 2.1, "timestamp": 100.0})
    healthy = await atom.health_check()
    assert healthy.state == HealthState.HEALTHY
    # BACKUP يصمت فوق provider_timeout_s بينما cTrader يستمر يتدفّق بلا انقطاع.
    for t in range(101, 140):
        await bus.publish("kernel.clock.heartbeat", {"official_time": float(t)})
        await bus.publish("feed.ctrader.tick",
                          {"provider": "CTRADER", "symbol": "NQ", "bid": 1.1, "ask": 2.1, "timestamp": float(t)})
    status = await atom.health_check()
    assert status.state == HealthState.DEGRADED, (
        f"BACKUP صامت 39 ثانية رغم أن cTrader حيّ تماماً — يجب أن تُبلَّغ متعثّرة لا سليمة، "
        f"الحالة الفعلية: {status.state} / {status.message}"
    )
    assert "BACKUP" in status.message, f"الرسالة يجب أن تسمّي المصدر الميت صراحة: {status.message}"
    print(f"OK — health_check سمّت المصدر الميت: {status.message}")


async def test_volume_published_when_present():
    print("\n--- test_volume_published_when_present ---")
    bus = FakeEventBus()
    await _ready(bus)
    await bus.publish("feed.ctrader.tick",
                      {"provider": "CTRADER", "symbol": "ES", "bid": 1, "ask": 2, "timestamp": 3.0, "volume": 500})
    vols = [p for n, p in bus.published if n == VOLUME]
    assert len(vols) == 1 and vols[0]["volume"] == 500
    print("OK — نشر market.volume عند وجود حجم")


async def test_garbage_only_feed_does_not_fake_freshness():
    """v2.5.0: ساعة الصمت لا تُنعِش إلا بتكة صحيحة فعلاً."""
    print("\n--- test_garbage_only_feed_does_not_fake_freshness ---")
    import time as _time
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG, max_input_silence_seconds=5)))
    await atom.start()
    # قبل الإصلاح: كل حزمة تالفة كانت تُنعِش _last_input_at رغم إسقاطها --
    # فتغذية لا تنتج تكة صحيحة واحدة أبداً كانت تبقى "طازجة" للأبد.
    atom._last_input_at = _time.time() - 999  # صمت طويل مسبقاً، بلا أي تكة صحيحة
    for _ in range(5):
        await bus.publish("feed.ctrader.tick",
                          {"provider": "CTRADER", "symbol": "NQ", "timestamp": 1.0})  # لا bid/ask
    assert atom._dropped == 5, atom._dropped
    assert atom._forwarded == 0, atom._forwarded
    health = await atom.health_check()
    assert health.state == HealthState.DEGRADED, health.state
    assert "INPUT_STARVED" in health.message, health.message
    print("OK — تغذية لا تنتج إلا حزماً تالفة تُصنَّف DEGRADED فعلاً، لا HEALTHY وهمياً")


async def main():
    tests = [
        test_routes_reference_tick_to_market_tick,
        test_mt5_diverted_to_display_never_to_analysts,
        test_foreign_source_rejected_loudly_without_display_route,
        test_no_failover_when_reference_dies,
        test_propagates_account_id_identity_key,
        test_incomplete_payload_dropped_without_crash,
        test_health_check_surfaces_single_dead_provider,
        test_mt5_alone_does_not_fake_analyst_health,
        test_volume_published_when_present,
        test_garbage_only_feed_does_not_fake_freshness,
    ]
    failed = []
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAILED: {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e)))
            print(f"ERROR: {t.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}")
        sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
