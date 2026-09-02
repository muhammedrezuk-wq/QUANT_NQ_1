#!/usr/bin/env python3
"""Verify only the owner-approved four-paper infrastructure freeze scope."""
from __future__ import annotations

import sys
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

SEAL=ROOT/'governance/seals/INFRASTRUCTURE_4_PAPERS_SCOPE_FREEZE_20260816.json'
SEAL_SHA=SEAL.with_suffix(SEAL.suffix+'.sha256')
PRIMARY=('003','006','007','111','608','701','702','703','704','706','707','708','709','712','713','714','715','716','717','718','753','800','802','803','806')
SUPPORTING=('500','506','507','516','552')
SHARED=('clock','catchup','storage_policy')
EXPLICIT=('pyproject.toml','governance/scripts/verify_four_papers_scope_freeze.py','governance/seals/REVOCATION_SYSTEM_FREEZE_20260816.json','تسليم_مصحح_تجميد_ذرات_الورقات_الأربع_فقط.md')
SKIP={'__pycache__','.pytest_cache'}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def atom_dir(atom_id):
 rows=[p for p in (ATOM_ROOT).iterdir() if p.is_dir() and p.name.startswith(atom_id+'_')]
 if len(rows)!=1:raise RuntimeError(f'ATOM_SCOPE_{atom_id}_COUNT_{len(rows)}')
 return rows[0]
def files_under(base):
 return {p.relative_to(ROOT).as_posix():sha(p) for p in base.rglob('*') if p.is_file() and not any(x in SKIP for x in p.parts)}
def inventory():
 out={}
 for atom_id in PRIMARY+SUPPORTING:out.update(files_under(atom_dir(atom_id)))
 for name in SHARED:out.update(files_under(ROOT/name))
 for rel in EXPLICIT:
  p=ROOT/rel
  if p.is_file():out[rel]=sha(p)
 return out
def main():
 if not SEAL.is_file():print('SCOPED_SEAL_MISSING');return 1
 data=json.loads(SEAL.read_text('utf8'));expected=data['files'];actual=inventory()
 changed=sorted(k for k,v in expected.items() if actual.get(k)!=v);missing=sorted(set(expected)-set(actual));unexpected=sorted(set(actual)-set(expected))
 core=json.loads((ROOT/'core/CORE.lock').read_text('utf8'))['root_digest'];core_ok=core==data['core_root_digest'];ok=not changed and not missing and not unexpected and core_ok
 print(json.dumps({'ok':ok,'scope':'four_infrastructure_papers_only','primary_atoms':len(PRIMARY),'supporting_atoms':len(SUPPORTING),'files':len(expected),'changed':changed,'missing':missing,'unexpected_in_scope':unexpected,'outside_scope_frozen':False,'core_ok':core_ok},ensure_ascii=False))
 return 0 if ok else 1
if __name__=='__main__':raise SystemExit(main())
