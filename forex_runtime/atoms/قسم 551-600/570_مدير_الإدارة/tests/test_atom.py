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
    "_atom570", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom570"] = _mod
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
        return AtomContext(atom_id=570, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _sl(stop, side="BUY", ticket=7, reason="breakeven"):
    return {"account_id": "A", "ticket": ticket, "symbol": "NQ100", "side": side,
            "action": "MODIFY_SL", "stop_loss": stop, "reason": reason}


def _partial(volume, ticket=7):
    return {"account_id": "A", "ticket": ticket, "symbol": "NQ100", "side": "BUY",
            "action": "CLOSE_PARTIAL", "volume": volume, "reason": "partial_take"}


def _close(ticket=7):
    return {"account_id": "A", "ticket": ticket, "symbol": "NQ100", "side": "BUY", "action": "CLOSE",
            "reason": "exit"}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    await atom._on_pulse({"official_time": 1000.0})
    return atom, bus


def _outs(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]

async def _commit(atom, bus):
    command = _outs(bus)[-1]
    await atom._on_written(command)
    await atom._on_ack(command)


async def test_tighter_sl_wins():
    print("\n--- test_tighter_sl_wins ---")
    atom, bus = await _new()
    await atom._on_intent(_sl(100.0))      # breakeven
    await _commit(atom, bus)
    await atom._on_intent(_sl(100.5))
    await _commit(atom, bus)      # trailing tighter -> pass
    await atom._on_intent(_sl(100.2))      # looser -> dropped
    outs = _outs(bus)
    assert len(outs) == 2, len(outs)
    assert outs[-1]["stop_loss"] == 100.5
    print("OK — الأضيق يفوز (100→100.5) · الأرخى مرفوض")


async def test_partial_once():
    print("\n--- test_partial_once ---")
    atom, bus = await _new()
    await atom._on_intent(_partial(0.1))
    await _commit(atom, bus)
    await atom._on_intent(_partial(0.1))
    assert len(_outs(bus)) == 1, "جزئيّ مرّة واحدة"
    print("OK — جزئيّ مرّة")


async def test_close_locks():
    print("\n--- test_close_locks ---")
    atom, bus = await _new()
    await atom._on_intent(_close())
    await _commit(atom, bus)
    await atom._on_intent(_sl(100.5))  # after close -> ignored
    outs = _outs(bus)
    assert len(outs) == 1 and outs[0]["action"] == "CLOSE"
    print("OK — CLOSE يقفل ما بعده")


async def test_vanish_clears():
    print("\n--- test_vanish_clears ---")
    atom, bus = await _new()
    await atom._on_intent(_sl(100.0))
    await atom._on_vanish({"account_id": "A", "ticket": 7, "symbol": "NQ100"})
    assert "7" not in atom._state
    await atom._on_intent(_sl(99.5))  # fresh ticket state -> passes as first
    assert _outs(bus)[-1]["stop_loss"] == 99.5
    print("OK — الاختفاء يُصفّر الحالة")


async def test_health():
    print("\n--- test_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    ready = await atom.health_check()
    assert ready.state == HealthState.HEALTHY and ready.message.startswith("READY")
    await atom._on_intent(_sl(100.0))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة تتدرّج UNHEALTHY→HEALTHY(جاهز، صفر نيّة)→HEALTHY(يعمل)")


async def main():
    tests = [test_tighter_sl_wins, test_partial_once, test_close_locks,
             test_vanish_clears, test_health]
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
