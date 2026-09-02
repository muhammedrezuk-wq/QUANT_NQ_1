import asyncio
import importlib.util
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[4]
folder = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
s = importlib.util.spec_from_file_location("a582", folder / "atom.py")
m = importlib.util.module_from_spec(s)
sys.modules["a582"] = m
s.loader.exec_module(m)

CFG = {
    "max_deviation_points": 50,
    "max_age_s": 5,
    "alignment_window_s": 0.15,
    "expected_return_abs": 0.0005,
    "suspicious_return_abs": 0.005,
}


class _Clock:
    value = 1000.0

    def now(self):
        return self.value

    def mono(self):
        return self.value

    def quality(self):
        return "SYNCED"

    def state(self):
        return {"quality": "SYNCED"}


CLOCK = _Clock()
m.clock = CLOCK


class L:
    def __getattr__(self, n):
        return lambda *a, **k: None


class B:
    def __init__(self):
        self.e = []

    def subscribe(self, *a):
        pass

    async def publish(self, n, p):
        self.e.append((n, p))


async def new(cfg=None):
    b = B()
    a = m.Atom()
    await a.initialize(m.AtomContext(582, dict(cfg or CFG), L(), b.publish, b.subscribe))
    await a.start()
    await a._on_specs({"symbols": [{"symbol": "X", "point": 1}]})
    return a, b


def last(b):
    return [p for n, p in b.e if n == m.EVENT_OUT][-1]


def _tick_ct(price, ts, received=None, **extra):
    body = {
        "symbol": "X",
        "price": price,
        "timestamp": ts,
        "source": "ctrader",
        "clock_domain": "ctrader",
        "received_at": CLOCK.value if received is None else received,
    }
    body.update(extra)
    return body


def _tick_mt(price, ts, received=None, **extra):
    body = {
        "symbol": "X",
        "price": price,
        "broker_timestamp": ts,
        "exchange_timestamp": extra.pop("exchange_timestamp", None),
        "source": "mt5",
        "clock_domain": "mt5",
        "received_at": CLOCK.value if received is None else received,
    }
    body.update(extra)
    return body


async def two_steps(a, b, ct0, mt0, ct1, mt1, ts0=None, ts1=None):
    t0 = CLOCK.value if ts0 is None else ts0
    t1 = CLOCK.value + 1.0 if ts1 is None else ts1
    await a._on_ct(_tick_ct(ct0, t0))
    await a._on_mt(_tick_mt(mt0, t0))
    seed = last(b)
    await a._on_ct(_tick_ct(ct1, t1))
    await a._on_mt(_tick_mt(mt1, t1))
    return seed, last(b)


async def case_1_level_offset_only():
    CLOCK.value = 1000.0
    a, b = await new()
    seed, r = await two_steps(a, b, 77100, 77300, 77110, 77310)
    assert seed["classification"] == "INSUFFICIENT_DATA", seed
    assert r["classification"] == "LEVEL_OFFSET_ONLY", r
    assert r["status"] == "SYNCED", r
    assert r["observe_only"] is True
    assert r["direction_agreement"] is True
    assert abs(r["reference_move"] - 10) < 1e-9
    assert abs(r["broker_move"] - 10) < 1e-9
    assert abs(r["level_offset"] - 200) < 1e-9
    print("582 — حالة ١: إزاحة مستوى ثابتة + حركة متطابقة = LEVEL_OFFSET_ONLY")


async def case_2_expected_divergence():
    CLOCK.value = 1000.0
    a, b = await new()
    _, r = await two_steps(a, b, 100.0, 100.0, 100.05, 100.10)
    assert r["classification"] == "EXPECTED_DIVERGENCE", r
    assert r["status"] == "SYNCED", r
    assert r["observe_only"] is True
    print("582 — حالة ٢: فرق حركة صغير = EXPECTED_DIVERGENCE")


async def case_3_one_large_move_observe():
    CLOCK.value = 1000.0
    a, b = await new()
    _, r = await two_steps(a, b, 100.0, 100.0, 100.01, 110.0)
    assert r["classification"] == "SUSPICIOUS_DIVERGENCE", r
    assert r["status"] == "SYNCED", r
    assert r["observe_only"] is True
    assert "BROKER_MANIPULATION" not in str(r)
    assert "MANIPULAT" not in str(r).upper()
    h = await a.health_check()
    assert h.state == m.HealthState.HEALTHY, h
    print("582 — حالة ٣: نقلة واحدة كبيرة = SUSPICIOUS_DIVERGENCE مراقبة بلا إيقاف")


async def case_4_clock_invalid_not_stale():
    CLOCK.value = 1000.0
    a, b = await new()
    await a._on_ct(_tick_ct(100.0, 1000.0))
    await a._on_mt({"symbol": "X", "price": 100.0, "exchange_timestamp": None, "received_at": 1000.0})
    r = last(b)
    assert r["classification"] == "CLOCK_INVALID", r
    assert r["status"] == "CLOCK_INVALID", r
    assert r["classification"] != "STALE"
    print("582 — حالة ٤: بلا طابع وسيط = CLOCK_INVALID لا STALE")


