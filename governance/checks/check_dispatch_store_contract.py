"""Contract guard for problem 61 — a stream nobody listened to.

Owner's ruling, verbatim scope for this item:

    "61 - 707 becomes the subscriber. It keeps BOTH streams, 578 and 467.
     No silent replacement. We do not delete the declaration."

What was measured before this guard existed:

    Item 4 stopped 467 from publishing `execution.order.requested`; it now
    publishes `decision.dispatch.state` with `executable: false`. But nothing in
    the project subscribed to that name -- an honest diagnostic hook with no
    reader -- and 707, the decision store, silently lost the 467 stream while
    keeping only the 581 one. The audit trail had a hole in it.

  أ) بنيويّ  -- 707 declares AND wires the dispatch stream, the execution
              request stream is still there beside it (no replacement), and
              467's own declaration was not deleted to make room.
  ب) طرف-لطرف -- the REAL 707 against a REAL sqlite store: both events are fed
              and BOTH land as rows, under two distinct stages.

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
A707 = "707_مخزن_القرارات"
A467 = "467_إرسال_القرار"
OLD_VERSION = "2.1.0"
EVENT_DISPATCH = "decision.dispatch.state"
EVENT_REQUEST = "execution.order.requested"
STAGE_DISPATCH = "DISPATCH"
STAGE_REQUEST = "ORDER_REQUESTED"


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.handlers = {}
        self.log = []

    def subscribe(self, name, handler):
        self.handlers[name] = handler

    async def publish(self, name, payload):
        self.log.append((name, payload))


def load():
    directory = ATOMS / A707
    spec = importlib.util.spec_from_file_location("_c61_707", directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def card(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


def structural() -> int:
    print("=" * 86)
    print("أ) الحواجز البنيويّة — التيّاران معًا، ولا إعلان يُحذف")
    print("=" * 86)
    bad = 0
    src = (ATOMS / A707 / "atom.py").read_text(encoding="utf-8")
    subs = card(A707).get("subscribes") or []
    pubs467 = card(A467).get("publishes") or []
    code_version = re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', src, re.M)
    code_version = code_version.group(1) if code_version else ""
    # The block ends at a ")" in column 0 -- not at the first ")" found, which
    # is the close of the first tuple and silently truncated the whole table.
    start = src.index("_STAGES = (")
    stages = src[start:src.index("\n)", start)]

    checks = (
        ("707 يعلن تيّار الإرسال", EVENT_DISPATCH in subs),
        ("707 يسلك تيّار الإرسال", "EVENT_DISPATCH" in stages),
        ("ومرحلة مستقلّة له", STAGE_DISPATCH in src),
        ("وتيّار التنفيذ باقٍ معه", EVENT_REQUEST in subs and "EVENT_ORDER_REQUESTED" in stages),
        ("لا استبدال صامت", len([s for s in subs if s in (EVENT_DISPATCH, EVENT_REQUEST)]) == 2),
        ("إعلان 467 لم يُحذف", EVENT_DISPATCH in pubs467),
        ("467 لا ينشر طلب تنفيذ", EVENT_REQUEST not in pubs467),
        ("النسخة تحرّكت عن %s" % OLD_VERSION, code_version not in ("", OLD_VERSION)),
        ("الكود والبطاقة نسخة واحدة", code_version == str(card(A707).get("version"))),
        ("لا حرف عربيّ داخل الكود", not re.search(r"[؀-ۿ]", src)),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))
    return bad


async def main_async() -> int:
    bad = structural()
    module = load()

    print("\n" + "=" * 86)
    print("ب) طرف-لطرف — مخزن حقيقيّ، والتيّاران يهبطان صفَّين مختلفين")
    print("=" * 86)

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "decisions.db")
        bus = Bus()
        atom = module.Atom()
        config = dict(card(A707).get("config") or {})
        config["db_path"] = db
        await atom.initialize(AtomContext(atom_id=707, config=config, logger=_Logger(),
                                          publish=bus.publish, subscribe=bus.subscribe))
        await atom.start()

        base = {"symbol": "BTCUSD", "timeframe": "60s", "account_id": "52992818",
                "cycle_id": "BTCUSD|60s|T0", "request_id": "req-1", "side": "buy"}
        for name, extra in ((EVENT_DISPATCH, {"executable": False, "source": "decision_dispatch"}),
                            (EVENT_REQUEST, {"volume": 0.1, "reference_price": 62962.66})):
            handler = bus.handlers.get(name)
            if handler is None:
                print("      %-38s ✗ لا مشترك" % name)
                bad += 1
                continue
            payload = dict(base)
            payload.update(extra)
            await handler(payload)

        rows = {}
        try:
            connection = sqlite3.connect(db)
            try:
                for stage, count in connection.execute(
                        "SELECT stage, COUNT(*) FROM decisions GROUP BY stage"):
                    rows[stage] = count
            finally:
                connection.close()
        except sqlite3.Error as exc:                                   # noqa: BLE001
            print("      ✗ تعذّرت قراءة المخزن: %s" % exc)
            return 1

        for stage in (STAGE_DISPATCH, STAGE_REQUEST):
            ok = rows.get(stage, 0) == 1
            bad += 0 if ok else 1
            print("      %-38s صفوف=%-4s %s" % ("مرحلة %s مخزَّنة" % stage,
                                                rows.get(stage, 0), "✓" if ok else "✗"))

        ok = len(rows) >= 2
        bad += 0 if ok else 1
        print("      %-38s %-8s %s" % ("مرحلتان متمايزتان لا واحدة", len(rows),
                                       "✓" if ok else "✗ تيّار ابتلع الآخر"))

    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: 707 يخزّن تيّاري 467 و581 معًا، ولا إعلان حُذف.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
