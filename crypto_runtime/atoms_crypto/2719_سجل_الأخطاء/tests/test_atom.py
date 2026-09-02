import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
from core import logger as core_logger  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom719", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom719"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
_MARKER = _mod._MARKER


class _NullLogger:
    def debug(self, *a): pass
    def info(self, *a): pass
    def warning(self, *a): pass
    def error(self, *a): pass
    def critical(self, *a): pass


async def _noop_publish(name, payload):
    return None


def _make_context(cfg):
    return AtomContext(atom_id=719, config=cfg, logger=_NullLogger(),
                       publish=_noop_publish, subscribe=lambda n, h: None)


def _cfg(tmp, **over):
    cfg = {"dir": tmp, "file_prefix": "errors", "min_level": "WARNING",
           "include_root": True, "max_lines_per_day": 20000}
    cfg.update(over)
    return cfg


def _today_file(tmp):
    import time
    return Path(tmp) / ("errors-%s.log" % time.strftime("%Y%m%d"))


async def _start(cfg):
    atom = Atom()
    await atom.initialize(_make_context(cfg))
    await atom.start()
    return atom


async def test_captures_atom_error_with_traceback():
    print("\n--- test_captures_atom_error_with_traceback ---")
    tmp = tempfile.mkdtemp()
    atom = await _start(_cfg(tmp))
    try:
        log611 = core_logger.get_logger(611)
        log611.error("عطل تجريبي: قاعدة الجسر مقفولة")
        try:
            raise ValueError("قيمة فاسدة للاختبار")
        except ValueError:
            log611.error("فشل مع تتبّع", exc_info=True)
        text = _today_file(tmp).read_text(encoding="utf-8")
        assert "- ASMAR ERROR LOG -" in text.splitlines()[0], text.splitlines()[0]
        assert "| ERROR | ATOM 611 |" in text and "عطل تجريبي" in text, text
        assert "ValueError" in text and "    Traceback" in text, text
        print("OK — خطأ ذرّة انكتب سطرًا عربيًّا + تتبّع الاستثناء كامل")
    finally:
        await atom.stop()


async def test_root_capture_marked_external():
    print("\n--- test_root_capture_marked_external ---")
    tmp = tempfile.mkdtemp()
    atom = await _start(_cfg(tmp))
    try:
        logging.getLogger("lib_khariji_719").warning("تحذير من مكتبة خارجية")
        text = _today_file(tmp).read_text(encoding="utf-8")
        assert "| WARNING | EXTERNAL | lib_khariji_719 |" in text, text
        print("OK — تحذير خارج شجرة النواة (asyncio/مكتبات) يُلتقط ويُعلَّم «خارجي»")
    finally:
        await atom.stop()


async def test_daily_cap_suppresses_honestly():
    print("\n--- test_daily_cap_suppresses_honestly ---")
    tmp = tempfile.mkdtemp()
    atom = await _start(_cfg(tmp, max_lines_per_day=3))
    try:
        log = core_logger.get_logger(999)
        for i in range(5):
            log.error("فيضان %d", i)
        lines = _today_file(tmp).read_text(encoding="utf-8").splitlines()
        body = [l for l in lines if "فيضان" in l]
        cap = [l for l in lines if "ERROR_LOG_DAILY_CAP_REACHED" in l]
        assert len(body) == 3 and len(cap) == 1, lines
        h = await atom.health_check()
        assert h.details["suppressed_today"] == 2, h.details
        print("OK — السقف اليومي: 3 أسطر + سطر إعلان واحد، والمكبوت مُعَدّ (2)")
    finally:
        await atom.stop()


async def test_health_states_and_detach():
    print("\n--- test_health_states_and_detach ---")
    tmp = tempfile.mkdtemp()
    atom = Atom()
    await atom.initialize(_make_context(_cfg(tmp)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    h = await atom.health_check()
    assert h.state == HealthState.HEALTHY and "READY" in h.message and "written=0" in h.message, h
    core_logger.get_logger(42).error("خطأ للحالة")
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY and h2.details["written_today"] == 1, h2
    await atom.stop()
    for name in ("asmar.core", None):
        lg = logging.getLogger(name) if name else logging.getLogger()
        assert not any(getattr(x, _MARKER, False) for x in lg.handlers), lg.handlers
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    print("OK — الصحّة: UNHEALTHY → HEALTHY(جاهز) → today=1 → stop يفكّ اللاقط كاملًا")


async def test_hot_reload_no_duplicate_lines():
    print("\n--- test_hot_reload_no_duplicate_lines ---")
    tmp = tempfile.mkdtemp()
    old = await _start(_cfg(tmp))
    new = Atom()
    await new.initialize(_make_context(_cfg(tmp)))
    await new.start()  # يطهّر لاقط النسخة القديمة قبل الشبك
    try:
        core_logger.get_logger(7).error("سطر واحد لا اثنان")
        text = _today_file(tmp).read_text(encoding="utf-8")
        assert text.count("سطر واحد لا اثنان") == 1, text
        print("OK — تحميل حي فوق نسخة قديمة: اللاقط لا يتضاعف والسطر يُكتب مرّة")
    finally:
        await new.stop()
        await old.stop()


async def main():
    tests = [test_captures_atom_error_with_traceback, test_root_capture_marked_external,
             test_daily_cap_suppresses_honestly, test_health_states_and_detach,
             test_hot_reload_no_duplicate_lines]
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
