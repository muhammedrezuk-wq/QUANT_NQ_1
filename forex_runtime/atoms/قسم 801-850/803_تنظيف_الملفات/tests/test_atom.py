import asyncio
import inspect
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
    "_atom803", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom803"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_DAY = _mod.EVENT_DAY
EVENT_DONE = _mod.EVENT_DONE


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
        for h in self._handlers.get(name, []):
            r = h(payload)
            if inspect.isawaitable(r):
                await r

    def make_context(self, cfg):
        return AtomContext(atom_id=803, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _write(path, mtime):
    with open(path, "w") as f:
        f.write("x")
    os.utime(path, (mtime, mtime))


async def _make(bus, scan, patterns, older=1, interval=7):
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"scan_dirs": [scan], "older_than_days": older, "patterns": patterns,
         "interval_days": interval}))
    await atom.start()
    return atom


async def test_deletes_old_matching_only():
    print("\n--- test_deletes_old_matching_only ---")
    d = tempfile.mkdtemp()
    try:
        _write(os.path.join(d, "old.tmp"), 1_000_000.0)     # matches + old -> delete
        _write(os.path.join(d, "new.tmp"), 9_999_999.0)     # matches but new -> keep
        _write(os.path.join(d, "keep.db"), 1_000_000.0)     # old but no match -> keep
        bus = FakeEventBus()
        await _make(bus, d, ["*.tmp"], older=1)
        await bus.publish(EVENT_DAY, {"official_time": 10_000_000.0})
        assert not os.path.isfile(os.path.join(d, "old.tmp")), "old .tmp deleted"
        assert os.path.isfile(os.path.join(d, "new.tmp")), "new .tmp kept"
        assert os.path.isfile(os.path.join(d, "keep.db")), "non-matching kept"
        done = [p for n, p in bus.published if n == EVENT_DONE][-1]
        assert done["deleted_count"] == 1, done
        print("OK — حذف القديم المطابق فقط (new.tmp و keep.db باقيان)")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_no_patterns_deletes_nothing():
    print("\n--- test_no_patterns_deletes_nothing ---")
    d = tempfile.mkdtemp()
    try:
        _write(os.path.join(d, "old.tmp"), 1_000_000.0)
        bus = FakeEventBus()
        atom = await _make(bus, d, [], older=1)
        await bus.publish(EVENT_DAY, {"official_time": 10_000_000.0})
        assert os.path.isfile(os.path.join(d, "old.tmp")), "fail-safe: nothing deleted"
        done = [p for n, p in bus.published if n == EVENT_DONE][-1]
        assert done["deleted_count"] == 0 and done.get("reason"), done
        h = await atom.health_check()
        assert h.state == HealthState.DEGRADED, "no patterns -> DEGRADED"
        print("OK — بلا patterns: ما حذف شي (فشل آمن) + الصحة DEGRADED")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_interval_skip():
    print("\n--- test_interval_skip ---")
    d = tempfile.mkdtemp()
    try:
        _write(os.path.join(d, "a.tmp"), 1_000_000.0)
        bus = FakeEventBus()
        atom = await _make(bus, d, ["*.tmp"], older=1, interval=7)
        await bus.publish(EVENT_DAY, {"official_time": 10_000_000.0})
        r1 = atom.run_count
        await bus.publish(EVENT_DAY, {"official_time": 10_050_000.0})  # < 7d
        assert atom.run_count == r1, "within interval skipped"
        print("OK — الفاصل الزمني محسوب من official_time")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_health_states():
    print("\n--- test_health_states ---")
    d = tempfile.mkdtemp()
    try:
        _write(os.path.join(d, "a.tmp"), 1_000_000.0)
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(
            {"scan_dirs": [d], "older_than_days": 1, "patterns": ["*.tmp"],
             "interval_days": 7}))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        await atom.start()
        assert (await atom.health_check()).state == HealthState.DEGRADED
        await bus.publish(EVENT_DAY, {"official_time": 10_000_000.0})
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> DEGRADED -> HEALTHY")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def main():
    tests = [test_deletes_old_matching_only, test_no_patterns_deletes_nothing,
             test_interval_skip, test_health_states]
    failed = []
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            failed.append((t.__name__, str(e))); print(f"FAILED: {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e))); print(f"ERROR: {t.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}"); sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
