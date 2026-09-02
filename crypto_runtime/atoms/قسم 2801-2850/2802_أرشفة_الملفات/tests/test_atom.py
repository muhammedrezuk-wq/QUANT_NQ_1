import asyncio
import inspect
import os
import sys
import tarfile
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom802", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom802"] = _mod
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
        return AtomContext(atom_id=802, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _write(path, mtime):
    with open(path, "w") as f:
        f.write("x" * 100)
    os.utime(path, (mtime, mtime))


def _dirs():
    root = tempfile.mkdtemp()
    src = os.path.join(root, "journal")
    arch = os.path.join(root, "file_archive")
    os.makedirs(src)
    return root, src, arch


async def _make(bus, src, arch, older=1, interval=7):
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"archive_dir": arch, "source_dirs": [src], "older_than_days": older,
         "interval_days": interval}))
    await atom.start()
    return atom


async def test_archives_old_files_only():
    print("\n--- test_archives_old_files_only ---")
    root, src, arch = _dirs()
    try:
        _write(os.path.join(src, "old.log"), 1_000_000.0)
        _write(os.path.join(src, "new.log"), 9_999_999.0)
        bus = FakeEventBus()
        await _make(bus, src, arch, older=1)
        # cutoff = 10_000_000 - 86400 = 9_913_600 -> old(<) archived, new(>) kept
        await bus.publish(EVENT_DAY, {"official_time": 10_000_000.0})
        assert not os.path.isfile(os.path.join(src, "old.log")), "old removed"
        assert os.path.isfile(os.path.join(src, "new.log")), "new kept"
        done = [p for n, p in bus.published if n == EVENT_DONE][-1]
        assert done["files_archived"] == 1, done
        arch_files = os.listdir(arch)
        with tarfile.open(os.path.join(arch, arch_files[0]), "r:gz") as tar:
            names = tar.getnames()
        assert any(n.endswith("old.log") for n in names), names
        print(f"OK — أرشف الملفات القديمة فقط (بلا time.time): {names}")
    finally:
        __import__("shutil").rmtree(root, ignore_errors=True)


async def test_no_old_files():
    print("\n--- test_no_old_files ---")
    root, src, arch = _dirs()
    try:
        _write(os.path.join(src, "new.log"), 9_999_999.0)
        bus = FakeEventBus()
        await _make(bus, src, arch, older=1)
        await bus.publish(EVENT_DAY, {"official_time": 10_000_000.0})
        done = [p for n, p in bus.published if n == EVENT_DONE][-1]
        assert done["files_archived"] == 0, done
        assert os.path.isfile(os.path.join(src, "new.log"))
        print("OK — ما في ملفات قديمة: files_archived=0 (الحديث باقٍ)")
    finally:
        __import__("shutil").rmtree(root, ignore_errors=True)


async def test_accepts_relative_journal_file_source():
    root = tempfile.mkdtemp(); journal = os.path.join(root, "journal.jsonl")
    arch = os.path.join(root, "file_archive")
    try:
        _write(journal, 1_000_000.0)
        bus = FakeEventBus(); await _make(bus, journal, arch, older=1, interval=1)
        await bus.publish(EVENT_DAY, {"official_time": 10_000_000.0})
        done = [p for n,p in bus.published if n == EVENT_DONE][-1]
        assert done["files_archived"] == 1 and not os.path.exists(journal)
        with tarfile.open(done["archive_path"], "r:gz") as tar:
            assert "journal.jsonl" in tar.getnames()
    finally:
        __import__("shutil").rmtree(root, ignore_errors=True)


async def test_absolute_bridge_dir_space_in_path_live_file_kept():
    print("\n--- test_absolute_bridge_dir_space_in_path_live_file_kept ---")
    root = tempfile.mkdtemp()
    src = os.path.join(root, "User Files", "asmar_bridge")
    arch = os.path.join(root, "file_archive")
    os.makedirs(src)
    try:
        _write(os.path.join(src, "ctrader_bridge.jsonl.20260815_175725"), 1_000_000.0)
        _write(os.path.join(src, "ctrader_bridge.jsonl"), 9_999_999.0)
        bus = FakeEventBus()
        await _make(bus, src, arch, older=1)
        await bus.publish(EVENT_DAY, {"official_time": 10_000_000.0})
        assert not os.path.isfile(
            os.path.join(src, "ctrader_bridge.jsonl.20260815_175725")), "rotated archived"
        assert os.path.isfile(os.path.join(src, "ctrader_bridge.jsonl")), "live kept"
        done = [p for n, p in bus.published if n == EVENT_DONE][-1]
        assert done["files_archived"] == 1, done
        with tarfile.open(done["archive_path"], "r:gz") as tar:
            names = tar.getnames()
        assert names == ["asmar_bridge/ctrader_bridge.jsonl.20260815_175725"], names
        print("OK — مجلد مطلق وفيه مسافة: المدوَّر أُرشف والملف الحي بقي")
    finally:
        __import__("shutil").rmtree(root, ignore_errors=True)


async def test_interval_skip():
    print("\n--- test_interval_skip ---")
    root, src, arch = _dirs()
    try:
        _write(os.path.join(src, "a.log"), 1_000_000.0)
        bus = FakeEventBus()
        atom = await _make(bus, src, arch, older=1, interval=7)
        await bus.publish(EVENT_DAY, {"official_time": 10_000_000.0})
        r1 = atom.run_count
        await bus.publish(EVENT_DAY, {"official_time": 10_050_000.0})  # +50000 < 7d
        assert atom.run_count == r1, "within interval skipped"
        print("OK — الفاصل الأسبوعي محسوب من official_time")
    finally:
        __import__("shutil").rmtree(root, ignore_errors=True)


async def test_health_states():
    print("\n--- test_health_states ---")
    root, src, arch = _dirs()
    try:
        _write(os.path.join(src, "old.log"), 1_000_000.0)
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(
            {"archive_dir": arch, "source_dirs": [src], "older_than_days": 1,
             "interval_days": 7}))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        await atom.start()
        assert (await atom.health_check()).state == HealthState.DEGRADED
        await bus.publish(EVENT_DAY, {"official_time": 10_000_000.0})
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> DEGRADED -> HEALTHY")
    finally:
        __import__("shutil").rmtree(root, ignore_errors=True)


async def main():
    tests = [test_archives_old_files_only, test_no_old_files,
             test_accepts_relative_journal_file_source,
             test_absolute_bridge_dir_space_in_path_live_file_kept,
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
