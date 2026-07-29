"""Load and validate profile YAML without allowing paths outside its workspace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from runtime.models import Profile
from runtime.safety import require_safe_identifier


class ProfileError(ValueError):
    """A profile is malformed, unsupported, or unsafe to use."""


_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "profile.schema.json"
_PATH_FIELDS = (
    ("workspace", "state_directory"),
    ("processing", "extraction_schema"),
    ("processing", "aliases"),
    ("processing", "terminology"),
    ("publish", "local", "directory"),
    ("publish", "github", "path"),
    ("publish", "lark_receipt", "template"),
)


def load_profile(path: Path) -> Profile:
    """Read a version-1 Profile and validate all configured local paths."""
    source_path = Path(path).resolve()
    try:
        raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ProfileError("profile_parse_error") from error

    _validate_schema(raw)

    try:
        profile_id = require_safe_identifier(raw["profile"]["id"])
    except (KeyError, TypeError, ValueError) as error:
        raise ProfileError("unsafe_profile_id") from error

    workspace_value = raw["workspace"]["root"]
    workspace_path = Path(workspace_value)
    if workspace_path.is_absolute():
        raise ProfileError("absolute_workspace_root")

    workspace_root = (source_path.parent / workspace_path).resolve()
    _require_within(source_path.parent.resolve(), workspace_root, "workspace_root")
    profile = Profile(
        id=profile_id,
        source_path=source_path,
        workspace_root=workspace_root,
        data=raw,
    )
    _validate_profile_paths(profile)
    return profile


def _validate_schema(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ProfileError("profile_must_be_object")

    try:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProfileError("profile_schema_error") from error

    errors = sorted(
        Draft202012Validator(schema).iter_errors(raw),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "root"
        raise ProfileError(f"profile_validation:{location}")


def _validate_profile_paths(profile: Profile) -> None:
    for field_path in _PATH_FIELDS:
        value = _nested_value(profile.data, field_path)
        if value is not None:
            _resolve_profile_path(profile, value, ".".join(field_path))

    for route in profile.data.get("routes", []):
        value = route["action"].get("config")
        if value is not None:
            _resolve_profile_path(profile, value, f"routes.{route['id']}.action.config")


def _resolve_profile_path(profile: Profile, value: str, field: str) -> Path:
    candidate_value = Path(value)
    if candidate_value.is_absolute():
        raise ProfileError(f"absolute_path:{field}")
    try:
        return profile.resolve_path(value)
    except (OSError, TypeError, ValueError) as error:
        raise ProfileError(f"path_escapes_workspace:{field}") from error


def _nested_value(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _require_within(root: Path, candidate: Path, field: str) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ProfileError(f"path_escapes_profile:{field}") from error
