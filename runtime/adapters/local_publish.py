"""Atomic, digest-verified local publication for approved output files."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


@dataclass(frozen=True)
class PublishResult:
    """The stable result returned by local and GitHub publishing adapters."""

    ok: bool
    status: str
    sha256: str | None = None
    destination: str | None = None
    commit: str | None = None
    files: tuple[str, ...] = ()
    error: str | None = None
    retryable: bool = False


class LocalPublishError(ValueError):
    """The approved source or destination cannot be published safely."""


def publish_local(source: Path, destination: Path) -> PublishResult:
    """Copy one already allowlisted file atomically and verify its digest.

    Callers must derive ``destination`` from ``Profile.resolve_path``. The
    two-argument public interface deliberately accepts no arbitrary root, so
    the Profile-aware finalizer remains the single authority for destination
    allowlisting.
    """
    source_path = Path(source)
    destination_path = Path(destination)
    if source_path.is_symlink() or not source_path.is_file():
        raise LocalPublishError("source_must_be_regular_file")
    if not destination_path.name or destination_path.name in {".", ".."}:
        raise LocalPublishError("invalid_destination")

    source_digest = _sha256(source_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with source_path.open("rb") as source_stream, NamedTemporaryFile(
            mode="wb",
            dir=destination_path.parent,
            prefix=f".{destination_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_stream:
            temporary_path = Path(temporary_stream.name)
            for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                temporary_stream.write(chunk)
            temporary_stream.flush()
            os.fsync(temporary_stream.fileno())
        if _sha256(temporary_path) != source_digest:
            raise LocalPublishError("temporary_digest_mismatch")
        os.replace(temporary_path, destination_path)
    except OSError as error:
        raise LocalPublishError("local_publish_failed") from error
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    if _sha256(destination_path) != source_digest:
        raise LocalPublishError("destination_digest_mismatch")
    return PublishResult(
        ok=True,
        status="published",
        sha256=source_digest,
        destination=str(destination_path),
        files=(destination_path.name,),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
