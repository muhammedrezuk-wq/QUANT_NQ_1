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
    "_atom800", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom800"] = _mod
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
        return AtomContext(atom_id=800, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _dirs():
    root = tempfile.mkdtemp()
    src = os.path.join(root, "store")
    bak = os.path.join(root, "backups")
    os.makedirs(src)
    with open(os.path.join(src, "data.txt"), "w") as f:
        f.write("hello")
    return root, src, bak


async def _make(bus, src, bak, keep=7, interval=1):
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"backup_dir": bak, "source_dirs": [src], "keep_last_n": keep,
         "interval_days": interval}))
    await atom.start()
    return atom


def _backups(bak):
    if not os.path.isdir(bak):
        return []
    return sorted(f for f in os.listdir(bak)
                  if f.startswith("backup_") and f.endswith(".tar.gz"))


async def test_backup_on_day():
    print("\n--- test_backup_on_day ---")
    root, src, bak = _dirs()
    try:
        bus = FakeEventBus()
        await _make(bus, src, bak)
        await bus.publish(EVENT_DAY, {"official_time": 100000.0})
        bks = _backups(bak)
        assert bks == ["backup_100000.tar.gz"], bks
        with tarfile.open(os.path.join(bak, bks[0]), "r:gz") as tar:
            names = tar.getnames()
        assert any(n.endswith("data.txt") for n in names), names
        done = [p for n, p in bus.published if n == EVENT_DONE][-1]
        assert done["file_count"] == 1 and done["timestamp"] == 100000.0, done
        print(f"OK — نسخ احتياطي على SYS_DAY (بلا time.time): {bks[0]} · محتوى={names}")
    finally:
        __import__("shutil").rmtree(root, ignore_errors=True)


async def test_interval_skip():
    print("\n--- test_interval_skip ---")
    root, src, bak = _dirs()
    try:
        bus = FakeEventBus()
        await _make(bus, src, bak, interval=1)
        await bus.publish(EVENT_DAY, {"official_time": 100000.0})
        await bus.publish(EVENT_DAY, {"official_time": 130000.0})  # +30000 < 86400
        assert _backups(bak) == ["backup_100000.tar.gz"], "second within interval skipped"
        await bus.publish(EVENT_DAY, {"official_time": 200000.0})  # +100000 >= 86400
        assert len(_backups(bak)) == 2, "third after interval runs"
        print("OK — الفاصل الزمني محسوب من official_time (لا يعيد ضمن اليوم)")
    finally:
        __import__("shutil").rmtree(root, ignore_errors=True)


async def test_retention():
    print("\n--- test_retention ---")
    root, src, bak = _dirs()
    try:
        bus = FakeEventBus()
        await _make(bus, src, bak, keep=2, interval=1)
        for t in (100000.0, 200000.0, 300000.0):
            await bus.publish(EVENT_DAY, {"official_time": t})
        bks = _backups(bak)
        assert bks == ["backup_200000.tar.gz", "backup_300000.tar.gz"], bks
        print(f"OK — احتفاظ بآخر N نسخة فقط (keep=2): {bks}")
    finally:
        __import__("shutil").rmtree(root, ignore_errors=True)


async def test_health_states():
    print("\n--- test_health_states ---")
    root, src, bak = _dirs()
    try:
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(
            {"backup_dir": bak, "source_dirs": [src], "keep_last_n": 7,
             "interval_days": 1}))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        await atom.start()
        # قاعدة الصدق (أمر المالك ٢٠٢٦-٠٨-١٩): «لم تعمل بعد» مع جدولة مسلّحة
        # وبلا خطأ = جاهزية سليمة برسالة صادقة، لا تعثّر.
        ready = await atom.health_check()
        assert ready.state == HealthState.HEALTHY and "READY" in ready.message, ready.message
        await bus.publish(EVENT_DAY, {"official_time": 100000.0})
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> HEALTHY(جاهز) -> HEALTHY(backups)")
    finally:
        __import__("shutil").rmtree(root, ignore_errors=True)


async def main():
    tests = [test_backup_on_day, test_interval_skip, test_retention,
             test_health_states]
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
