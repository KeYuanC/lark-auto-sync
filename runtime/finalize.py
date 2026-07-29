"""Deterministic queue finalization with publication and receipt gates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import csv
import yaml

from runtime.adapters.csv_update import append_record, update_unique_row
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
        routed_job = _prepare_csv_match_counts(profile, job, extracted)
        decisions = Router(profile).decide(routed_job, extracted)
        csv_destinations = _apply_csv_routes(profile, decisions, extracted)
        result = _publish(profile, routed_job, markdown_path, decisions)
        result["csv_destinations"] = csv_destinations
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


def _prepare_csv_match_counts(profile: Any, job: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(job)
    counts = dict(prepared.get("csv_match_counts") or {})
    for route in profile.data.get("routes", []):
        if not isinstance(route, dict) or not _has_csv_predicate(route.get("match")):
            continue
        action = route.get("action")
        if not isinstance(action, dict) or action.get("adapter") not in {"csv_update", "csv_append"}:
            continue
        config = _load_mapping(profile, action.get("config"))
        counts[str(route["id"])] = _route_match_count(profile, config, extracted)
    prepared["csv_match_counts"] = counts
    return prepared


def _has_csv_predicate(match: Any) -> bool:
    predicates = match if isinstance(match, list) else [match]
    return any(isinstance(predicate, dict) and predicate.get("predicate") in {"csv_unique_row", "csv_row_missing"} for predicate in predicates)


def _route_match_count(profile: Any, config: dict[str, Any], extracted: dict[str, Any]) -> int:
    identity = config.get("identity")
    if not isinstance(identity, dict):
        return 0
    rows, headers = _read_csv(profile.resolve_path(str(config.get("path") or "")))
    participants = _participants(extracted)
    counts: list[int] = []
    for participant in participants:
        resolved = _expand_mapping(identity, extracted, participant)
        if set(resolved) - set(headers):
            return 2
        counts.append(sum(1 for row in rows if all(row.get(key) == value for key, value in resolved.items())))
    return 1 if counts and all(count == 1 for count in counts) else 0 if counts and all(count == 0 for count in counts) else 2


def _apply_csv_routes(profile: Any, decisions: list[Any], extracted: dict[str, Any]) -> list[str]:
    destinations: list[str] = []
    for decision in decisions:
        action = decision.action
        adapter = action.get("adapter")
        if adapter not in {"csv_update", "csv_append"}:
            continue
        config = _load_mapping(profile, action.get("config"))
        path = profile.resolve_path(str(config.get("path") or ""))
        headers = config.get("expected_headers")
        if not isinstance(headers, list) or not all(isinstance(value, str) for value in headers):
            raise FinalizeError("csv_expected_headers_required")
        for participant in _participants(extracted):
            if adapter == "csv_update":
                identity = _expand_mapping(_mapping(config, "identity"), extracted, participant)
                changes = _expand_mapping(_mapping(config, "changes"), extracted, participant)
                update_unique_row(path, identity, changes, headers)
            else:
                record = _expand_mapping(_mapping(config, "record"), extracted, participant)
                append_record(path, record, headers)
            destinations.append(str(path))
    return list(dict.fromkeys(destinations))


def _load_mapping(profile: Any, configured: Any) -> dict[str, Any]:
    if not isinstance(configured, str) or not configured:
        raise FinalizeError("csv_mapping_required")
    path = profile.resolve_path(configured)
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise FinalizeError("csv_mapping_load_failed") from error
    if not isinstance(value, dict):
        raise FinalizeError("csv_mapping_invalid")
    return value


def _mapping(config: dict[str, Any], key: str) -> dict[str, str]:
    value = config.get(key)
    if not isinstance(value, dict) or not value or not all(isinstance(column, str) and isinstance(template, str) for column, template in value.items()):
        raise FinalizeError("csv_mapping_invalid")
    return value


def _participants(extracted: dict[str, Any]) -> list[str]:
    participants = extracted.get("participants")
    if not isinstance(participants, list) or not participants or not all(isinstance(value, str) and value for value in participants):
        raise FinalizeError("csv_participants_required")
    return participants


def _expand_mapping(mapping: dict[str, str], extracted: dict[str, Any], participant: str) -> dict[str, str]:
    context = {key: _value_to_text(value) for key, value in extracted.items()}
    context["participant"] = participant
    resolved: dict[str, str] = {}
    for column, template in mapping.items():
        value = template
        for key, replacement in context.items():
            value = value.replace("{{" + key + "}}", replacement)
        if "{{" in value or "}}" in value:
            raise FinalizeError("csv_mapping_placeholder_unknown")
        resolved[column] = value
    return resolved


def _value_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return "" if value is None else str(value)


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise FinalizeError("csv_headers_missing")
            return list(reader), list(reader.fieldnames)
    except (OSError, UnicodeError, csv.Error) as error:
        raise FinalizeError("csv_read_failed") from error


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