async def case_5_stale_is_received_age():
    CLOCK.value = 10000.0
    a, b = await new()
    sending = CLOCK.value - 10800.0
    await a._on_ct(_tick_ct(100.0, sending, received=CLOCK.value))
    await a._on_mt(_tick_mt(100.0, sending, received=CLOCK.value))
    r = last(b)
    assert r["classification"] != "STALE", ("SendingTime≠now ليست تقادمًا", r)
    assert r["classification"] in ("INSUFFICIENT_DATA", "NORMAL", "LEVEL_OFFSET_ONLY"), r

    CLOCK.value = 2000.0
    a, b = await new()
    await a._on_ct(_tick_ct(100.0, 2000.0, received=1990.0))
    await a._on_mt(_tick_mt(101.0, 2000.0, received=2000.0))
    r = last(b)
    assert r["classification"] == "STALE", r
    assert r["status"] == "STALE", r
    print("582 — حالة ٥: التقادم من عمر الاستقبال لا من SendingTime مقابل الحائط")


async def case_6_insufficient_one_side():
    CLOCK.value = 1000.0
    a, b = await new()
    await a._on_ct(_tick_ct(100.0, 1000.0))
    r = last(b)
    assert r["classification"] == "INSUFFICIENT_DATA", r
    assert r["status"] == "INSUFFICIENT_DATA", r
    h = await a.health_check()
    assert h.state == m.HealthState.DEGRADED, h
    assert h.details["compared"] == 0
    print("582 — حالة ٦: طرف واحد = INSUFFICIENT_DATA والصحة DEGRADED بلا compared")


async def case_3h_is_timezone_not_lag():
    """ساعة وسيط UTC+3 مقابل SendingTime UTC ليست تأخير سوق ولا تقادم."""
    CLOCK.value = 1_786_791_577.281
    a, b = await new()
    ct_ts = CLOCK.value
    mt_ts = CLOCK.value + 10800.0
    await a._on_ct(_tick_ct(77100.0, ct_ts, received=CLOCK.value))
    await a._on_mt(_tick_mt(
        77300.0, mt_ts, received=CLOCK.value,
        exchange_timestamp=None,
        broker_clock_offset_s=10800.0,
    ))
    await a._on_ct(_tick_ct(77110.0, ct_ts + 1.0, received=CLOCK.value))
    await a._on_mt(_tick_mt(
        77310.0, mt_ts + 1.0, received=CLOCK.value,
        exchange_timestamp=None,
        broker_clock_offset_s=10800.0,
    ))
    r = last(b)
    assert r["classification"] != "STALE", r
    assert r["classification"] != "CLOCK_INVALID", r
    assert r["timestamp_gap_s"] is None, ("لا تُطرح ساعتان من مجالين", r)
    assert r["timestamp_gap_s"] != 10800
    assert r["broker_clock_offset_s"] == 10800.0, r
    assert r["receipt_gap_s"] == 0.0, r
    assert r["classification"] == "LEVEL_OFFSET_ONLY", r
    assert r["status"] == "SYNCED", r
    print("582 — ٣ ساعات = إزاحة منطقة وسيط معلنة، ليست فجوة تكة ولا تقادم")


async def extras():
    CLOCK.value = 1000.0
    a, b = await new()
    await a._on_ct(_tick_ct(100.0, 1000.0))
    await a._on_mt({
        "symbol": "X",
        "price": 100.0,
        "exchange_timestamp": None,
        "broker_timestamp": 1000.0,
        "received_at": 1000.0,
        "source": "mt5",
        "clock_domain": "mt5",
    })
    r = last(b)
    assert r["classification"] != "STALE", r
    assert r["classification"] != "CLOCK_INVALID", r
    assert r["broker_timestamp"] == 1000.0, r

    _, r = await two_steps(a, b, 100.0, 100.0, 110.0, 110.0)
    assert r["classification"] == "NORMAL", r
    assert r["status"] == "SYNCED", r

    before = r["classification"]
    CLOCK.value = 1001.0
    await a._on_pulse({"official_time": 1001.0})
    pulsed = last(b)
    assert pulsed["classification"] == before, pulsed

    print("582 — إضافي: broker_timestamp عند exchange=None · النبض لا يعيد عيّنة حركة")


async def main():
    await case_1_level_offset_only()
    await case_2_expected_divergence()
    await case_3_one_large_move_observe()
    await case_4_clock_invalid_not_stale()
    await case_5_stale_is_received_age()
    await case_6_insufficient_one_side()
    await extras()
    await case_3h_is_timezone_not_lag()
    print("582 — ست حالات صناعية + حراس الورقة + ٣ ساعات ليست تأخير")


def test_582_case_1_level_offset_only():
    asyncio.run(case_1_level_offset_only())


def test_582_case_2_expected_divergence():
    asyncio.run(case_2_expected_divergence())


def test_582_case_3_one_large_move_observe():
    asyncio.run(case_3_one_large_move_observe())


def test_582_case_4_clock_invalid_not_stale():
    asyncio.run(case_4_clock_invalid_not_stale())


def test_582_case_5_stale_is_received_age():
    asyncio.run(case_5_stale_is_received_age())


def test_582_case_6_insufficient_one_side():
    asyncio.run(case_6_insufficient_one_side())


def test_582_paper_guards():
    asyncio.run(extras())


def test_582_three_hours_is_timezone_not_lag():
    asyncio.run(case_3h_is_timezone_not_lag())


def test_582_reference_divergence():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
