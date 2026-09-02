"""Contract guard for problem 77 — the reference froze on a dead price.

What was measured live (2026-08-15, 70s on BTCUSD, 33 incoming ticks, 262
verdicts): the real price range was 62957.14 -> 62959.81, the largest gap
between two consecutive ticks was $2.44, and the configured threshold is
max_tick_jump = 500 -- a 200x margin. Yet 90 of 90 ticks came out
`reason=TICK_JUMP · valid=false · state=STALE · selected_provider=null`, and
521 published NO_USABLE_REFERENCE 712 times in 180 seconds: the second most
frequent event in the entire system after the clock pulse.

The root, at 521/atom.py jump_ok: on rejection it returned BEFORE writing
`_last_prices[key]`. So the comparison stayed pinned to one old price, every
later tick was measured against a dead number, and the latch never opened.

  أ) بنيويّ  -- the reference is written on EVERY tick, before the verdict.
  ب) طرف-لطرف -- the REAL atom: one genuine jump is rejected ONCE, and the very
              next quiet tick rebuilds a valid reference. A latched atom cannot
              pass this: it rejects the quiet tick too.

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
A521 = "521_صحة_المرجع"
OLD = "2.1.0"
SYM = "BTCUSD"


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


def card() -> dict:
    return yaml.safe_load((ATOMS / A521 / "manifest.yaml").read_text(encoding="utf-8"))


def load():
    directory = ATOMS / A521
    spec = importlib.util.spec_from_file_location("_c77_521", directory / "atom.py")
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
    print("أ) بنيويّ — المرجع يُكتب مع كل تكّة، قبل الحكم")
    print("=" * 86)
    bad = 0
    src = (ATOMS / A521 / "atom.py").read_text(encoding="utf-8")
    start = src.index("def jump_ok")
    body = src[start:src.index("async def _on_primary", start)]
    writes = body.index("self._last_prices[key]=") if "self._last_prices[key]=" in body else -1
    rejects = body.index("return False") if "return False" in body else len(body) + 1
    version = re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', src, re.M)
    version = version.group(1) if version else ""
    checks = (
        ("المرجع يُكتب داخل الدالّة", writes >= 0),
        ("ولا رجوع قبل كتابته", writes >= 0 and writes < rejects),
        ("النسخة تحرّكت عن %s" % OLD, version not in ("", OLD)),
        ("الكود والبطاقة نسخة واحدة", version == str(card().get("version"))),
        ("العتبة لم تُمَسّ", float((card().get("config") or {}).get("max_tick_jump")) == 500.0),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))
    return bad


async def main_async() -> int:
    bad = structural()
    module = load()

    print("\n" + "=" * 86)
    print("ب) طرف-لطرف — قفزة واحدة تُرفض مرّة، والتكّة الهادئة بعدها تُقبل")
    print("=" * 86)

    bus = Bus()
    atom = module.Atom()
    config = dict(card().get("config") or {})
    config["symbols"] = [SYM]
    await atom.initialize(AtomContext(atom_id=521, config=config, logger=_Logger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    await atom.start()
    atom._now = 1000.0

    async def tick(price, stamp):
        await atom._on_primary({"symbol": SYM, "bid": price - 0.5, "ask": price + 0.5,
                                "price": price, "timestamp": stamp})
        return dict(atom.feed(SYM)["PRIMARY"] if "PRIMARY" in atom.feed(SYM)
                    else atom.feed(SYM)[module.PRIMARY])

    first = await tick(62957.0, 1000.0)
    jump = await tick(70000.0, 1001.0)
    quiet = await tick(70001.0, 1002.0)
    after = await tick(70002.0, 1003.0)

    rows = (("أوّل تكّة مقبولة", first, ""),
            ("والقفزة تُرفض مرّة", jump, "TICK_JUMP"),
            ("والهادئة بعدها تُقبل", quiet, ""),
            ("والتي تليها كذلك", after, ""))
    for label, state, want in rows:
        got = str(state.get("reason") or "")
        ok = got == want and bool(state.get("valid")) == (want == "")
        bad += 0 if ok else 1
        print("      %-38s سبب=%-22s صالح=%-6s %s"
              % (label, got or "-", state.get("valid"), "✓" if ok else "✗"))

    latched = quiet.get("reason") == "TICK_JUMP"
    bad += 1 if latched else 0
    print("      %-38s %s" % ("لا مِزلاج: المرجع تقدّم",
                              "✗ ما زال مقفولًا" if latched else "✓"))

    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: القفزة تُرفض مرّة واحدة، والمرجع يُعاد بناؤه فورًا.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
