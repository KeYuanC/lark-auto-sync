from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any, Mapping

from runtime.state import (
    StatePaths,
    append_audit_record,
    create_state_paths,
    read_json,
    redact_error,
    relative_path,
    write_json_atomic,
)


class QueueError(RuntimeError):
    """Base class for profile-local queue errors."""


class QueueBusyError(QueueError):
    """Raised when a job has already been claimed."""


class QueueNotFoundError(QueueError):
    """Raised when a requested queued job does not exist."""


class QueueConflictError(QueueError):
    """Raised when enqueueing would overwrite an existing job."""


_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class Queue:
    def __init__(self, profile: Any) -> None:
        self.profile = profile
        self.paths: StatePaths = create_state_paths(profile)
        self.root = self.paths.root
        self.staging_root = self.paths.staging
        self.queue_root = self.paths.queue
        self.done_root = self.paths.done
        self.locks_root = self.paths.locks
        self.extractions_root = self.paths.extractions
        self.logs_root = self.paths.logs
        self.workspace_root = Path(profile.workspace_root).resolve()

    def enqueue(self, job: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(job)
        job_id = self._require_job_id(payload.get("job_id"))
        queue_path = self._queue_path(job_id)
        if queue_path.exists() or self._done_path(job_id).exists():
            raise QueueConflictError("job_already_exists")

        payload["job_id"] = job_id
        payload["status"] = "pending"
        payload["attempts"] = self._attempts(payload.get("attempts"))
        payload.setdefault("created_at", self._now())
        payload.pop("claimed_at", None)
        write_json_atomic(queue_path, payload)
        self._audit("enqueued", job_id, paths=[queue_path])
        return payload

    def list_pending(self) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for path in sorted(self.queue_root.glob("*.json")):
            job = read_json(path)
            if job.get("status") == "pending":
                pending.append(job)
        return pending

    def claim(self, job_id: str) -> dict[str, Any]:
        job_id = self._require_job_id(job_id)
        queue_path = self._queue_path(job_id)
        if not queue_path.is_file():
            raise QueueNotFoundError("job_not_found")

        lock_path = self._lock_path(job_id)
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise QueueBusyError("job_already_claimed") from error
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(job_id)
                handle.write("\n")

        try:
            job = read_json(queue_path)
            if job.get("status") != "pending":
                raise QueueBusyError("job_not_pending")
            job["status"] = "processing"
            job["claimed_at"] = self._now()
            write_json_atomic(queue_path, job)
        except Exception:
            self._remove_lock(lock_path)
            raise

        self._audit("claimed", job_id, paths=[queue_path, lock_path])
        return job

    def complete(self, job_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
        job_id = self._require_job_id(job_id)
        queue_path = self._queue_path(job_id)
        lock_path = self._lock_path(job_id)
        if not queue_path.is_file():
            raise QueueNotFoundError("job_not_found")
        if not lock_path.is_file():
            raise QueueBusyError("job_not_claimed")

        job = read_json(queue_path)
        if job.get("status") != "processing":
            raise QueueBusyError("job_not_claimed")

        completed = dict(job)
        completed["status"] = "done"
        completed["completed_at"] = self._now()
        completed["result"] = dict(result)
        done_path = self._done_path(job_id)
        write_json_atomic(done_path, completed)
        queue_path.unlink()
        self._remove_lock(lock_path)
        self._audit("completed", job_id, paths=[done_path])
        return completed

    def fail(self, job_id: str, reason: object) -> dict[str, Any]:
        job_id = self._require_job_id(job_id)
        queue_path = self._queue_path(job_id)
        lock_path = self._lock_path(job_id)
        if not queue_path.is_file():
            raise QueueNotFoundError("job_not_found")
        if not lock_path.is_file():
            raise QueueBusyError("job_not_claimed")

        job = read_json(queue_path)
        if job.get("status") != "processing":
            raise QueueBusyError("job_not_claimed")

        job["status"] = "pending"
        job["attempts"] = self._attempts(job.get("attempts")) + 1
        job["last_error"] = redact_error(reason)
        job.pop("claimed_at", None)
        write_json_atomic(queue_path, job)
        self._remove_lock(lock_path)
        self._audit("failed", job_id, paths=[queue_path], error=reason, attempts=job["attempts"])
        return job

    def _queue_path(self, job_id: str) -> Path:
        return self.queue_root / f"{job_id}.json"

    def _done_path(self, job_id: str) -> Path:
        return self.done_root / f"{job_id}.json"

    def _lock_path(self, job_id: str) -> Path:
        return self.locks_root / f"{job_id}.lock"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _attempts(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        return value

    @staticmethod
    def _require_job_id(value: Any) -> str:
        if not isinstance(value, str) or not _SAFE_JOB_ID.fullmatch(value):
            raise QueueError("unsafe_job_id")
        return value

    @staticmethod
    def _remove_lock(lock_path: Path) -> None:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass

    def _audit(
        self,
        event: str,
        job_id: str,
        *,
        paths: list[Path],
        error: object | None = None,
        attempts: int | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "event": event,
            "job_id": job_id,
            "paths": [relative_path(path, self.workspace_root) for path in paths],
        }
        if error is not None:
            record["error"] = redact_error(error)
        if attempts is not None:
            record["attempts"] = attempts
        append_audit_record(self.paths, self.workspace_root, record)
