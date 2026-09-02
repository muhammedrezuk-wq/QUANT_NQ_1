"""Contract guard for problem 11 — two atoms, two private ways out.

Owner's ruling, verbatim:

    "11 - ONE transport only. 608 and 620 are its consumers. No second
     transport."  And then: "keep 212 atoms, do not create a 213th; make them
     consumers of one transport that already sits inside the structure."

What was measured before this guard existed:

    620/atom.py opened its own `urllib.request.urlopen`, and 608/atom.py its own
    `socket.socket(AF_INET, SOCK_DGRAM)` -- both breaking atom rule 18, and both
    invisible to any single place that could time them out or account for them.

  أ) المسح  -- no atom.py in the whole project imports a network module. Not a
             sample: all 212 cards' folders.
  ب) الملكيّة -- one transport package exists, exposes both primitives, and the
             two consumers import it instead of owning a socket.
  ج) طرف-لطرف -- the REAL atoms are driven with the transport primitive
             replaced; if either still had a private path, the substitute would
             never be reached and the call would not be recorded.

Exit 1 on any divergence.
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
A608 = "608_مزامنة_الوقت"
A620 = "620_مصدر_ياهو"
OLD = {"608": "2.0.0", "620": "1.1.0"}
NET_IMPORTS = ("socket", "urllib.request", "urllib.error", "http.client",
               "requests", "aiohttp", "httpx")
CONSUMERS = (A608, A620)


def card(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


def code(folder: str) -> str:
    src = (ATOMS / folder / "atom.py").read_text(encoding="utf-8")
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def imports_of(src: str) -> set:
    found = set()
    for line in src.splitlines():
        stripped = line.strip()
        for name in NET_IMPORTS:
            if re.match(r"^(import|from)\s+%s\b" % re.escape(name), stripped):
                found.add(name)
    return found


def structural() -> int:
    print("=" * 86)
    print("أ) المسح — لا ذرّة واحدة تفتح الشبكة بنفسها")
    print("=" * 86)
    bad = 0
    offenders = []
    for path in sorted(ATOMS.glob("*/atom.py")):
        src = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                        if not l.lstrip().startswith("#"))
        hit = imports_of(src)
        if hit:
            offenders.append("%s(%s)" % (path.parent.name.split("_")[0], ",".join(sorted(hit))))
    ok = not offenders
    bad += 0 if ok else 1
    print("      %-38s %s" % ("ذرات تستورد الشبكة مباشرة",
                              "✓ صفر" if ok else "✗ " + " · ".join(offenders)))

    print("\n" + "=" * 86)
    print("ب) الملكيّة — ناقل واحد، والذرّتان مستهلكتان")
    print("=" * 86)
    try:
        transport = importlib.import_module("transport")
        has_both = hasattr(transport, "http_get_json") and hasattr(transport, "udp_exchange")
    except ImportError:
        transport, has_both = None, False
    checks = [("الناقل موجود ويصدّر الاثنين", has_both)]
    for folder in CONSUMERS:
        src = code(folder)
        checks.append(("%s يستورد الناقل" % folder.split("_")[0],
                       re.search(r"^(from|import)\s+transport\b", src, re.M) is not None))
    for folder in CONSUMERS:
        atom_id = folder.split("_")[0]
        src = code(folder)
        version = re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', src, re.M)
        version = version.group(1) if version else ""
        checks.append(("%s نسخة تحرّكت وتطابق البطاقة" % atom_id,
                       version not in ("", OLD[atom_id]) and version == str(card(folder).get("version"))))
    others = [p.parent.name.split("_")[0] for p in sorted(ATOMS.glob("*/atom.py"))
              if re.search(r"^(from|import)\s+transport\b",
                           "\n".join(l for l in p.read_text(encoding="utf-8").splitlines()
                                     if not l.lstrip().startswith("#")), re.M)]
    checks.append(("ولا مستهلك ثالث غير معلَن", sorted(others) == ["608", "620"]))
    checks.append(("عدد الذرات ما زال ٢١٢", len(list(ATOMS.glob("*/manifest.yaml"))) == 212))
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))
    return bad


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


def behavioural() -> int:
    print("\n" + "=" * 86)
    print("ج) طرف-لطرف — الاستبدال يُلتقط، فلا طريق خاصّ باقٍ")
    print("=" * 86)
    bad = 0
    import transport
    import transport.client as client

    calls = []
    real_http, real_udp = client.http_get_json, client.udp_exchange
    client.http_get_json = lambda *a, **k: calls.append("http") or {
        "chart": {"result": [{"meta": {"regularMarketPrice": 42.5}}]}}
    client.udp_exchange = lambda *a, **k: calls.append("udp") or (b"\0" * 48)
    transport.http_get_json = client.http_get_json
    transport.udp_exchange = client.udp_exchange
    try:
        yahoo = load(A620, "_c11_620")
        clock = load(A608, "_c11_608")
        value = None
        try:
            value = yahoo.Atom()._fetch("DX-Y.NYB")
        except Exception:                                            # noqa: BLE001
            pass                       # a private path fails here; that is the point
        ok = "http" in calls and value == 42.5
        bad += 0 if ok else 1
        print("      %-38s قيمة=%-8s %s" % ("620 يمرّ بالناقل", value, "✓" if ok else "✗"))
        try:
            clock.Atom()._query("pool.ntp.org")
        except Exception:                                            # noqa: BLE001
            pass
        ok = "udp" in calls
        bad += 0 if ok else 1
        print("      %-38s %s" % ("608 يمرّ بالناقل", "✓" if ok else "✗ طريق خاصّ باقٍ"))
    finally:
        client.http_get_json, client.udp_exchange = real_http, real_udp
        transport.http_get_json, transport.udp_exchange = real_http, real_udp
    return bad


def main() -> int:
    bad = structural() + behavioural()
    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: مخرج شبكيّ واحد · و608 و620 مستهلكان · والعدد ٢١٢ كما هو.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
