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
    "_atom468", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom468"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom

CFG = {"allowed_symbols": ["XAUUSD", "USTEC"]}


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
        return AtomContext(atom_id=468, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tick(symbol):
    return {"symbol": symbol, "account_id": "A", "broker": "BR", "sequence": "1"}


def _filters(bus):
    return [p for n, p in bus.published if n == "decision.filter.asset.state"]


def _whitelists(bus):
    return [p for n, p in bus.published if n == "allowed.symbols.state"]


async def _new(cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(cfg if cfg is not None else CFG)))
    await atom.start()
    return atom, bus


async def test_allowed_symbol_passes():
    print("\n--- test_allowed_symbol_passes ---")
    atom, bus = await _new()
    await atom._on_tick(_tick("XAUUSD"))
    f = _filters(bus)[-1]
    assert f["symbol"] == "XAUUSD" and f["metadata"]["passed"] is True
    print("OK — رمز مسموح (XAUUSD) → passed=True")


async def test_blocked_symbol_fails():
    print("\n--- test_blocked_symbol_fails ---")
    atom, bus = await _new()
    await atom._on_tick(_tick("BTCUSD"))
    f = _filters(bus)[-1]
    assert f["symbol"] == "BTCUSD" and f["metadata"]["passed"] is False
    print("OK — رمز ممنوع (BTCUSD) → passed=False")


async def test_whitelist_published_on_start():
    print("\n--- test_whitelist_published_on_start ---")
    _atom, bus = await _new()
    w = _whitelists(bus)
    assert w and w[-1]["allowed"] == ["USTEC", "XAUUSD"]
    print("OK — القائمة البيضاء تُنشَر عند البدء (مرتّبة)")


async def test_empty_whitelist_blocks_all():
    print("\n--- test_empty_whitelist_blocks_all ---")
    atom, bus = await _new({"allowed_symbols": []})
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_tick(_tick("XAUUSD"))
    assert _filters(bus)[-1]["metadata"]["passed"] is False
    print("OK — قائمة فاضية → DEGRADED + يمنع الكلّ (fail-closed)")


async def test_unknown_command_does_not_partition_allowlist():
    print("\n--- test_unknown_command_does_not_partition_allowlist ---")
    atom, bus = await _new()
    await atom._on_asset_command({"symbol": "XAUUSD", "account_id": "A",
                                   "command": "BOGUS_TYPO"})
    assert "A" not in atom._allowed_by_account, (
        "أمر غير معروف أنشأ تقسيماً بالحساب رغم أنه لا يفعل شيئاً: %r"
        % atom._allowed_by_account)
    print("OK — أمر غير معروف لا يُنشئ تقسيماً بالحساب")


async def test_noop_recognized_command_does_not_partition_allowlist():
    print("\n--- test_noop_recognized_command_does_not_partition_allowlist ---")
    atom, bus = await _new()
    # PAUSE على رمز غائب أصلاً (BTCUSD ليس بالقائمة CFG) -- لا شيء يتغيّر.
    await atom._on_asset_command({"symbol": "BTCUSD", "account_id": "A",
                                   "command": "PAUSE"})
    assert "A" not in atom._allowed_by_account, (
        "أمر معروف بلا أثر فعلي أنشأ تقسيماً بالحساب: %r"
        % atom._allowed_by_account)
    print("OK — أمر معروف بلا أثر فعلي لا يُنشئ تقسيماً بالحساب")


async def test_unknown_command_does_not_desync_account_from_later_global_activation():
    print("\n--- test_unknown_command_does_not_desync_account_from_later_global_activation ---")
    atom, bus = await _new()
    # أمر غير معروف يصل بحدث مشترك -- قبل الإصلاح كان هذا وحده كافياً
    # لتجميد لقطة القائمة لهذا الحساب.
    await atom._on_asset_command({"symbol": "XAUUSD", "account_id": "A",
                                   "command": "BOGUS_TYPO"})
    # تفعيل عام (بلا account_id) يضيف رمزاً جديداً للقائمة العامة.
    await atom._on_activate({"symbol": "NEWSYM"})
    # الحساب A يجب أن يرى الرمز الجديد فوراً -- لا انشقاق صامت.
    await atom._on_tick(_tick("NEWSYM"))
    f = _filters(bus)[-1]
    assert f["symbol"] == "NEWSYM" and f["metadata"]["passed"] is True, (
        "الحساب A انشقّ صامتاً عن القائمة العامة بعد أمر غير معروف: %s" % f)
    print("OK — لا انشقاق صامت: الحساب يتبع القائمة العامة الحيّة بعد أمر غير معروف")


async def test_health_healthy():
    print("\n--- test_health_healthy ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    await atom._on_tick(_tick("XAUUSD"))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→HEALTHY")


async def main():
    tests = [test_allowed_symbol_passes, test_blocked_symbol_fails,
             test_whitelist_published_on_start, test_empty_whitelist_blocks_all,
             test_unknown_command_does_not_partition_allowlist,
             test_noop_recognized_command_does_not_partition_allowlist,
             test_unknown_command_does_not_desync_account_from_later_global_activation,
             test_health_healthy]
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
