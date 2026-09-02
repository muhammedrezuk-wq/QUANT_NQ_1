"""Contract guard for problem 70 — five publishers of one emergency.

Owner's ruling, verbatim:

    506/507/508 ─┐
                 ├─ risk.halt.requested → 516
    901 ─────────┘

    516 = the ONLY owner of the authority
    516 = the ONLY owner of the release
    and the reason dictionary becomes unified, carrying `origin`, instead of
    every atom keeping its own private dictionary of authority.

What was measured before this guard existed (full census of all 212 cards):

    emergency.halt publishers = 506 507 508 516 901
    risk.kill_switch.reset_requested publishers = 516 901

    Each publisher carried its OWN reason vocabulary -- 506 fires
    SESSION_LOSS_LIMIT / MAX_SESSION_TRADES, which are not 516 constants at all
    -- and no single path released them together, so "half halted" was
    structurally reachable: 506 halted while 516 read as reset.

  أ) الملكيّة  -- census over every card: one publisher of the halt, one of the
                release, and the requesters publish a REQUEST instead.
  ب) القاموس   -- the requesters no longer carry the halt name in code at all,
                and every request states its `origin`.
  ج) طرف-لطرف  -- the REAL 516: a request goes in, the halt comes out carrying
                the requester's reason AND origin, and the switch is shut.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
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
A516 = "516_قاطع_الأمان"
HALT = "emergency.halt"
REQUEST = "risk.halt.requested"
RELEASE = "risk.kill_switch.reset_requested"
RELEASE_REQUEST = "risk.release.requested"
OWNER = "516"
REQUESTERS = ("506", "507", "508", "901")
GATEWAY = "901"
# م-41 (2026-08-28): المستمعون أُعيد قياسهم بعد الدمج — 500 خرج، 575/576/2704 دخلوا
HALT_LISTENERS = {"519", "550", "552", "575", "576", "578", "601", "704"}  # نطاق فوركس فقط — 2704 كريبتو خارج البطاقات


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []
        self.wired = {}

    def subscribe(self, name, handler):
        # Recording the real wiring: calling a handler by attribute proved a
        # hollow barrier -- cutting the subscribe line left the guard green.
        self.wired[name] = handler

    async def publish(self, name, payload):
        self.log.append((name, payload))

    def last(self, name):
        rows = [p for n, p in self.log if n == name]
        return rows[-1] if rows else None

    def count(self, name):
        return sum(1 for n, _ in self.log if n == name)


def cards() -> dict:
    out = {}
    for path in sorted(ATOMS.glob("*/manifest.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out[str(data.get("id"))] = (data, path.parent.name)
    return out


def folder_of(atom_id: str) -> str:
    for aid, (_, name) in cards().items():
        if aid == atom_id:
            return name
    raise KeyError(atom_id)


def code_of(atom_id: str) -> str:
    src = (ATOMS / folder_of(atom_id) / "atom.py").read_text(encoding="utf-8")
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def load(atom_id: str):
    directory = ATOMS / folder_of(atom_id)
    spec = importlib.util.spec_from_file_location("_c70_%s" % atom_id, directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def structural() -> int:
    print("=" * 86)
    print("أ) الملكيّة والقاموس — مسح كل البطاقات، لا عيّنة")
    print("=" * 86)
    bad = 0
    data = cards()
    events = (HALT, REQUEST, RELEASE, RELEASE_REQUEST)
    pub = {e: sorted(a for a, (d, _) in data.items() if e in (d.get("publishes") or []))
           for e in events}
    sub = {e: sorted(a for a, (d, _) in data.items() if e in (d.get("subscribes") or []))
           for e in events}

    checks = (
        ("ناشر واحد للإيقاف وهو %s" % OWNER, pub[HALT] == [OWNER]),
        ("الطالبون ينشرون الطلب", set(pub[REQUEST]) == set(REQUESTERS)),
        ("%s يسمع الطلب" % OWNER, sub[REQUEST] == [OWNER]),
        # Symmetry: the gateway requests the release, 516 alone declares its effect.
        ("طالب الفكّ هو %s وحده" % GATEWAY, pub[RELEASE_REQUEST] == [GATEWAY]),
        ("و%s يسمع طلب الفكّ" % OWNER, sub[RELEASE_REQUEST] == [OWNER]),
        ("ناشر واحد لأثر الفكّ وهو %s" % OWNER, pub[RELEASE] == [OWNER]),
        ("والبوّابات ما زالت تسمعه", {"550", "552"} <= set(sub[RELEASE])),
        ("ولا أحد ينشر ما يستهلكه", not (set(pub[RELEASE]) & set(sub[RELEASE]))),
        ("مستمعو الإيقاف لم يتغيّروا", set(pub[HALT] + sub[HALT]) >= HALT_LISTENERS),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))

    for atom_id in REQUESTERS:
        src = code_of(atom_id)
        clean = HALT not in src
        origin = '"origin"' in src or "'origin'" in src
        ok = clean and origin
        bad += 0 if ok else 1
        print("      %-38s اسم الإيقاف=%-6s origin=%-6s %s"
              % ("%s طالب لا سلطة" % atom_id, "لا" if clean else "نعم",
                 "نعم" if origin else "لا", "✓" if ok else "✗"))

    src516 = code_of(OWNER)
    ok = RELEASE_REQUEST in src516 and src516.count('"%s"' % RELEASE) == 1
    bad += 0 if ok else 1
    print("      %-38s %s" % ("516 يفصل الطلب عن الأثر",
                              "✓" if ok else "✗ ما زال يخلطهما"))
    for atom_id in (OWNER,) + REQUESTERS:
        src = (ATOMS / folder_of(atom_id) / "atom.py").read_text(encoding="utf-8")
        code_version = re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', src, re.M)
        code_version = code_version.group(1) if code_version else ""
        ok = code_version != "" and code_version == str(data[atom_id][0].get("version"))
        bad += 0 if ok else 1
        print("      %-38s كود=%-8s بطاقة=%-8s %s"
              % ("%s نسخة واحدة" % atom_id, code_version,
                 data[atom_id][0].get("version"), "✓" if ok else "✗"))

    print("      %-38s %s" % ("الناشرون المرصودون للإيقاف", " · ".join(pub[HALT]) or "لا أحد"))
    print("      %-38s %s" % ("الناشرون المرصودون للطلب", " · ".join(pub[REQUEST]) or "لا أحد"))
    return bad


async def main_async() -> int:
    bad = structural()

    print("\n" + "=" * 86)
    print("ب) طرف-لطرف — الطلب يدخل 516، والإيقاف يخرج منها بسببه وأصله")
    print("=" * 86)

    module = load(OWNER)
    data, folder = cards()[OWNER]
    bus = Bus()
    atom = module.Atom()
    config = dict(data.get("config") or {})
    config["consumer_db_path"] = (tempfile.mkdtemp(prefix="chk516_") + "/c.db")  # عزل journal
    await atom.initialize(AtomContext(atom_id=516, config=config,
                                      logger=_Logger(), publish=bus.publish,
                                      subscribe=bus.subscribe))
    await atom.start()

    wired_request = REQUEST in bus.wired
    wired_release = RELEASE_REQUEST in bus.wired
    no_self_feed = RELEASE not in bus.wired
    for label, ok in (("الطلب مسلوك فعلًا بالكود", wired_request),
                      ("وطلب الفكّ مسلوك", wired_release),
                      ("ولا يشترك بما ينشره", no_self_feed)):
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))

    handler = bus.wired.get(REQUEST)
    if handler is None:
        print("      ✗ 516 بلا مستقبِل مسلوك للطلب")
        return 1
    await handler({"account_id": "A", "reason": "SESSION_LOSS_LIMIT", "origin": "506"})

    out = bus.last(HALT)
    ok = bool(out) and out.get("reason") == "SESSION_LOSS_LIMIT" and out.get("origin") == "506"
    bad += 0 if ok else 1
    print("      %-38s سبب=%-22s أصل=%-6s %s"
          % ("الإيقاف خرج من المالك", (out or {}).get("reason"), (out or {}).get("origin"),
             "✓" if ok else "✗"))

    ok = bool(atom.book("A")["kill"])
    bad += 0 if ok else 1
    print("      %-38s %-8s %s" % ("والقاطع صار مغلقًا", atom.book("A")["kill"], "✓" if ok else "✗"))

    before = bus.count(HALT)
    await handler({"account_id": "A", "reason": "MAX_SESSION_TRADES", "origin": "506"})
    ok = bus.count(HALT) == before
    bad += 0 if ok else 1
    print("      %-38s %s" % ("ولا إيقاف مكرَّر وهو مغلق", "✓" if ok else "✗"))

    release = bus.wired.get(RELEASE_REQUEST)
    if release is None:
        print("      ✗ 516 بلا مستقبِل مسلوك لطلب الفكّ")
        return 1
    await release({"account_id": "A"})
    announced = bus.last(RELEASE)
    ok = (not atom.book("A")["kill"]) and bus.count(RELEASE) == 1 and (announced or {}).get("origin") == OWNER
    bad += 0 if ok else 1
    print("      %-38s قاطع=%-6s إعلان فكّ=%-4s أصل=%-6s %s"
          % ("والفكّ يفتحه ويُعلن أثره للبوّابات", atom.book("A")["kill"], bus.count(RELEASE),
             (announced or {}).get("origin"), "✓" if ok else "✗"))

    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: سلطة واحدة للإيقاف · طلب موحَّد بأصله · وفكّ بمسار واحد.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
