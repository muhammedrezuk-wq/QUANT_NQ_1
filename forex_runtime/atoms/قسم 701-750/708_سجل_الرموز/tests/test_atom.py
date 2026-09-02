import asyncio
import inspect
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
    "_atom708", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom708"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_REQ = _mod.EVENT_RESOLVE_REQUESTED
EVENT_RESOLVED = _mod.EVENT_RESOLVED
EVENT_UNMAPPED = _mod.EVENT_UNMAPPED
EVENT_MAP = _mod.EVENT_MAP
EVENT_RESOLVED_NEW = _mod.EVENT_RESOLVED_NEW

_CFG = {
    "canonical_map": {"NQ100": ["USTEC", "NAS100", "US100"], "XAUUSD": ["GOLD"]},
    "canonical_patterns": {"NQ100": r"(NAS|USTEC|US100|NDX)\d*"},
    "min_stem_length": 4,
    "strip_suffixes": [".pro", "_i", "m"],
    "passthrough_unknown": True,
}


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
        return AtomContext(atom_id=708, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _make(bus, cfg=None):
    atom = Atom()
    await atom.initialize(bus.make_context(dict(cfg or _CFG)))
    await atom.start()
    return atom


async def test_broadcasts_map_state_on_start():
    print("\n--- test_broadcasts_map_state_on_start ---")
    bus = FakeEventBus()
    await _make(bus)
    maps = [p for n, p in bus.published if n == EVENT_MAP]
    assert len(maps) == 1 and maps[0]["aliases"]["USTEC"] == "NQ100", maps
    assert EVENT_MAP.endswith(".state"), "map must be a replayable .state event"
    print(f"OK — بثّ خريطة الرموز كحدث .state (يعيده الناقل للمتأخّر): {EVENT_MAP}")


async def test_resolves_aliases():
    print("\n--- test_resolves_aliases ---")
    bus = FakeEventBus()
    atom = await _make(bus)
    assert atom.resolve("USTEC") == "NQ100"
    assert atom.resolve("NAS100") == "NQ100"
    assert atom.resolve("GOLD") == "NQ100" or atom.resolve("GOLD") == "XAUUSD"
    assert atom.resolve("XAUUSDm") == "XAUUSD", "strips lowercase micro suffix m"
    print("OK — حلّ الأسماء المختلفة لرمز موحّد (USTEC/NAS100→NQ100)")


async def test_pattern_match():
    print("\n--- test_pattern_match ---")
    bus = FakeEventBus()
    atom = await _make(bus)
    assert atom.resolve("USTEC100") == "NQ100", "pattern (NAS|USTEC...)\\d*"
    print("OK — حلّ عبر النمط (regex) لمّا ما في تطابق مباشر")


async def test_does_not_overstrip():
    print("\n--- test_does_not_overstrip ---")
    bus = FakeEventBus()
    atom = await _make(bus)
    # 'm' suffix must NOT strip PLATINUM -> PLATINU (uppercase tail = part of name)
    assert atom.resolve("PLATINUM") == "PLATINUM", atom.resolve("PLATINUM")
    print("OK — ما يبتر حرفًا من اسم حقيقي (PLATINUM يظلّ PLATINUM)")


async def test_resolve_request_response():
    print("\n--- test_resolve_request_response ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish(EVENT_REQ, {"request_id": "r1", "account_id": "A1",
                                  "symbol": "USTEC", "timestamp": 4.0})
    resolved = [p for n, p in bus.published if n == EVENT_RESOLVED]
    assert resolved and resolved[-1]["canonical"] == "NQ100"
    assert resolved[-1]["request_id"] == "r1" and resolved[-1]["account_id"] == "A1"
    assert resolved[-1]["timestamp"] == 4.0, "echoes request time (Rule 13)"
    print(f"OK — ردّ على طلب الحلّ مع request_id/account_id: {resolved[-1]['canonical']}")


async def test_new_resolution_matches_canonical_alias_to_executable_spec():
    print("\n--- test_new_resolution_matches_canonical_alias_to_executable_spec ---")
    bus = FakeEventBus()
    atom = await _make(bus)
    await atom._on_specs({"account_id": "A1", "symbols": [
        {"account_id": "A1", "symbol": "USTEC", "tick_value": 1.0,
         "tick_size": 0.1, "contract_size": 1.0}
    ]})
    await atom._on_resolve_new({
        "request_id": "new-1", "account_id": "A1", "logical_symbol": "NQ100"
    })
    rows = [p for n, p in bus.published if n == EVENT_RESOLVED_NEW]
    assert rows and rows[-1]["approved"] is True
    assert rows[-1]["broker_symbol"] == "USTEC"
    print("OK — NQ100 يُحل إلى مواصفة USTEC التنفيذية")


async def test_unmapped_emits_and_degrades():
    print("\n--- test_unmapped_emits_and_degrades ---")
    bus = FakeEventBus()
    atom = await _make(bus)
    await bus.publish(EVENT_REQ, {"symbol": "WEIRDCOIN"})
    unmapped = [p for n, p in bus.published if n == EVENT_UNMAPPED]
    assert unmapped and unmapped[-1]["symbol"] == "WEIRDCOIN"
    h = await atom.health_check()
    assert h.state == HealthState.DEGRADED, "unmapped symbol -> DEGRADED"
    print("OK — رمز غير معروف: ينشر unmapped + الصحة DEGRADED (تحذير لا عطل)")


async def main():
    tests = [test_broadcasts_map_state_on_start, test_resolves_aliases,
             test_pattern_match, test_does_not_overstrip,
             test_resolve_request_response,
             test_new_resolution_matches_canonical_alias_to_executable_spec,
             test_unmapped_emits_and_degrades]
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
