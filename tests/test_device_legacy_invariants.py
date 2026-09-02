from pathlib import Path

from build_registry.paths import RegistryAtomRoot
ROOT = Path(__file__).resolve().parents[1]
ATOM_ROOT = RegistryAtomRoot(ROOT)

def test_device_resource_atom_keeps_raw_fact_contract():
 source=(ATOM_ROOT/'753_موارد_الجهاز'/'atom.py').read_text('utf8')
 assert 'cpu_pct' in source and 'memory_pct' in source
 assert 'danger' not in source and 'critical_threshold' not in source
