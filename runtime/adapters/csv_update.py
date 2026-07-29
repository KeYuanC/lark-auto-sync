"""Safe, narrow CSV row updates for profile-driven routes."""

from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


class CsvUpdateError(ValueError):
    """Base error for a CSV update that was not applied."""


class CsvHeaderError(CsvUpdateError):
    """The CSV header is not exactly the profile-approved header."""


class CsvAmbiguityError(CsvUpdateError):
    """The identity selected zero or more than one row."""


@dataclass(frozen=True)
class CsvUpdateResult:
    """The one row changed by :func:`update_unique_row`."""

    path: Path
    row_index: int
    updated: bool


def update_unique_row(
    path: Path,
    identity: dict[str, str],
    changes: dict[str, str],
    expected_headers: list[str],
) -> CsvUpdateResult:
    """Atomically update exactly one approved row in a UTF-8 CSV.

    ``identity`` and ``changes`` are intentionally limited to declared columns. A
    zero or multi-row match is never guessed, and the source is left untouched on
    every validation error.
    """
    csv_path = Path(path)
    headers = _validate_inputs(identity, changes, expected_headers)
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, restkey="__extra_columns__")
            if reader.fieldnames != headers:
                raise CsvHeaderError("csv_headers_do_not_match_profile")
            rows = list(reader)
    except CsvUpdateError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise CsvUpdateError("csv_read_error") from error

    _validate_rows(rows, headers)
    matched_indexes = [
        index
        for index, row in enumerate(rows)
        if all(row[column] == value for column, value in identity.items())
    ]
    if len(matched_indexes) != 1:
        raise CsvAmbiguityError("csv_identity_requires_exactly_one_match")

    row_index = matched_indexes[0]
    rows[row_index].update(changes)
    _replace_atomically(csv_path, headers, rows)
    return CsvUpdateResult(path=csv_path, row_index=row_index, updated=True)


def _validate_inputs(
    identity: dict[str, str], changes: dict[str, str], expected_headers: list[str]
) -> list[str]:
    if not isinstance(expected_headers, list) or not expected_headers:
        raise CsvHeaderError("expected_headers_required")
    if any(not isinstance(header, str) or not header for header in expected_headers):
        raise CsvHeaderError("invalid_expected_header")
    if len(set(expected_headers)) != len(expected_headers):
        raise CsvHeaderError("duplicate_expected_header")
    if not isinstance(identity, dict) or not identity:
        raise CsvAmbiguityError("identity_required")
    if not isinstance(changes, dict) or not changes:
        raise CsvUpdateError("changes_required")

    for mapping, label in ((identity, "identity"), (changes, "changes")):
        for column, value in mapping.items():
            if column not in expected_headers:
                raise CsvHeaderError(f"unknown_{label}_column")
            if not isinstance(value, str):
                raise CsvUpdateError(f"{label}_values_must_be_strings")
    return list(expected_headers)


def _validate_rows(rows: list[dict[str, str | list[str] | None]], headers: list[str]) -> None:
    for row in rows:
        if row.get("__extra_columns__") is not None:
            raise CsvUpdateError("csv_row_has_extra_columns")
        if any(row.get(header) is None for header in headers):
            raise CsvUpdateError("csv_row_is_missing_columns")


def _replace_atomically(path: Path, headers: list[str], rows: list[dict[str, str | list[str] | None]]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
            writer.writeheader()
            writer.writerows(
                {header: row[header] for header in headers}
                for row in rows
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except (OSError, UnicodeError, csv.Error) as error:
        raise CsvUpdateError("csv_write_error") from error
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
