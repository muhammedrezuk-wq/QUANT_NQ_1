"""Contract guard for problem 54 — the owner's protection state must survive a
HOT RELOAD, not only a restart.

Owner's ruling 2026-08-14, scope (a), verbatim:

    "552 · 550 · 519 only. 552 proved live that HALTED is lost on an upgrade;
     550 and 519 are the same class of owner-intent / sticky protection.
     The Event Bus mechanism already exists and replays the last *.state, so we
     do NOT touch event_bus.py, the seal, or add a second snapshot path.
     516 is not included now: it owns no replayable *.state output.
     Do not widen 54 to everything 5-3..5-5 covered -- that chain handles the
     RESTART; 54 handles the HOT RELOAD."
    "What you proposed is not a second snapshot. It is re-receiving the last
     published state through the bus -- and that is better than widening 5-3 IF
     the evidence really proves the published state is the authoritative source
     itself."

Why a hot reload loses it, measured: `hot_reload_service` unloads and rebuilds
the instance without snapshot/restore, and the state in question is built from
events the bus never replays -- `_is_replayable` demands a `.state`/`.synced`/
`.snapshot` suffix, and `emergency.halt` has none. Meanwhile 519 DID come back
frozen after its own upgrade, because it listens to `risk.asset_ledger.state`,
which IS replayable. The mechanism works; nobody uses it on their own output.

  A) الآليّة   -- proven on the REAL EventBus, not a stand-in: the three output
     events are replayable, and `emergency.halt` is not.
  B) الترقية   -- state built -> published -> a NEW instance is created exactly
     as the hot loader does -> the last *.state must reach it -> the semantic
     state must match, field by field.
  C) لا حلقة   -- the rehydrated instance must not answer its own output
     forever: publications are counted and must settle.
  D) fail-closed -- a corrupt/incomplete published state must leave the
     protection ON, never open it.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.contracts.atom import AtomContext  # noqa: E402
from core.event_bus import EventBus, _is_replayable  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
ACC = "52992818"

SPECS = {
    552: {"folder": "552_مدقق_الأمر", "out": "execution.gate.state"},
    550: {"folder": "550_مدير_التنفيذ", "out": "execution.unified.state"},
    519: {"folder": "519_محفظة_الأصل", "out": "asset.portfolio.owner_intent.state"},
}
# Rebased 2026-08-15: items 56/70 moved 516 and item 69 moved 611, both by the
# owner's order. The barrier's meaning is unchanged; only its baseline moves.
UNTOUCHED = {"516_قاطع_الأمان": "5.2.0", "611_قارئ_الصفقات": "4.0.2"}


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


def load(folder: str):
    directory = ATOMS / folder
    spec = importlib.util.spec_from_file_location("_c54_" + folder.split("_")[0],
                                                  directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def manifest(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


async def spawn(module, atom_id: int, folder: str, bus: EventBus, tag: str,
               *, start: bool = True):
    """Instantiate and initialize; optionally start after replay has drained."""
    atom = module.Atom()
    await atom.initialize(AtomContext(
        atom_id=atom_id, config=dict(manifest(folder).get("config") or {}),
        logger=_Logger(),
        publish=lambda name, payload: bus.publish(name, payload, publisher=tag),
        subscribe=lambda name, handler: bus.subscribe(name, handler, subscriber=tag)))
    if start:
        await atom.start()
    return atom


def semantics(atom_id: int, atom) -> dict:
    """Read the current scoped state contract, not retired field names."""
    if atom_id == 552:
        return {"halted": bool(atom._global_halted),
                "halted_accounts": dict(atom._halted_accounts),
                "restore_error": atom._restore_error}
    if atom_id == 550:
        return {"halted_accounts": dict(atom._halted_accounts),
                "restore_error": atom._restore_error}
    return {"halted_accounts": sorted(atom._halted_accounts),
            "paused": sorted(atom._paused),
            "states": {k: v for k, v in sorted(atom._states.items())},
            "restore_error": atom._restore_error}


async def build_state(atom_id: int, atom, bus: EventBus) -> None:
    """Give the atom the owner's intent, exactly through its real inputs."""
    if atom_id == 552:
        await atom._on_halt({"scope": "SYSTEM", "reason": "OWNER"})
        return
    if atom_id == 550:
        await atom._on_halt({"account_id": ACC, "reason": "OWNER"})
        return
    await atom._on_account({"account_id": ACC, "broker": "TEST", "margin_mode": 2, "equity": 1000.0})
    await atom._on_command({"account_id": ACC, "broker": "TEST", "symbol": "PAUSED_ONE", "command": "pause"})
    await atom._on_command({"account_id": ACC, "symbol": "RELEASED_ONE", "command": "release"})
    await atom._on_ledger({"ledgers": [{"account_id": ACC, "symbol": "NORMAL_ONE",
                                        "u": 0.0, "v_net": 0.0, "budgeted": True,
                                        "risk_budget": 100.0}], "count": 1})


