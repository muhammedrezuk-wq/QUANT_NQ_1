import asyncio
import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# عزل سجلّ المُعامِلات: الاختبار لا يقرأ ولا يكتب القاعدة الحيّة أبدًا.
os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = os.path.join(
    tempfile.mkdtemp(), "analysis_settings_test.db")

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom450", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom450"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT
EVENT_RECORD = _mod.EVENT_RECORD

CFG = {"timeout_seconds": 5}

IDENTITY = {"account_id": "ACC-1", "broker": "ctrader", "symbol": "BTCUSD",
            "timeframe": "60s", "period_start": 7.0, "decision_id": "D-1"}
CYCLE = "BTCUSD|60s|7.0"


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
        return AtomContext(atom_id=450, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _eligibility(unit_id, status, reason=None, identity=IDENTITY):
    payload = {"id": unit_id, "cycle_id": CYCLE, "status": status,
               "reason": reason, "source_timestamp": 7.5}
    payload.update(identity)
    return payload


def _resolved(direction="buy", reason="RESOLVED", conflict=False,
              identity=IDENTITY):
    payload = {"id": "conflict_resolver", "cycle_id": CYCLE,
               "direction": direction, "signal": direction, "reason": reason,
               "conflict": conflict, "source_timestamp": 7.6}
    payload.update(identity)
    return payload


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    return atom, bus


def _records(bus):
    return [p for n, p in bus.published if n == EVENT_RECORD]


async def test_record_unifies_three_states_and_resolution():
    print("\n--- test_record_unifies_three_states_and_resolution ---")
    atom, bus = await _new()
    await atom._on_time({"official_time": 100.0})
    await atom._on_buy_state(_eligibility("buy_eligibility", "eligible"))
    await atom._on_sell_state(_eligibility(
        "sell_eligibility", "not_eligible", "DIRECTION_ABOVE_THRESHOLD"))
    await atom._on_wait_state(_eligibility("wait_state", "inactive",
                                           "BUY_SIDE_ELIGIBLE"))
    partial = _records(bus)[-1]
    # القرار النهائي لا يُخترع هنا: قبل وصول الحسم يبقى None.
    assert partial["final_decision"] is None and partial["complete"] is False
    await atom._on_resolved(_resolved("buy"))
    record = _records(bus)[-1]
    assert record["final_decision"] == "buy" and record["final_reason"] == "RESOLVED"
    assert record["buy_eligibility"] == {"status": "eligible", "reason": None}
    assert record["sell_eligibility"] == {"status": "not_eligible",
                                          "reason": "DIRECTION_ABOVE_THRESHOLD"}
    assert record["wait_state"] == {"status": "inactive",
                                    "reason": "BUY_SIDE_ELIGIBLE"}
    assert record["complete"] is True
    for key, value in IDENTITY.items():
        assert record[key] == value, (key, record[key])
    assert record["warnings"] == []
    times = record["stage_times"]
    assert set(times) == {"buy_eligibility", "sell_eligibility", "wait",
                          "resolution"}
    assert times["buy_eligibility"]["source_timestamp"] == 7.5
    assert times["buy_eligibility"]["received_at"] == 100.0
    assert times["resolution"]["source_timestamp"] == 7.6
    print("OK — سجل موحد: القرار وأهلية الجانبين وحالة الانتظار وأوقات المراحل")


async def test_q8_19_record_keeps_both_eligible_resolution_from_458():
    print("\n--- test_q8_19_record_keeps_both_eligible_resolution_from_458 ---")
    atom, bus = await _new()
    await atom._on_buy_state(_eligibility("buy_eligibility", "eligible"))
    await atom._on_sell_state(_eligibility("sell_eligibility", "eligible"))
    await atom._on_wait_state(_eligibility("wait_state", "inactive",
                                           "BOTH_SIDES_ELIGIBLE"))
    record = _records(bus)[-1]
    # ق٨ §١٩: حالتان مؤهلتان تبقيان كما وصلتا — 450 لا يحسم.
    assert record["buy_eligibility"]["status"] == "eligible"
    assert record["sell_eligibility"]["status"] == "eligible"
    assert record["final_decision"] is None
    await atom._on_resolved(_resolved("wait", "RESOLVED_WITH_CONFLICT", True))
    record = _records(bus)[-1]
    assert record["final_decision"] == "wait" and record["conflict"] is True
    assert record["buy_eligibility"]["status"] == "eligible"
    assert record["sell_eligibility"]["status"] == "eligible"
    print("OK — §19: التعارض محفوظ كما هو والحسم وصل من 458 وحده")


async def test_record_missing_decision_id_declared_not_invented():
    print("\n--- test_record_missing_decision_id_declared_not_invented ---")
    atom, bus = await _new()
    identity = {k: v for k, v in IDENTITY.items() if k != "decision_id"}
    await atom._on_buy_state(_eligibility("buy_eligibility", "not_eligible",
                                          "STATE_NOT_READY", identity))
    record = _records(bus)[-1]
    assert record["decision_id"] is None
    assert record["record_key"] == CYCLE  # ربط بالدورة الحقيقية لا باختراع
    assert "identity_incomplete" in record["warnings"]
    assert "decision_id" in record["missing_identity"]
    print("OK — سجل بلا decision_id يُعلن النقص ويربط بالدورة")


async def test_legacy_cycle_collection_still_complete():
    print("\n--- test_legacy_cycle_collection_still_complete ---")
    atom, bus = await _new()
    await atom._on_tick({"symbol": "BTCUSD", "account_id": "ACC-1",
                           "broker": "ctrader", "sequence": "7", "cycle_id": CYCLE})
    units = [("decision.aggregated.state", "aggregate"),
             ("decision.scored.state", "score_calculator"),
             ("decision.filtered.state", "decision_filter"),
             ("decision.eligibility.buy.state", "buy_eligibility"),
             ("decision.eligibility.sell.state", "sell_eligibility"),
             ("decision.wait.state", "wait_state"),
             ("decision.approved.state", "approval")]
    for event, unit_id in units:
        payload = {"id": unit_id, "cycle_id": CYCLE, "symbol": "BTCUSD"}
        if event == "decision.eligibility.buy.state":
            await atom._on_buy_state(payload)
        elif event == "decision.eligibility.sell.state":
            await atom._on_sell_state(payload)
        elif event == "decision.wait.state":
            await atom._on_wait_state(payload)
        else:
            await atom._on_unit_state(payload)
    collected = [p for n, p in bus.published if n == EVENT_OUT]
    assert collected, "cycle must forward when all units arrive"
    last = collected[-1]
    assert last["complete"] is True and last["present"] == 7
    assert "buy_eligibility" in last["results"]
    assert "sell_eligibility" in last["results"]
    assert "wait_state" in last["results"]
    print("OK — السطح القديم للوحة يكتمل بوحدات الأهلية الجديدة")


async def test_health():
    print("\n--- test_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_buy_state(_eligibility("buy_eligibility", "eligible"))
    health = await atom.health_check()
    assert health.state == HealthState.HEALTHY and "records=1" in health.message
    print("OK — الصحة:", health.message)


async def main():
    tests = [test_record_unifies_three_states_and_resolution,
             test_q8_19_record_keeps_both_eligible_resolution_from_458,
             test_record_missing_decision_id_declared_not_invented,
             test_legacy_cycle_collection_still_complete, test_health]
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
