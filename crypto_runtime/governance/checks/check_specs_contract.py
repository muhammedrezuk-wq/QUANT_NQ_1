"""Contract guard for problem 5-1 (option d) — symbol specs must reach a
consumer that starts LATE, without waiting for a new symbol to appear.

Owner's four requirements, verbatim:

    "The first announcement works.
     A consumer that starts after the announcement gets the specs.
     There is no dependence on a new symbol appearing only.
     Breaking the test fails, then restore."

Measured correction that shaped this guard: `market.symbol_specs` IS
republished periodically -- 618/_loop refreshes every `spec_refresh_s`. An
earlier claim that it is never republished came from capture windows SHORTER
than that period (90-190s against a 300s cycle), which is an inference, not a
measurement. So the real question is not "does it republish" but "how long is
a freshly loaded consumer blind", and whether that window is DECLARED.

  A) STRUCTURAL -- the periodic refresh exists and is not gated on a new
     symbol; the new-symbol path is an ADDITION, not the only trigger; the
     bound is a declared config value, not a hidden constant.

  B) END TO END -- the real 618 against a real sqlite bridge file. A consumer
     that subscribes AFTER the first announcement, and never sees a new symbol,
     must still receive the specs within the declared bound.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.contracts.atom import AtomContext  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
A618 = "618_مصدر_جسر_MT5"
EVENT_SPECS = "market.symbol_specs"

# the guard runs the real atom on a fast clock; the DECLARED bound is read
# from the live manifest and reported, never hard-coded here.
FAST_REFRESH_S = 0.5
FAST_POLL_S = 0.05
SYMBOL = "BTCUSD"

# The blind window a freshly loaded consumer may face, as a CONTRACT number.
# It pins today's declared value: raising `spec_refresh_s` above this makes the
# guard fall, so the window can never grow quietly. Lowering it is the owner's
# call and changes BOTH this constant and the manifest together.
MAX_DECLARED_BLIND_S = 300.0


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []
        self.handlers = {}
        self.late = []          # a consumer that subscribes only later
        self.late_open = False

    def subscribe(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.log.append((name, payload, time.monotonic()))
        if self.late_open and name == EVENT_SPECS:
            self.late.append((payload, time.monotonic()))
        for handler in list(self.handlers.get(name, [])):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result

    def events(self, name):
        return [p for n, p, _ in self.log if n == name]


def load_atom(folder: str):
    directory = ATOMS / folder
    spec = importlib.util.spec_from_file_location("_cspec_" + folder.split("_")[0],
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


def make_bridge(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE ticks (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT,"
                " bid REAL, ask REAL, last REAL, volume REAL, tick_ms REAL)")
    con.execute("CREATE TABLE symbol_specs (symbol TEXT PRIMARY KEY, contract_size REAL,"
                " tick_value REAL, tick_size REAL, point REAL, digits INTEGER,"
                " stops_level INTEGER, freeze_level INTEGER, volume_min REAL,"
                " volume_max REAL, volume_step REAL, filling_mode INTEGER)")
    con.execute("INSERT INTO symbol_specs VALUES (?,1.0,0.01,0.01,0.01,2,0,0,0.01,10.0,0.01,2)",
                (SYMBOL,))
    con.execute("INSERT INTO ticks (symbol,bid,ask,last,volume,tick_ms)"
                " VALUES (?,63000.0,63001.0,63000.5,1.0,1000.0)", (SYMBOL,))
    con.commit()
    con.close()


def structural() -> int:
    print("=" * 78)
    print("أ) الحواجز البنيويّة — الإعادة دوريّة، لا معلَّقة على رمز جديد")
    print("=" * 78)
    bad = 0
    src = (ATOMS / A618 / "atom.py").read_text(encoding="utf-8")
    cfg = manifest(A618)["config"]

    checks = (
        ("إعادة دوريّة موجودة",
         "if loop.time() - last_specs >= self._spec_refresh_s:" in src),
        ("إعلان أوّل عند الإقلاع", "await self._refresh_specs()\n            loop =" in src),
        ("مسار الرمز الجديد إضافة لا شرط وحيد",
         "_announce_if_new" in src and src.count("await self._refresh_specs()") >= 3),
        ("الحدّ معلَن بالمنيفست لا ثابتًا مخفيًّا", "spec_refresh_s" in str(cfg)),
        ("والحدّ ضمن مخطّط الإعدادات",
         "spec_refresh_s" in str(manifest(A618)["config_schema"]["properties"])),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("  %-44s %s" % (label, "✓" if ok else "✗"))
    declared = float(cfg.get("spec_refresh_s") or 0.0)
    ok = 0.0 < declared <= MAX_DECLARED_BLIND_S
    bad += 0 if ok else 1
    print("  %-44s %s" % ("الحدّ لا يتجاوز سقف العقد",
                          "✓ %.0fs ≤ %.0fs" % (declared, MAX_DECLARED_BLIND_S) if ok
                          else "✗ %.0fs > %.0fs — نافذة العمى كبرت بصمت!" % (
                              declared, MAX_DECLARED_BLIND_S)))
    return bad


async def drive() -> tuple[Bus, dict]:
    """The real 618 against a real sqlite bridge, on a fast clock."""
    module = load_atom(A618)
    atom = module.Atom()
    tmp = Path(tempfile.mkdtemp(prefix="specs_guard_"))
    db = tmp / "nq_brain.db"
    make_bridge(db)
    bus = Bus()
    cfg = dict(manifest(A618)["config"])
    cfg.update({"db_path": str(db), "spec_refresh_s": FAST_REFRESH_S,
                "poll_interval_s": FAST_POLL_S, "delete_consumed": False})
    await atom.initialize(AtomContext(atom_id=618, config=cfg, logger=_Logger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    await atom.start()

    # 1) the first announcement
    first_at = None
    started = time.monotonic()
    while time.monotonic() - started < 5.0:
        await asyncio.sleep(0.05)
        if bus.events(EVENT_SPECS):
            first_at = time.monotonic() - started
            break

    # 2) a consumer that subscribes ONLY NOW, and never sees a new symbol
    bus.late_open = True
    late_started = time.monotonic()
    late_at = None
    while time.monotonic() - late_started < 5.0:
        await asyncio.sleep(0.05)
        if bus.late:
            late_at = bus.late[0][1] - late_started
            break

    await atom.stop()
    try:
        db.unlink()
        tmp.rmdir()
    except OSError:
        pass
    return bus, {"first_at": first_at, "late_at": late_at,
                 "announcements": len(bus.events(EVENT_SPECS))}


async def main_async() -> int:
    bad = structural()
    print("\n" + "-" * 78)
    print("ب) طرف-لطرف حقيقيّ — 618 على جسر sqlite فعليّ، بساعة سريعة")
    print("-" * 78)
    bus, got = await drive()
    declared = float(manifest(A618)["config"]["spec_refresh_s"])

    ok = got["first_at"] is not None
    bad += 0 if ok else 1
    print("  ١· الإعلان الأوّل                : %s" % (
        "✓ بعد %.2fs" % got["first_at"] if ok else "✗ لم يقع"))

    ok = got["late_at"] is not None
    bad += 0 if ok else 1
    print("  ٢· مستهلك بدأ بعد الإعلان         : %s" % (
        "✓ وصلته بعد %.2fs" % got["late_at"] if ok else "✗ لم تصله أبدًا"))

    ok = ok and got["late_at"] <= FAST_REFRESH_S * 3
    bad += 0 if ok else 1
    print("  ٣· ضمن الحدّ المعلَن              : %s" % (
        "✓ (%.2fs ≤ %.2fs)" % (got["late_at"], FAST_REFRESH_S * 3) if ok else "✗ تجاوز الحدّ"))

    ok = got["announcements"] >= 2
    bad += 0 if ok else 1
    print("  ٤· لا اعتماد على رمز جديد         : %s" % (
        "✓ %d إعلانًا ولم يظهر رمز جديد قطّ" % got["announcements"] if ok
        else "✗ إعلان واحد فقط — الإعادة معلَّقة على رمز جديد"))

    print("\n  ⏱ نافذة العمى الحقيقيّة على الإعداد الحيّ: حتّى %.0f ثانية بعد كل تحميل" % declared)

    print("\n" + "=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الإعادة دوريّة، والمستهلك المتأخّر يصله البثّ ضمن الحدّ المعلَن.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
