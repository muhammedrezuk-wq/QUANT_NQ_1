"""Regression guard for approved cTrader/MT5 bridge paper 11."""
from __future__ import annotations

import sys
from pathlib import Path
import subprocess,sys

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)



def source(atom_id:int)->str:return (next((ATOM_ROOT).glob(f"{atom_id}_*"))/"atom.py").read_text()


def main()->int:
 result=subprocess.run([sys.executable,str(ROOT/"governance/scripts/proof_bridges.py"),str(ROOT)],capture_output=True,text=True)
 print(result.stdout,end="")
 s551=source(551);s575=source(575);s601=source(601)
 checks={
  "order builder stamps magic":"\"magic\":self._magic" in s551,
  "brain writer rejects missing magic":"MISSING_OR_FOREIGN_MAGIC" in s601,
  "management writer checks scoped ownership":"POSITION_OWNERSHIP_MISMATCH" in s575 and "magic=?" in s575,
  "brain writer uses account_v2 only":"FROM account_v2" in s601 and 'else "account"' not in s601,
  "source role documented":"MT5 هو مصدر التنفيذ الوحيد" in (ROOT/"atoms/617_cTrader_feed/الشرح.md").read_text(),
 }
 failed=[name for name,ok in checks.items() if not ok]
 if result.returncode or failed:
  print("PAPER_11_BRIDGE_CONTRACT=FAIL")
  for name in failed:print(name)
  return 1
 print("PAPER_11_BRIDGE_CONTRACT=PASS")
 return 0


if __name__=="__main__":raise SystemExit(main())
