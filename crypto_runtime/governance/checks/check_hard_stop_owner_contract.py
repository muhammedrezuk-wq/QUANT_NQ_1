"""Contract guard for problem 57 — one field, two publishers, two authorities.

Owner's ruling, verbatim:

    "57 - (a) strip the `risk.hard_stop.price` declaration from 512, inside the
     package.  525 is the ONLY owner.  512 computes its asset_stop.state and
     sends the event to the owner.  581 consumes the canonical one."

What was measured before this guard existed:

    Both 512 and 525 declared AND published `risk.hard_stop.price`. Determinism
    had been settled at the consuming end only -- 571 names 525 as its source --
    while the field itself still had two authorities upstream. Two writers of
    one protection number is exactly the shape the owner forbids: whoever fires
    last wins, silently.

  أ) ملكيّة  -- a project-wide census of all 212 cards: exactly ONE publisher,
              and it is 525.  512 no longer declares it and no longer carries
              the constant or the publish call.
  ب) الجوار  -- 512 keeps its own `risk.asset_stop.state`, 571 keeps consuming
              the canonical field, and 581 keeps reading the state (never a
              second hard-stop authority).
  ج) طرف-لطرف -- the REAL 512 driven by a REAL ledger publishes its state and
              emits ZERO `risk.hard_stop.price`.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.contracts.atom import AtomContext  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
A512 = "512_الوقف_الهيكلي"
A525 = "525_سعر_الستوب_الصلب"
A571 = "571_مخطط_الإدارة_الدائمة"
A581 = "581_محرك_فرق_المركز"
OLD_512 = "2.1.0"
FIELD = "risk.hard_stop.price"
STATE = "risk.asset_stop.state"
OWNER_ID = "525"
ACC, SYM = "A", "GOLD"


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.log.append((name, payload))

    def count(self, name):
        return sum(1 for n, _ in self.log if n == name)


def load():
    directory = ATOMS / A512
    spec = importlib.util.spec_from_file_location("_c57_512", directory / "atom.py")
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


def census() -> list:
    """Every card in the project, not a sample."""
    found = []
    for path in sorted(ATOMS.glob("*/manifest.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if FIELD in (data.get("publishes") or []):
            found.append(str(data.get("id")))
    return found


def structural() -> int:
    print("=" * 86)
    print("أ) الملكيّة — ناشر واحد لا اثنان، بمسح كل البطاقات")
    print("=" * 86)
    bad = 0
    owners = census()
    src512 = (ATOMS / A512 / "atom.py").read_text(encoding="utf-8")
    code_version = re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', src512, re.M)
    code_version = code_version.group(1) if code_version else ""

    checks = (
        ("ناشر واحد فقط للحقل", len(owners) == 1),
        ("والمالك هو %s" % OWNER_ID, owners == [OWNER_ID]),
        ("512 لا يعلنه", FIELD not in (card(A512).get("publishes") or [])),
        ("512 لا يحمل ثابته", "EVENT_HARD_STOP" not in src512),
        # A comment naming the field is documentation, not a publish. Counting it
        # made the barrier fire on the very note that records the owner's ruling.
        ("512 لا ينشره بالكود", FIELD not in "\n".join(
            line for line in src512.splitlines() if not line.lstrip().startswith("#"))),
        ("512 يبقي حالته هو", STATE in (card(A512).get("publishes") or [])),
        ("525 يعلنه فعلًا", FIELD in (card(A525).get("publishes") or [])),
        ("571 يبقى مستهلكه", FIELD in (card(A571).get("subscribes") or [])),
        ("581 يقرأ الحالة لا سلطة ثانية",
         STATE in (card(A581).get("subscribes") or [])
         and FIELD not in (card(A581).get("subscribes") or [])),
        ("نسخة 512 تحرّكت عن %s" % OLD_512, code_version not in ("", OLD_512)),
        ("الكود والبطاقة نسخة واحدة", code_version == str(card(A512).get("version"))),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))
    print("      %-38s %s" % ("الناشرون المرصودون", " · ".join(owners) or "لا أحد"))
    return bad


async def main_async() -> int:
    bad = structural()
    module = load()

    print("\n" + "=" * 86)
    print("ب) طرف-لطرف — 512 الحقيقيّة بدفتر حقيقيّ: حالتها تخرج، والحقل لا")
    print("=" * 86)

    bus = Bus()
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=512, config=dict(card(A512).get("config") or {}),
                                      logger=_Logger(), publish=bus.publish,
                                      subscribe=bus.subscribe))
    await atom.start()
    await atom._on_ledger({"ledgers": [{
        "account_id": ACC, "symbol": SYM, "asset_canonical": SYM,
        "risk_budget": 100.0, "R": 100.0, "budgeted": True,
        "v_net": 0.3, "w": 600.0369369369, "vpu": 1.0,
        "K": 0.0, "cost": 0.0, "X": 0.0, "open_legs": 1,
    }], "count": 1})

    state_out = bus.count(STATE)
    field_out = bus.count(FIELD)
    for label, value, want in (("حالة الأصل تُنشَر", state_out, 1),
                               ("والحقل لا يُنشَر إطلاقًا", field_out, 0)):
        ok = value == want
        bad += 0 if ok else 1
        print("      %-38s %-4s %s" % (label, value, "✓" if ok else "✗"))

    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: 525 المالك الوحيد · 512 يحتفظ بحالته · و571 يستهلك الأصل.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
