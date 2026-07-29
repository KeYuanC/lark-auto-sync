"""Safe, atomic updates for one uniquely identified CSV record."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Mapping


class CsvUpdateError(ValueError):
    """Base class for CSV safety and validation failures."""


class CsvHeaderError(CsvUpdateError):
    """The CSV columns differ from the Profile's exact expected header list."""


class CsvAmbiguityError(CsvUpdateError):
    """The identity did not select exactly one existing record."""


class CsvValueError(CsvUpdateError):
    """Identity or change data is not a string-to-string column mapping."""


@dataclass(frozen=True)
class CsvUpdateResult:
    path: Path
    row_index: int
    changed_fields: tuple[str, ...]
    updated: bool = True


def update_unique_row(
    path: Path,
    identity: dict[str, str],
    changes: dict[str, str],
    expected_headers: list[str],
    *,
    encoding: str = "utf-8-sig",
) -> CsvUpdateResult:
    """Atomically update one row after exact-header and uniqueness checks.

    ``identity`` and ``changes`` are already explicitly mapped by the caller.
    This adapter never derives column names from extraction data and never
    appends a record when a unique update cannot be proved.
    """
    csv_path = Path(path)
    _validate_mappings(identity, changes, expected_headers)
    headers, rows = _read_csv(csv_path, encoding)
    if headers != expected_headers:
        raise CsvHeaderError("csv_headers_do_not_match")

    matches = [
        index
        for index, row in enumerate(rows)
        if all(row[column] == value for column, value in identity.items())
    ]
    if len(matches) != 1:
        raise CsvAmbiguityError("csv_identity_match_count:" + str(len(matches)))

    row_index = matches[0]
    changed_fields = tuple(column for column, value in changes.items() if rows[row_index][column] != value)
    rows[row_index].update(changes)
    _write_csv_atomic(csv_path, expected_headers, rows, encoding)
    return CsvUpdateResult(csv_path, row_index, changed_fields)


def _validate_mappings(
    identity: Mapping[str, str], changes: Mapping[str, str], expected_headers: list[str]
) -> None:
    if not isinstance(expected_headers, list) or not expected_headers:
        raise CsvHeaderError("expected_headers_required")
    if any(not isinstance(header, str) or not header for header in expected_headers):
        raise CsvHeaderError("invalid_expected_header")
    if len(set(expected_headers)) != len(expected_headers):
        raise CsvHeaderError("duplicate_expected_header")
    for mapping, label in ((identity, "identity"), (changes, "changes")):
        if not isinstance(mapping, Mapping) or (label == "identity" and not mapping):
            raise CsvValueError(f"{label}_mapping_required")
        for column, value in mapping.items():
            if column not in expected_headers:
                raise CsvHeaderError(f"unknown_{label}_column:{column}")
            if not isinstance(value, str):
                raise CsvValueError(f"non_string_{label}_value:{column}")


def _read_csv(path: Path, encoding: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding=encoding, newline="") as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames
            if headers is None:
                raise CsvHeaderError("csv_headers_missing")
            rows = list(reader)
    except CsvUpdateError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise CsvUpdateError("csv_read_failed") from error

    if any(header is None for header in headers):
        raise CsvHeaderError("csv_header_invalid")
    normalised_headers = list(headers)
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise CsvUpdateError("csv_row_malformed")
    return normalised_headers, rows


def _write_csv_atomic(
    path: Path, headers: list[str], rows: list[dict[str, str]], encoding: str
) -> None:
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_name = stream.name
            writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n", extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_name, path)
    except (OSError, UnicodeError, csv.Error) as error:
        raise CsvUpdateError("csv_write_failed") from error
    finally:
        if temporary_name is not None:
            temporary_path = Path(temporary_name)
            if temporary_path.exists():
                temporary_path.unlink()
