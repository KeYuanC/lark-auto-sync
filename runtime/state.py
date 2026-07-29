from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


class StateError(ValueError):
    """Raised when a state path would escape a profile workspace."""


@dataclass(frozen=True)
class StatePaths:
    root: Path
    staging: Path
    queue: Path
    done: Path
    locks: Path
    extractions: Path
    logs: Path


def create_state_paths(profile: Any) -> StatePaths:
    """Create and return the profile-local state directories."""
    workspace_root = Path(profile.workspace_root).resolve()
    data = profile.data if isinstance(profile.data, Mapping) else {}
    workspace = data.get("workspace", {}) if isinstance(data, Mapping) else {}
    state_directory = workspace.get("state_directory", ".state")
    if not isinstance(state_directory, str) or not state_directory:
        raise StateError("invalid_state_directory")

    configured_path = Path(state_directory)
    if configured_path.is_absolute() or ".." in configured_path.parts:
        raise StateError("invalid_state_directory")

    root = (workspace_root / configured_path / str(profile.id)).resolve()
    try:
        root.relative_to(workspace_root)
    except ValueError as error:
        raise StateError("state_path_outside_workspace") from error

    paths = StatePaths(
        root=root,
        staging=root / "staging",
        queue=root / "queue",
        done=root / "done",
        locks=root / "locks",
        extractions=root / "extractions",
        logs=root / "logs",
    )
    for path in (paths.root, paths.staging, paths.queue, paths.done, paths.locks, paths.extractions, paths.logs):
        path.mkdir(parents=True, exist_ok=True)
    return paths


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write JSON with a same-directory temporary file and atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise StateError("invalid_state_json") from error
    if not isinstance(value, dict):
        raise StateError("invalid_state_json")
    return value


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise StateError("state_path_outside_workspace") from error


_SECRET_VALUE = re.compile(r"(?i)(token|secret|password|authorization)\s*([=:])\s*[^\s,;]+")


def redact_error(error: object) -> str:
    """Keep audit errors useful without retaining common credential values."""
    text = str(error).replace("\r", " ").replace("\n", " ")
    text = _SECRET_VALUE.sub(r"\1\2[redacted]", text)
    return text[:500]


def append_audit_record(paths: StatePaths, workspace_root: Path, record: Mapping[str, Any]) -> None:
    """Append a compact JSONL audit event containing only relative paths."""
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        **record,
    }
    with (paths.logs / "audit.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        handle.write("\n")
