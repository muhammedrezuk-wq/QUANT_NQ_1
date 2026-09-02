import asyncio
import inspect
import gzip
import os
import sys
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
    "_atom715", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom715"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_IN = _mod.EVENT_IN
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
        for h in self._handlers.get(name, []):
            r = h(payload)
            if inspect.isawaitable(r):
                await r

    def make_context(self, cfg):
        return AtomContext(atom_id=715, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _make(bus, min_size=0, level=6, keep=True):
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"min_size_bytes": min_size, "compression_level": level,
         "keep_original": keep}))
    await atom.start()
    return atom


async def test_compresses_archive():
    print("\n--- test_compresses_archive ---")
    d = tempfile.mkdtemp()
    src = os.path.join(d, "archive.db")
    try:
        with open(src, "wb") as f:
            f.write(b"HELLO-ARCHIVE-DATA" * 1000)
        bus = FakeEventBus()
        await _make(bus, min_size=0, keep=True)
        await bus.publish(EVENT_IN, {"rows": 5, "archive_path": src, "timestamp": 9.0})
        gz = src + ".gz"
        assert os.path.isfile(gz), "gz created"
        assert os.path.isfile(src), "original kept (keep_original=true)"
        with gzip.open(gz, "rb") as f:
            assert f.read().startswith(b"HELLO-ARCHIVE-DATA")
        out = [p for n, p in bus.published if n == EVENT_OUT][-1]
        assert out["compressed"] and out["timestamp"] == 9.0, out
        print(f"OK — ضغط الأرشيف → .gz (وفّر {out['saved_bytes']} بايت، أبقى الأصل)")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_skips_when_no_rows():
    print("\n--- test_skips_when_no_rows ---")
    d = tempfile.mkdtemp()
    src = os.path.join(d, "archive.db")
    try:
        with open(src, "wb") as f:
            f.write(b"x" * 1000)
        bus = FakeEventBus()
        await _make(bus, min_size=0)
        await bus.publish(EVENT_IN, {"rows": 0, "archive_path": src})
        assert not os.path.isfile(src + ".gz"), "no compression when nothing archived"
        print("OK — ما ضغط لمّا ما في صفوف مؤرشفة (rows=0)")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_below_min_size():
    print("\n--- test_below_min_size ---")
    d = tempfile.mkdtemp()
    src = os.path.join(d, "archive.db")
    try:
        with open(src, "wb") as f:
            f.write(b"tiny")
        bus = FakeEventBus()
        await _make(bus, min_size=1000)
        await bus.publish(EVENT_IN, {"rows": 1, "archive_path": src})
        out = [p for n, p in bus.published if n == EVENT_OUT][-1]
        assert out["compressed"] is False and out["reason"] == "BELOW_MIN_SIZE", out
        print("OK — تخطّى الملف الأصغر من الحدّ الأدنى")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_delete_original_when_configured():
    print("\n--- test_delete_original_when_configured ---")
    d = tempfile.mkdtemp()
    src = os.path.join(d, "archive.db")
    try:
        with open(src, "wb") as f:
            f.write(b"data" * 1000)
        bus = FakeEventBus()
        await _make(bus, min_size=0, keep=False)
        await bus.publish(EVENT_IN, {"rows": 1, "archive_path": src})
        assert os.path.isfile(src + ".gz") and not os.path.isfile(src)
        print("OK — keep_original=false: حذف الأصل بعد الضغط")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_health_states():
    print("\n--- test_health_states ---")
    d = tempfile.mkdtemp()
    src = os.path.join(d, "archive.db")
    try:
        with open(src, "wb") as f:
            f.write(b"data" * 1000)
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(
            {"min_size_bytes": 0, "compression_level": 6, "keep_original": True}))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        await atom.start()
        h = await atom.health_check()
        assert h.state == HealthState.HEALTHY, (h.state, h.message)
        assert "READY" in (h.message or "") and "runs=0" in (h.message or ""), h.message
        await bus.publish(EVENT_IN, {"rows": 1, "archive_path": src})
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> HEALTHY(جاهز بانتظار أول عمل) -> HEALTHY")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def main():
    tests = [test_compresses_archive, test_skips_when_no_rows, test_below_min_size,
             test_delete_original_when_configured, test_health_states]
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
