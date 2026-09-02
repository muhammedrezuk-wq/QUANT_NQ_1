"""Central, read-only build discovery registry for QUANT_NQ.

This package owns build discovery metadata only. It does not instantiate,
start, stop, or reload atoms, and it never changes the source tree.
"""

from .contracts import AtomBootResult, CoreBootResult, ReleaseGateResult, core_boot_from_report, evaluate_release
from .models import BuildSnapshot, ComponentRecord, BuildSource, RootSpec
from .registry import BuildRegistry

__all__ = [
    "AtomBootResult",
    "BuildRegistry",
    "BuildSnapshot",
    "BuildSource",
    "ComponentRecord",
    "CoreBootResult",
    "ReleaseGateResult",
    "RootSpec",
    "core_boot_from_report",
    "evaluate_release",
]
