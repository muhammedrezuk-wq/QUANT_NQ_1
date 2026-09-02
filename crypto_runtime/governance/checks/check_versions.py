"""Single-truth version check: code ATOM_VERSION vs manifest version vs the
LIVE core (when :8010 is up). Exit 1 on any divergence — rerunnable evidence,
not a claim. Owner's ruling 2026-08-13: the running code is the operational
reference and no card may say one number while the code does another."""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
# منفذ نواة السوق الجاري — نكشة ٢٠٢٦-٠٨-٢٩: كان 8010 مسمَّرًا، فزرّ هذا الفحص
# بلوحة الكريبتو كان يسأل نواة **الفوركس** ويعرض أرقامها. الافتراض يبقى فوركس.
def _core_api() -> str:
    import os
    market = str(os.environ.get("QUANT_GOV_MARKET", "forex")).strip().lower()
    return "http://127.0.0.1:%d/api/atoms" % (8020 if market == "crypto" else 8010)


CORE_API = _core_api()

RE_CODE = re.compile(r'^ATOM_VERSION\s*=\s*"(\d+\.\d+\.\d+)"', re.M)
# ٢٠٢٦-٠٨-٢٩ — نكشة مقيسة: كان النمط يشترط علامتَي تنصيص حول النسخة.
# منيفستات الكريبتو تكتبها بلا تنصيص (`version: 2.0.1`) وكذلك 37 منيفست فوركس،
# فكان `man_m` يعود None فلا يتحقّق شرط الاختلاف أبدًا. النتيجة: الفحص أعلن
# «checked=84 mismatches=0» و«كل نسخة معلَنة تقول حقيقة واحدة» — وهو لم يقارن
# ولا نسخة كريبتو واحدة. 121 ذرّة (84 كريبتو + 37 فوركس) كانت عمياء عنه.
# الفحص الذي يمرّ أخضر وهو أعمى أسوأ من غياب الفحص. التنصيص صار اختياريًّا.
RE_MAN = re.compile(r"""^version:\s*['"]?(\d+\.\d+\.\d+)['"]?\s*$""", re.M)


def runtime_versions() -> dict[int, str]:
    try:
        with urllib.request.urlopen(CORE_API, timeout=3) as response:
            rows = json.load(response)
    except OSError:
        return {}
    out: dict[int, str] = {}
    for row in rows if isinstance(rows, list) else []:
        atom_id = row.get("id")
        version = row.get("version")
        if isinstance(atom_id, int) and isinstance(version, str):
            out[atom_id] = version
    return out


def main() -> int:
    live = runtime_versions()
    mismatches = 0
    no_constant = 0
    checked = 0
    print("%-34s %-9s %-9s %-9s" % ("ATOM", "CODE", "MANIFEST", "RUNTIME"))
    for atom_dir in sorted(ATOMS.iterdir()):
        atom_py = atom_dir / "atom.py"
        manifest = atom_dir / "manifest.yaml"
        if not atom_py.is_file() or not manifest.is_file():
            continue
        checked += 1
        code_m = RE_CODE.search(atom_py.read_text(encoding="utf-8-sig"))
        man_m = RE_MAN.search(manifest.read_text(encoding="utf-8-sig"))
        code_v = code_m.group(1) if code_m else "-"
        man_v = man_m.group(1) if man_m else "-"
        try:
            atom_id = int(atom_dir.name.split("_")[0])
        except ValueError:
            atom_id = -1
        run_v = live.get(atom_id, "")
        bad = (code_m and man_m and code_v != man_v) or (
            run_v and man_v != "-" and run_v != man_v)
        if code_m is None:
            no_constant += 1
        if bad:
            mismatches += 1
            print("%-34s %-9s %-9s %-9s  <-- MISMATCH" % (
                atom_dir.name, code_v, man_v, run_v or "?"))
    print()
    print("checked=%d mismatches=%d atoms_without_code_constant=%d live_core=%s"
          % (checked, mismatches, no_constant, "yes" if live else "no"))
    if mismatches == 0:
        print("OK: every declared version tells one truth.")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
