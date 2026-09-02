import asyncio
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

_spec = _ilu.spec_from_file_location(
    "_atom811", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom811"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT


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

    def make_context(self, config):
        return AtomContext(atom_id=811, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _card(direction, value, strength=50.0, confidence=60.0, net=0.5,
          participation=0.4, stamp=1000.0, complete=True, symbol="BTCUSD"):
    return {"account_id": "A", "broker": "BR", "symbol": symbol,
            "cycle_id": "%s|%s" % (symbol, stamp), "direction": direction,
            "direction_value": value, "strength_value": strength,
            "confidence_value": confidence, "net": net,
            "participation": participation, "complete": complete,
            "source_timestamp": stamp}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    return atom, bus


def _out(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_first_cycle_has_no_previous_and_says_so():
    print("\n--- test_first_cycle_has_no_previous_and_says_so ---")
    atom, bus = await _new()
    await atom._on_scored(_card("buy", 60.0))
    row = _out(bus)[-1]
    for field in ("direction", "strength", "confidence", "net", "participation"):
        assert row[field]["previous"] is None
        assert row[field]["delta"] is None
    assert row["direction"]["current"] == 60.0
    print("OK — الدورة الأولى بلا سابق، والفرق null معلَن لا صفر")


async def test_deltas_and_jumps_are_measured():
    print("\n--- test_deltas_and_jumps_are_measured ---")
    atom, bus = await _new()
    await atom._on_scored(_card("buy", 60.0, strength=50.0, confidence=60.0,
                                stamp=1000.0))
    await atom._on_scored(_card("buy", 72.0, strength=44.0, confidence=65.0,
                                stamp=1030.0))
    row = _out(bus)[-1]
    assert row["direction"]["delta"] == 12.0
    assert row["strength"]["delta"] == -6.0
    assert row["confidence"]["delta"] == 5.0
    stats = row["jump_stats"]["direction"]
    assert stats["count"] == 1 and stats["max"] == 12.0
    assert row["jump_stats"]["strength"]["max"] == 6.0
    print("OK — الفروق موقّعة والقفز مطلق: 12 · 6 · 5")


async def test_direct_reversal_is_told_apart_from_after_neutral():
    print("\n--- test_direct_reversal_is_told_apart_from_after_neutral ---")
    atom, bus = await _new()
    await atom._on_scored(_card("buy", 60.0, stamp=1000.0))
    await atom._on_scored(_card("sell", -60.0, stamp=1010.0))
    direct = _out(bus)[-1]
    assert direct["reversal_kind"] == "DIRECT_REVERSAL"
    assert direct["transition"] == "BUY_TO_SELL"
    await atom._on_scored(_card("neutral", 0.0, stamp=1020.0))
    await atom._on_scored(_card("buy", 55.0, stamp=1030.0))
    after = _out(bus)[-1]
    assert after["reversal_kind"] == "REVERSAL_AFTER_NEUTRAL"
    counts = after["counts"]
    assert counts["direct_reversals"] == 1
    assert counts["reversals_after_neutral"] == 1
    assert counts["reversals"] == 2
    print("OK — الانقلاب المباشر مفصول عن الانقلاب بعد الحياد")


async def test_run_length_grows_then_resets():
    print("\n--- test_run_length_grows_then_resets ---")
    atom, bus = await _new()
    for index in range(4):
        await atom._on_scored(_card("buy", 60.0, stamp=1000.0 + index))
    assert _out(bus)[-1]["run_length"] == 4
    await atom._on_scored(_card("sell", -60.0, stamp=1004.0))
    row = _out(bus)[-1]
    assert row["run_length"] == 1
    assert row["run_stats"]["max"] == 4
    print("OK — طول البقاء يتراكم ثمّ يُصفَّر عند تبدّل الجهة")


async def test_counts_ride_beside_the_jumps():
    print("\n--- test_counts_ride_beside_the_jumps ---")
    atom, bus = await _new()
    await atom._on_scored(_card("buy", 60.0, stamp=1000.0))
    await atom._on_scored(_card("neutral", 0.0, stamp=1010.0, complete=False))
    counts = _out(bus)[-1]["counts"]
    assert counts["decisions"] == 2 and counts["signals"] == 1
    assert counts["neutral"] == 1 and counts["ready"] == 1
    print("OK — الإشارات والجاهزة والمحايدة تُعدّ بجانب القفز")


async def test_rates_come_from_market_time_and_are_null_without_it():
    print("\n--- test_rates_come_from_market_time_and_are_null_without_it ---")
    atom, bus = await _new()
    await atom._on_scored(_card("buy", 60.0, stamp=1000.0))
    assert _out(bus)[-1]["rates_per_minute"]["decisions"] is None
    await atom._on_scored(_card("buy", 61.0, stamp=1030.0))
    row = _out(bus)[-1]
    assert row["elapsed_market_s"] == 30.0
    assert row["rates_per_minute"]["decisions"] == 4.0
    assert row["measured_from"] == "market_stamp"
    print("OK — المعدّل من ختم السوق: دورتان في 30 ثانية = 4/دقيقة · وبلا زمن null")


async def test_missing_field_is_refused_and_counted():
    print("\n--- test_missing_field_is_refused_and_counted ---")
    atom, bus = await _new()
    card = _card("buy", 60.0)
    card.pop("confidence_value")
    await atom._on_scored(card)
    assert not _out(bus)
    assert atom._dropped == 1
    assert "FIELD_MISSING:confidence" in atom._drop_reasons
    print("OK — الحقل الغائب يُرفض ويُعدّ بسببه، ولا يُصفَّر")


async def test_scopes_do_not_mix():
    print("\n--- test_scopes_do_not_mix ---")
    atom, bus = await _new()
    await atom._on_scored(_card("buy", 60.0, symbol="BTCUSD", stamp=1000.0))
    await atom._on_scored(_card("sell", -70.0, symbol="XAUUSD", stamp=1000.0))
    row = _out(bus)[-1]
    assert row["symbol"] == "XAUUSD"
    assert row["direction"]["previous"] is None
    assert len(atom._books) == 2
    print("OK — كل رمز كتابه، ولا يتسرّب رقم من رمز إلى آخر")


async def test_state_survives_a_reload():
    print("\n--- test_state_survives_a_reload ---")
    atom, bus = await _new()
    await atom._on_scored(_card("buy", 60.0, stamp=1000.0))
    await atom._on_scored(_card("buy", 70.0, stamp=1010.0))
    state = await atom.snapshot()
    fresh = Atom()
    await fresh.initialize(FakeEventBus().make_context({}))
    await fresh.restore(state)
    await fresh.start()
    assert fresh._seen == 2 and len(fresh._books) == 1
    book = list(fresh._books.values())[0]
    assert list(book["jumps"]["direction"]) == [10.0]
    print("OK — العدّاد ينجو من إعادة التحميل")


async def test_health_declares_hunger_then_numbers():
    print("\n--- test_health_declares_hunger_then_numbers ---")
    atom, bus = await _new()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_scored(_card("buy", 60.0))
    health = await atom.health_check()
    assert health.state == HealthState.HEALTHY
    assert health.details["scopes"] == 1
    assert "A|BR|BTCUSD" in health.details["books"]
    print("OK — الصحّة: جائعة قبل أوّل بطاقة، وبأرقام بعدها")


async def main():
    tests = [test_first_cycle_has_no_previous_and_says_so,
             test_deltas_and_jumps_are_measured,
             test_direct_reversal_is_told_apart_from_after_neutral,
             test_run_length_grows_then_resets,
             test_counts_ride_beside_the_jumps,
             test_rates_come_from_market_time_and_are_null_without_it,
             test_missing_field_is_refused_and_counted,
             test_scopes_do_not_mix,
             test_state_survives_a_reload,
             test_health_declares_hunger_then_numbers]
    failed = []
    for test in tests:
        try:
            await test()
        except AssertionError as exc:
            failed.append("%s: %s" % (test.__name__, exc))
            print("FAIL —", test.__name__, exc)
    if failed:
        raise SystemExit("\n".join(failed))
    print("\n%d/%d اختبارًا ناجحًا" % (len(tests), len(tests)))


if __name__ == "__main__":
    asyncio.run(main())
