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

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom517", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom517"] = _mod
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
        return AtomContext(atom_id=517, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _account(equity):
    return {"account_id": "ACC", "broker": "BR", "equity": equity}


def _outcome(profit):
    return {"event_id": f"outcome:{profit}", "trade_id": f"trade:{profit}",
            "account_id": "ACC", "broker": "BR",
            "symbol": "NQ100", "profit": profit, "swap": 0.0,
            "commission": 0.0, "fee": 0.0}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    atom._test_tmp = tempfile.TemporaryDirectory()
    await atom.initialize(bus.make_context(
        {"consumer_db_path": os.path.join(atom._test_tmp.name, "consumer.db")}))
    await atom.start()
    return atom, bus


def _last(bus):
    return [p for n, p in bus.published if n == EVENT_OUT][-1]


async def test_loss_reported():
    print("\n--- test_loss_reported ---")
    atom, bus = await _new()
    await atom._on_truth_equity(_account(10000.0))
    await atom._on_outcome(_outcome(-100.0))
    last = _last(bus)
    assert last["is_loss"] is True
    assert last["loss_pct"] == 1.0, last["loss_pct"]  # -(-100/10000)*100
    print(f"OK — خسارة 100 من 10000 → loss_pct={last['loss_pct']} is_loss")


async def test_win_reported():
    print("\n--- test_win_reported ---")
    atom, bus = await _new()
    await atom._on_truth_equity(_account(10000.0))
    await atom._on_outcome(_outcome(50.0))
    last = _last(bus)
    assert last["is_loss"] is False
    assert last["loss_pct"] == -0.5, last["loss_pct"]
    print(f"OK — ربح 50 → loss_pct={last['loss_pct']} (سالب) is_loss=false")


async def test_no_trades_ready():
    print("\n--- test_no_trades_ready ---")
    bus = FakeEventBus()
    atom = Atom()
    atom._test_tmp = tempfile.TemporaryDirectory()
    await atom.initialize(bus.make_context(
        {"consumer_db_path": os.path.join(atom._test_tmp.name, "consumer.db")}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    await atom._on_truth_equity(_account(10000.0))
    h = await atom.health_check()
    assert h.state == HealthState.HEALTHY and h.message.startswith("READY_AWAITING_FIRST_TRADE_OUTCOME")
    print("OK — لا صفقات بعد → HEALTHY «جاهز — بانتظار أول نتيجة صفقة» (صدق: جاهزية لا عجز)")


async def test_identity_thread():
    """بند 22 حزمة ت (ت١): معرف القرار والدورة يمران من النتيجة إلى تقرير
    الخسارة، والغائب يمر None مع إنذار identity_incomplete — لا اختراع."""
    print("\n--- test_identity_thread ---")
    atom, bus = await _new()
    await atom._on_truth_equity(_account(10000.0))
    threaded = _outcome(-25.0)
    threaded.update({"decision_id": "D-3", "gate_request_id": "G-3"})
    await atom._on_outcome(threaded)
    last = _last(bus)
    assert last["decision_id"] == "D-3" and last["gate_request_id"] == "G-3", last
    assert "identity_warnings" not in last, "هوية كاملة بلا إنذار"
    await atom._on_outcome(_outcome(-30.0))
    last = _last(bus)
    assert last["decision_id"] is None and last["gate_request_id"] is None, last
    assert last["identity_missing"] == ["decision_id", "gate_request_id"]
    assert last["identity_warnings"] == ["identity_incomplete"]
    assert (await atom.health_check()).details["identity_incomplete"] >= 1
    print("OK — ت١: الهوية تمر لتقرير الخسارة، والغائب None + إنذار")


async def main():
    tests = [test_loss_reported, test_win_reported, test_no_trades_ready,
             test_identity_thread]
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
