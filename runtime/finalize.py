"""Deterministic queue finalization with publication and receipt gates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.adapters.github_publish import publish_github
from runtime.adapters.lark_receipt import ReceiptError, send_receipt
from runtime.adapters.local_publish import publish_local
from runtime.extraction import ExtractionError, validate_extraction
from runtime.queue import Queue, QueueError
from runtime.router import Router, RouteConfigurationError


def finalize_job(profile: Any, job_id: str, extraction_path: Path, client: Any | None = None) -> dict[str, Any]:
    """Finalize one claimed job or leave it retryable with a structured reason."""
    queue = Queue(profile)
    try:
        job = queue.claim(job_id)
    except QueueError as error:
        return _result(False, "queue_claim_failed", error=str(error))

    try:
        if job.get("intake_error"):
            raise FinalizeError(str(job["intake_error"]))
        markdown_path = _existing_path(job.get("markdown_path"), "markdown_missing")
        payload = _load_json(extraction_path)
        schema = _load_schema(profile)
        markdown = markdown_path.read_text(encoding="utf-8")
        extracted = validate_extraction(
            markdown,
            payload,
            schema,
            filename=str(job.get("filename") or ""),
            participant_sources=profile.data.get("processing", {}).get("participant_sources", ()),
        )
        decisions = Router(profile).decide(job, extracted)
        result = _publish(profile, job, markdown_path, decisions)
        _send_configured_receipt(profile, client, job, result)
        _cleanup_source(queue, job)
        queue.complete(job_id, result)
        return _result(True, "completed", **result)
    except (FinalizeError, ExtractionError, RouteConfigurationError, ReceiptError, OSError, ValueError) as error:
        try:
            queue.fail(job_id, error)
        except QueueError:
            pass
        return _result(False, "retryable_failure", reason=_safe_reason(error))


class FinalizeError(ValueError):
    """A required finalization gate did not pass."""


def _publish(profile: Any, job: dict[str, Any], markdown_path: Path, decisions: list[Any]) -> dict[str, Any]:
    publish = profile.data.get("publish", {})
    local_destinations: list[str] = []
    github_destinations: list[str] = []
    commit = ""
    local = publish.get("local") if isinstance(publish, dict) else None
    published_file = markdown_path
    if isinstance(local, dict) and isinstance(local.get("directory"), str):
        destination = profile.resolve_path(local["directory"]) / markdown_path.name
        local_result = publish_local(markdown_path, destination)
        if not local_result.ok:
            raise FinalizeError("local_publish_failed")
        published_file = destination
        local_destinations.append(str(destination))

    github = publish.get("github") if isinstance(publish, dict) else None
    if isinstance(github, dict):
        github_result = publish_github(profile, [published_file], f"同步：{markdown_path.name}")
        if not github_result.ok:
            raise FinalizeError(github_result.error or github_result.status)
        github_destinations.extend(github_result.files)
        commit = github_result.commit or ""

    return {
        "filename": str(job.get("filename") or markdown_path.name),
        "local_destinations": local_destinations,
        "github_destinations": github_destinations,
        "routes": [decision.route_id for decision in decisions],
        "paused_participants": [],
        "commit": commit,
    }


def _send_configured_receipt(profile: Any, client: Any | None, job: dict[str, Any], result: dict[str, Any]) -> None:
    publish = profile.data.get("publish", {})
    receipt = publish.get("lark_receipt") if isinstance(publish, dict) else None
    if not isinstance(receipt, dict) or not receipt.get("enabled"):
        return
    if client is None:
        from runtime.lark_client import LarkClient

        client = LarkClient()
    template_path = profile.resolve_path(str(receipt.get("template") or ""))
    template = template_path.read_text(encoding="utf-8")
    message_id = str(job.get("message_id") or "")
    if not message_id:
        raise FinalizeError("source_message_missing")
    send_receipt(client, message_id, template, result)


def _cleanup_source(queue: Queue, job: dict[str, Any]) -> None:
    raw_source = job.get("source_path")
    if not isinstance(raw_source, str) or not raw_source:
        return
    source = Path(raw_source).resolve()
    try:
        source.relative_to(queue.staging_root.resolve())
    except ValueError as error:
        raise FinalizeError("unsafe_source_cleanup") from error
    if source.is_file() and not source.is_symlink():
        source.unlink()


def _load_schema(profile: Any) -> dict[str, Any]:
    processing = profile.data.get("processing", {})
    configured = processing.get("extraction_schema") if isinstance(processing, dict) else None
    path = profile.resolve_path(configured) if isinstance(configured, str) else Path(__file__).resolve().parents[1] / "schemas" / "extraction.schema.json"
    return _load_json(path)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FinalizeError("json_load_failed") from error
    if not isinstance(value, dict):
        raise FinalizeError("json_object_required")
    return value


def _existing_path(value: Any, reason: str) -> Path:
    path = Path(str(value or ""))
    if path.is_symlink() or not path.is_file():
        raise FinalizeError(reason)
    return path


def _safe_reason(error: Exception) -> str:
    return str(error).replace("\r", " ").replace("\n", " ")[:200] or "finalize_failed"


def _result(ok: bool, status: str, **details: Any) -> dict[str, Any]:
    return {"ok": ok, "status": status, **details}