def structural() -> int:
    print("=" * 78)
    print("أ) الآليّة — بالناقل الحقيقيّ لا ببديل")
    print("=" * 78)
    bad = 0
    for atom_id, s in SPECS.items():
        ok = _is_replayable(s["out"])
        bad += 0 if ok else 1
        print("  %-4s مخرَجه `%-28s` قابل للإعادة  %s" % (atom_id, s["out"],
                                                          "✓" if ok else "✗"))
    ok = not _is_replayable("emergency.halt")
    bad += 0 if ok else 1
    print("  %-4s `emergency.halt` غير قابل للإعادة (سبب الضياع)   %s" % ("", "✓" if ok else "✗"))

    print("  والعقد بالكود:")
    for atom_id, s in SPECS.items():
        src = (ATOMS / s["folder"] / "atom.py").read_text(encoding="utf-8")
        subs = manifest(s["folder"]).get("subscribes") or []
        for label, ok in ((f"{atom_id} يعلن مخرَجه مدخلًا", s["out"] in subs),
                          (f"{atom_id} يرمّم منه", "_rehydrate" in src),
                          (f"{atom_id} يمنع الحلقة", "_rehydrated" in src)):
            bad += 0 if ok else 1
            print("      %-34s %s" % (label, "✓" if ok else "✗"))
    print("  المستثنى من اللمس بأمره:")
    for folder, version in UNTOUCHED.items():
        got = str(manifest(folder).get("version"))
        ok = got == version
        bad += 0 if ok else 1
        print("      %-30s %-8s %s" % (folder.split("_")[0], got, "✓" if ok else "✗ تغيّرت!"))
    return bad


async def one(atom_id: int) -> int:
    s = SPECS[atom_id]
    module = load(s["folder"])
    bus = EventBus()
    bad = 0
    print("\n" + "-" * 78)
    print("الذرّة %d — %s" % (atom_id, s["out"]))
    print("-" * 78)

    old = await spawn(module, atom_id, s["folder"], bus, "old")
    await build_state(atom_id, old, bus)
    for _ in range(6):
        await asyncio.sleep(0)
    before = semantics(atom_id, old)
    published = bus.stats().get("published", {}).get(s["out"], 0) if hasattr(bus, "stats") else None
    print("  ١· الحالة قبل الترقية : %s" % before)
    ok = any(before.values())
    bad += 0 if ok else 1
    print("  ٢· نُشرت على الناقل   : %s  (مشتركون=%d)" % (
        "✓" if ok else "✗ الحالة فارغة", bus.subscriber_count(s["out"])))

    # the hot loader's own sequence: purge the old subscriptions, build a NEW one
    bus.unsubscribe_all("old")
    new = await spawn(module, atom_id, s["folder"], bus, "new", start=False)
    # Drain the replay before start publishes any initial state; otherwise a
    # same-name state publication can coalesce over the authoritative replay.
    await bus.drain()
    await new.start()
    await bus.drain()
    after = semantics(atom_id, new)
    print("  ٣· نسخة جديدة (ترقية): بُنيت ثمّ اشتركت")
    same = after == before
    bad += 0 if same else 1
    print("  ٤· الحالة بعد الترقية : %s" % after)
    print("  ٥· تطابق دلاليّ       : %s" % ("✓" if same else "✗ ضاعت"))

    # no loop: let it breathe and count its own output
    baseline = bus.stats()["published"].get(s["out"], 0)
    for _ in range(40):
        await asyncio.sleep(0)
    grew = bus.stats()["published"].get(s["out"], 0) - baseline
    ok = grew <= 2
    bad += 0 if ok else 1
    print("  ٦· لا حلقة راجعة      : نشرات إضافيّة=%d  %s" % (grew, "✓" if ok else "✗ حلقة!"))
    return bad


async def fail_closed(atom_id: int) -> int:
    """A corrupt published state must never open the protection."""
    s = SPECS[atom_id]
    module = load(s["folder"])
    bus = EventBus()
    await bus.publish(s["out"], {"__corrupt__": True}, publisher="old")
    new = await spawn(module, atom_id, s["folder"], bus, "new", start=False)
    await bus.drain()
    await new.start()
    await bus.drain()
    st = semantics(atom_id, new)
    safe = (
        st.get("halted") is True
        or bool(st.get("restore_error"))
        or "__UNKNOWN__" in st.get("halted_accounts", [])
    )
    print("  %-4s حالة منشورة فاسدة ⇒ يبقى محميًّا : %s  (%s)" % (
        atom_id, "✓" if safe else "✗ انفتح!", st))
    return 0 if safe else 1


async def main_async() -> int:
    bad = structural()
    print("\n" + "=" * 78)
    print("ب+ج) الترقية الحيّة — حالة · نشر · نسخة جديدة · وصول · تطابق · لا حلقة")
    print("=" * 78)
    for atom_id in sorted(SPECS):
        bad += await one(atom_id)
    print("\n" + "-" * 78)
    print("د) fail-closed — حالة منشورة فاسدة")
    print("-" * 78)
    for atom_id in sorted(SPECS):
        bad += await fail_closed(atom_id)
    print("\n" + "=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الترقية الحيّة لا تمحو إيقاف المالك ولا تجميده، والفاسد لا يفتح.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
