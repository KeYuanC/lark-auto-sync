"""Safe pairing and staging of explicitly mentioned Lark attachments."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from runtime.converter import ConversionError, convert_attachment
from runtime.lark_client import LarkClientError
from runtime.models import Profile
from runtime.queue import Queue, QueueConflictError


_DEFAULT_TYPES = frozenset({"txt", "md", "docx", "doc"})
_FILE_MARKER = re.compile(r'<file\b[^>]*\bkey=["\'](?P<key>[^"\']+)["\'][^>]*(?:\bname|\bfile_name)=["\'](?P<name>[^"\']+)["\'][^>]*>', re.IGNORECASE)
_SAFE_FILE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")


class CollectorError(ValueError):
    """A message could not safely enter the collector."""


@dataclass(frozen=True)
class _Mention:
    message_id: str
    chat_id: str
    sender: str
    created_at: float


@dataclass(frozen=True)
class _File:
    message_id: str
    chat_id: str
    sender: str
    sender_name: str
    created_at: float
    file_key: str
    filename: str


class Collector:
    def __init__(self, profile: Profile, client: Any) -> None:
        self.profile = profile
        self.client = client
        self.queue = Queue(profile)
        source = profile.data.get("source", {})
        self._chat_ids = frozenset(str(chat_id) for chat_id in source.get("chat_ids", []))
        self._bot_name = str(source.get("bot_name", ""))
        self._window_seconds = int(source.get("mention_window_seconds", 180))
        self._attachment_types = frozenset(str(value).lower().lstrip(".") for value in source.get("attachment_types", _DEFAULT_TYPES))
        self._mentions: dict[tuple[str, str], list[_Mention]] = {}
        self._files: dict[tuple[str, str], list[_File]] = {}
        self._seen_files: set[str] = set()

    @classmethod
    def for_test(cls, root: Path, client: Any, *, bot_name: str, window_seconds: int) -> "Collector":
        root = Path(root).resolve()
        profile = Profile(
            id="test",
            source_path=root / "profile.yaml",
            workspace_root=root,
            data={
                "workspace": {"state_directory": ".state"},
                "source": {
                    "chat_ids": ["oc_demo"],
                    "bot_name": bot_name,
                    "mention_window_seconds": window_seconds,
                    "attachment_types": sorted(_DEFAULT_TYPES),
                },
            },
        )
        return cls(profile, client)

    def handle_message(self, message: dict) -> list[str]:
        """Process one message as metadata only and return newly queued job IDs."""
        if not isinstance(message, dict):
            return []
        common = self._common_metadata(message)
        if common is None:
            return []
        message_id, chat_id, sender, sender_name, created_at = common
        if chat_id not in self._chat_ids:
            return []
        pair_key = (chat_id, sender)
        queued: list[str] = []

        if self._has_explicit_mention(message):
            mention = _Mention(message_id, chat_id, sender, created_at)
            self._mentions.setdefault(pair_key, []).append(mention)
            for pending_file in list(self._files.get(pair_key, [])):
                if abs(pending_file.created_at - created_at) <= self._window_seconds:
                    queued.extend(self._stage_convert_enqueue(pending_file))
                    self._files[pair_key].remove(pending_file)

        file_metadata = self._file_metadata(message)
        if file_metadata is not None and message_id not in self._seen_files:
            file_key, filename = file_metadata
            if self._is_supported_filename(filename):
                attachment = _File(message_id, chat_id, sender, sender_name, created_at, file_key, filename)
                self._seen_files.add(message_id)
                mention = self._latest_matching_mention(pair_key, created_at)
                if mention is None:
                    self._files.setdefault(pair_key, []).append(attachment)
                else:
                    queued.extend(self._stage_convert_enqueue(attachment))
        return queued

    def scan_once(self) -> int:
        queued = 0
        for chat_id in sorted(self._chat_ids):
            messages = self.client.recent_messages(chat_id, limit=100)
            if not isinstance(messages, list):
                continue
            ordered = sorted((message for message in messages if isinstance(message, dict)), key=self._sort_key)
            for message in ordered:
                queued += len(self.handle_message(message))
        return queued

    def _stage_convert_enqueue(self, attachment: _File) -> list[str]:
        job_id = f"msg_{sha256(attachment.message_id.encode('utf-8')).hexdigest()[:32]}"
        root = self.queue.staging_root / job_id
        source_path = root / "attachment" / self._sanitize_filename(attachment.filename)
        markdown_path = root / "markdown" / f"{source_path.stem}.md"
        try:
            source_path.parent.mkdir(parents=True, exist_ok=True)
            bind = getattr(self.client, "_remember_file_message", None)
            if callable(bind):
                bind(attachment.file_key, attachment.message_id)
            self.client.download_file(attachment.file_key, source_path)
        except (LarkClientError, OSError, ValueError):
            return self._enqueue_intake_failure(
                job_id, attachment, source_path, markdown_path, "download_failed"
            )

        if not source_path.is_file():
            return self._enqueue_intake_failure(
                job_id, attachment, source_path, markdown_path, "download_output_missing"
            )

        try:
            result = convert_attachment(source_path, markdown_path)
        except (ConversionError, OSError, ValueError):
            return self._enqueue_intake_failure(
                job_id, attachment, source_path, markdown_path, "conversion_failed"
            )

        try:
            job = self.queue.enqueue(
                self._job_payload(
                    job_id,
                    attachment,
                    source_path,
                    result.output_path,
                )
            )
        except QueueConflictError:
            return []
        return [str(job["job_id"])]

    def _enqueue_intake_failure(
        self,
        job_id: str,
        attachment: _File,
        source_path: Path,
        markdown_path: Path,
        intake_error: str,
    ) -> list[str]:
        payload = self._job_payload(job_id, attachment, source_path, markdown_path)
        if not source_path.is_file():
            payload.pop("source_path", None)
        payload["intake_error"] = intake_error
        try:
            job = self.queue.enqueue(payload)
        except QueueConflictError:
            return []
        return [str(job["job_id"])]

    def _job_payload(
        self,
        job_id: str,
        attachment: _File,
        source_path: Path,
        markdown_path: Path,
    ) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "message_id": attachment.message_id,
            "source_create_time": attachment.created_at,
            "sender_name": attachment.sender_name,
            "filename": source_path.name,
            "source_path": str(source_path),
            "markdown_path": str(markdown_path),
            "profile_id": self.profile.id,
        }

    def _latest_matching_mention(self, pair_key: tuple[str, str], created_at: float) -> _Mention | None:
        candidates = [mention for mention in self._mentions.get(pair_key, []) if abs(created_at - mention.created_at) <= self._window_seconds]
        return max(candidates, key=lambda mention: mention.created_at) if candidates else None

    def _has_explicit_mention(self, message: Mapping[str, Any]) -> bool:
        if not self._bot_name:
            return False
        mentions = message.get("mentions")
        if isinstance(mentions, list):
            for mention in mentions:
                if isinstance(mention, Mapping) and str(mention.get("name", "")) == self._bot_name:
                    return True
        text = self._message_text(message)
        return bool(re.search(rf"(?<!\w)@{re.escape(self._bot_name)}(?!\w)", text))

    @staticmethod
    def _message_text(message: Mapping[str, Any]) -> str:
        for key in ("text", "content"):
            value = message.get(key)
            if isinstance(value, str):
                return value
        return ""

    def _common_metadata(self, message: Mapping[str, Any]) -> tuple[str, str, str, str, float] | None:
        message_id = str(message.get("id") or message.get("message_id") or "")
        chat_id = str(message.get("chat_id") or "")
        sender_value = message.get("sender")
        sender = self._sender_identifier(sender_value, message)
        sender_name = self._sender_name(sender_value, message, sender)
        created_at = self._timestamp(message.get("created_at", message.get("create_time")))
        if not message_id or not chat_id or not sender or created_at is None:
            return None
        return message_id, chat_id, sender, sender_name, created_at

    @staticmethod
    def _sender_identifier(sender_value: Any, message: Mapping[str, Any]) -> str:
        if isinstance(sender_value, Mapping):
            for key in ("id", "open_id", "user_id"):
                if sender_value.get(key):
                    return str(sender_value[key])
        for key in ("sender_id", "sender_open_id"):
            if message.get(key):
                return str(message[key])
        return str(sender_value or "")

    @staticmethod
    def _sender_name(sender_value: Any, message: Mapping[str, Any], fallback: str) -> str:
        if message.get("sender_name"):
            return str(message["sender_name"])
        if isinstance(sender_value, Mapping):
            for key in ("name", "display_name"):
                if sender_value.get(key):
                    return str(sender_value[key])
        return fallback

    @staticmethod
    def _timestamp(value: Any) -> float | None:
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return None
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return timestamp if timestamp >= 0 else None

    def _file_metadata(self, message: Mapping[str, Any]) -> tuple[str, str] | None:
        file_key = message.get("file_key")
        filename = message.get("filename")
        if isinstance(file_key, str) and _SAFE_FILE_KEY.fullmatch(file_key) and isinstance(filename, str):
            return file_key, filename
        content = message.get("content")
        if not isinstance(content, str):
            return None
        marker = _FILE_MARKER.search(content)
        if marker and _SAFE_FILE_KEY.fullmatch(marker.group("key")):
            return marker.group("key"), marker.group("name")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, Mapping):
            key = parsed.get("file_key") or parsed.get("key")
            name = parsed.get("file_name") or parsed.get("name")
            if isinstance(key, str) and _SAFE_FILE_KEY.fullmatch(key) and isinstance(name, str):
                return key, name
        return None

    def _is_supported_filename(self, filename: str) -> bool:
        try:
            sanitized = self._sanitize_filename(filename)
        except CollectorError:
            return False
        return sanitized.rsplit(".", 1)[-1].lower() in self._attachment_types

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        raw = filename.strip()
        if not raw or Path(raw).name != raw or raw in {".", ".."} or any(ord(character) < 32 for character in raw):
            raise CollectorError("unsafe_filename")
        sanitized = re.sub(r'[<>:"/\\|?*]', "_", raw).rstrip(". ")
        if not sanitized or len(sanitized) > 180:
            raise CollectorError("unsafe_filename")
        return sanitized

    @staticmethod
    def _sort_key(message: Mapping[str, Any]) -> tuple[float, str]:
        timestamp = Collector._timestamp(message.get("created_at", message.get("create_time")))
        return (timestamp if timestamp is not None else float("inf"), str(message.get("id") or message.get("message_id") or ""))
