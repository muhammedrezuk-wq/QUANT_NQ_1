"""Compatibility path facade for legacy governance checkers.

The facade preserves the old ``ATOMS / folder`` spelling while resolving the
folder through BuildRegistry by atom identity. It is intentionally outside
Core and never writes to the tree.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Iterator

from .registry import BuildRegistry


_ID_PREFIX = re.compile(r"^(\d+)(?:_|$)")


class RegistryAtomRoot:
    """Path-like view whose atom lookups are recursive and registry-backed."""

    def __init__(self, project_root: Path | str, scope: str | None = None) -> None:
        # ٢٠٢٦-٠٨-٢٩ — نكشة مقيسة: كان السكوب الافتراضي "forex" ثابتًا، وكل فاحص
        # يكتب `RegistryAtomRoot(ROOT)` بلا سكوب (المدقّق · الملفّات · الأحداث ·
        # الاختبارات · النسخ). فأزرار «فحوصات الحوكمة» بلوحة **الكريبتو** كانت
        # تمسح شجرة **الفوركس** وتعرض أرقامها: `versions` أعلن `checked=233`
        # وهو عدد ذرّات الفوركس، و`health` ردّ بذرّة `#7` وهي فوركسيّة.
        # فحصٌ يقيس السوق الخطأ أسوأ من لا فحص — يعطي طمأنينة كاذبة.
        # الآن: السكوب من بيئة عمليّة الحوكمة (`QUANT_GOV_MARKET` يضبطه
        # `scripts/run_governance.py`)، والافتراض يبقى «فوركس» عند غيابه —
        # فسلوك سطر الأوامر وسلوك الفوركس لم يتغيّرا حرفًا.
        import os as _os
        if scope is None:
            scope = str(_os.environ.get("QUANT_GOV_MARKET", "forex")).strip().lower()
            if scope not in {"forex", "crypto"}:
                scope = "forex"
        self.project_root = Path(project_root).resolve()
        self.scope = scope
        self.root = self.project_root / ("atoms_crypto" if scope == "crypto" else "atoms")
        self._registry = BuildRegistry(self.project_root)

    def _records(self):
        snapshot = self._registry.snapshot()
        return snapshot.crypto_all if self.scope == "crypto" else snapshot.forex_all

    def _resolve_name(self, name: str) -> Path | None:
        match = _ID_PREFIX.match(name)
        if not match:
            return None
        atom_id = int(match.group(1))
        records = [record for record in self._records() if record.atom_id == atom_id]
        if len(records) != 1:
            return None
        return Path(records[0].path)

    def __truediv__(self, other: Any) -> Path:
        name = str(other)
        parts = Path(name).parts
        if parts:
            resolved = self._resolve_name(parts[0])
            if resolved is not None:
                return resolved.joinpath(*parts[1:])
        return self.root / other

    def iterdir(self) -> Iterator[Path]:
        """Return discovered atom directories, regardless of section depth."""
        return iter(Path(record.path) for record in self._records())

    def glob(self, pattern: str) -> Iterator[Path]:
        if pattern.endswith("/manifest.yaml") or pattern.endswith("\\manifest.yaml"):
            folder_pattern = pattern.rsplit("/", 1)[0].rsplit("\\", 1)[-1]
            return iter(
                Path(record.manifest_path) for record in self._records()
                if record.manifest_path and fnmatch.fnmatch(Path(record.path).name, folder_pattern)
            )
        if pattern.endswith("/atom.py") or pattern.endswith("\\atom.py"):
            folder_pattern = pattern.rsplit("/", 1)[0].rsplit("\\", 1)[-1]
            return iter(
                Path(record.path) / "atom.py" for record in self._records()
                if fnmatch.fnmatch(Path(record.path).name, folder_pattern)
            )
        if any(char in pattern for char in "*?["):
            matched = [Path(record.path) for record in self._records()
                       if fnmatch.fnmatch(Path(record.path).name, pattern)]
            if matched:
                return iter(matched)
        resolved = self._resolve_name(pattern.rstrip("*"))
        return iter((resolved,)) if resolved is not None else self.root.glob(pattern)

    def rglob(self, pattern: str) -> Iterator[Path]:
        if pattern == "manifest.yaml":
            return iter(Path(record.manifest_path) for record in self._records() if record.manifest_path)
        if pattern == "atom.py":
            return iter(Path(record.path) / "atom.py" for record in self._records())
        return self.root.rglob(pattern)

    def exists(self) -> bool:
        return self.root.exists()

    def is_dir(self) -> bool:
        return self.root.is_dir()

    def resolve(self) -> Path:
        return self.root.resolve()

    def __fspath__(self) -> str:
        return str(self.root)

    def __str__(self) -> str:
        return str(self.root)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.root, name)
