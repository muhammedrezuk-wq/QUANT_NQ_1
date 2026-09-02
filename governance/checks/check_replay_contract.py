"""Contract guard for problem 69 — dead history re-fired as fresh losses.

Owner's ruling, verbatim:

    "69 - (c) stop the replay at 611, at the source. The snapshot / the pointer
     are the continuity point; we do not treat the duplication in a lower layer
     after the history has already been pumped in.  And put a REAL trade
     identity into the 517 -> 516 chain."

What was measured before this guard existed:

    var/snapshots was empty, so 611 restored nothing and started at _last_id = 0
    -- and drained the WHOLE bridge table as if it had just happened. 563
    converted it, 517 turned it into 537 `risk.loss_reported` events all
    stamped inside one second (the boot moment), and 516 tripped
    MAX_CONSECUTIVE_LOSSES on trades that had closed a day and a third earlier.
    The whole execution chain sat NO_INPUT behind a breaker fired by ghosts.
    And 517's payload carried no ticket at all: 537 events, one distinct key.

  أ) المصدر  -- with no restored pointer, 611 starts from the CURRENT end of the
              table, not from zero. History is never replayed.
  ب) الاستمراريّة -- a restored pointer is still obeyed exactly; the snapshot
              remains the continuity point, so nothing is skipped after a clean
              stop.
  ج) الهويّة -- 517 states the ticket and the trade id on every loss report, so
              the chain into 516 can name what it counted.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.contracts.atom import AtomContext  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
A611 = "611_قارئ_الصفقات"
A517 = "517_نتيجة_الصفقة"
OLD = {"611": "2.1.0", "517": "1.1.0"}
EVENT_LOSS = "risk.loss_reported"
HISTORY_ROWS = 40


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []
        self.wired = {}

    def subscribe(self, name, handler):
        self.wired[name] = handler

    async def publish(self, name, payload):
        self.log.append((name, payload))

    def last(self, name):
        rows = [p for n, p in self.log if n == name]
        return rows[-1] if rows else None


def card(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


def load(folder: str, alias: str):
    directory = ATOMS / folder
    spec = importlib.util.spec_from_file_location(alias, directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def make_bridge(path: str, columns: tuple) -> None:
    """The table must carry the atom's REAL column list.

    A first harness invented its own columns; the atom's SELECT then raised,
    `_drain_once` swallowed it as a read failure, and BOTH branches published
    zero -- a hollow barrier that would have passed "no replay" for the wrong
    reason. The columns now come from the atom itself.
    """
    extra = [c for c in ("account_id", "profit", "request_id") if c not in columns]
    fields = ["%s TEXT" % c for c in columns if c != "id"] + ["%s TEXT" % c for c in extra]
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trade_events (id INTEGER PRIMARY KEY AUTOINCREMENT, %s)"
                 % ", ".join(fields))
    names = [c for c in columns if c != "id"] + extra
    marks = ", ".join("?" for _ in names)
    for i in range(HISTORY_ROWS):
        values = ["1.0" if n in ("close_time", "open_time") else "x" for n in names]
        conn.execute("INSERT INTO trade_events (%s) VALUES (%s)" % (", ".join(names), marks),
                     tuple(values))
    conn.commit()
    conn.close()


def version_of(folder: str) -> str:
    src = (ATOMS / folder / "atom.py").read_text(encoding="utf-8")
    found = re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', src, re.M)
    return found.group(1) if found else ""


def structural() -> int:
    print("=" * 86)
    print("أ) بنيويّ — المؤشّر نقطة الاستمراريّة، والهويّة معلَنة")
    print("=" * 86)
    bad = 0
    src611 = (ATOMS / A611 / "atom.py").read_text(encoding="utf-8")
    src517 = (ATOMS / A517 / "atom.py").read_text(encoding="utf-8")
    checks = (
        ("611 يعلم أنّه استُعيد أم لا", "_restored" in src611),
        ("611 يبدأ من آخر الجدول بلا لقطة", "MAX(id)" in src611 or "max(id)" in src611),
        ("517 يحمل التذكرة", '"ticket"' in src517),
        ("517 يحمل معرّف الصفقة", '"trade_id"' in src517),
        ("611 نسخة تحرّكت", version_of(A611) not in ("", OLD["611"])),
        ("517 نسخة تحرّكت", version_of(A517) not in ("", OLD["517"])),
        ("611 كود=بطاقة", version_of(A611) == str(card(A611).get("version"))),
        ("517 كود=بطاقة", version_of(A517) == str(card(A517).get("version"))),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))
    return bad


async def drive(module, db: str, restore_to: int | None):
    bus = Bus()
    atom = module.Atom()
    config = dict(card(A611).get("config") or {})
    config["db_path"] = db
    config["table_name"] = "trade_events"
    await atom.initialize(AtomContext(atom_id=611, config=config, logger=_Logger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    if restore_to is not None:
        await atom.restore({"last_id": restore_to})
    await atom.start()          # the seeding lives here -- drive the real path
    await atom.stop()           # and stop the loop so only our drain runs
    bus.log.clear()
    await atom._drain_once()
    return atom, bus


async def main_async() -> int:
    bad = structural()

    print("\n" + "=" * 86)
    print("ب) طرف-لطرف — جسر فيه %d صفًّا تاريخيًّا" % HISTORY_ROWS)
    print("=" * 86)

    module = load(A611, "_c69_611")
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "nq_brain.db")
        make_bridge(db, tuple(module._COLUMNS))

        atom, bus = await drive(module, db, None)
        replayed = len(bus.log)
        ok = replayed == 0 and atom._last_id == HISTORY_ROWS
        bad += 0 if ok else 1
        print("      %-38s أحداث=%-6s مؤشّر=%-6s %s"
              % ("بلا لقطة: لا يعيد بثّ التاريخ", replayed, atom._last_id,
                 "✓" if ok else "✗ أعاد الضخّ"))

        atom2, bus2 = await drive(module, db, HISTORY_ROWS - 5)
        ok = len(bus2.log) == 5
        bad += 0 if ok else 1
        print("      %-38s أحداث=%-6s %s"
              % ("وبلقطة: يكمل من حيث وقف", len(bus2.log), "✓" if ok else "✗"))

    print("\n  والهويّة تعبر إلى 516:")
    mod517 = load(A517, "_c69_517")
    bus3 = Bus()
    outcome = mod517.Atom()
    await outcome.initialize(AtomContext(atom_id=517, config=dict(card(A517).get("config") or {}),
                                         logger=_Logger(), publish=bus3.publish,
                                         subscribe=bus3.subscribe))
    await outcome.start()
    handler = bus3.wired.get("market.outcome.realized")
    account = bus3.wired.get("platform.account.state")
    if handler is None or account is None:
        print("      ✗ 517 بلا مستقبِل مسلوك")
        return 1
    # Without reference capital 517 drops the outcome and publishes nothing --
    # the first harness read that silence as "no identity" and blamed the atom.
    await account({"account_id": "A", "equity": 1000.0, "balance": 1000.0})
    await handler({"account_id": "A", "symbol": "BTCUSD", "profit": -5.0,
                   "ticket": 777, "trade_id": "t-777", "result": "LOSS"})
    out = bus3.last(EVENT_LOSS)
    ok = bool(out) and out.get("ticket") == 777 and out.get("trade_id") == "t-777"
    bad += 0 if ok else 1
    print("      %-38s تذكرة=%-8s معرّف=%-8s %s"
          % ("تقرير الخسارة يسمّي الصفقة", (out or {}).get("ticket"),
             (out or {}).get("trade_id"), "✓" if ok else "✗"))

    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: لا إعادة بثّ للتاريخ · واللقطة تُطاع · والخسارة تسمّي صفقتها.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
