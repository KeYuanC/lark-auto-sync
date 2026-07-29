"""Validated profile data used by the synchronization runtime."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Profile:
    id: str
    source_path: Path
    workspace_root: Path
    data: dict

    def resolve_path(self, value: str) -> Path:
        candidate = (self.workspace_root / value).resolve()
        candidate.relative_to(self.workspace_root)
        return candidate
