"""Collect allowlisted Lark attachments after an explicit bot mention."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping, Protocol

from runtime.converter import ConversionError, convert_attachment
from runtime.models import Profile
from runtime.queue import Queue, QueueConflictError


_SUPPORTED_SUFFIXES = {".txt", ".md", ".docx", ".doc"}
_MAX_BUFFERED_PER_PARTY = 100
_INVALID_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")


class CollectorError(RuntimeError):
    """A message passed intake validation but could not be safely staged."""


class LarkClientProtocol(Protocol):
    def recent_messages(self, chat_id: str, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def download_file(self, file_key: str, destination: Path) -> None: ...


@dataclass(frozen=True)
class _Mention:
    message: dict[str, Any]
    created_at: float


@dataclass(frozen=True)
class _Attachment:
    message: dict[str, Any]
    created_at: float
    file_key: str
    filename: str


class Collector:
    """Pair bot mentions and files before enqueueing an attachment conversion job."""

    def __init__(self, profile: Profile, client: LarkClientProtocol, queue: Queue | None = None) -> None:
        self.profile = profile
        self.client = client
        self.queue = queue or Queue(profile)
        source = profile.data.get("source", {})
        self.chat_ids = frozenset(source.get("chat_ids", ()))
        self.bot_name = source.get("bot_name", "")
        self.window_seconds = source.get("mention_window_seconds", 180)
        configured_types = source.get("attachment_types", ("txt", "md", "docx", "doc"))
        self.allowed_suffixes = frozenset(f".{item.lower()}" for item in configured_types)
        if (
            not self.chat_ids
            or not isinstance(self.bot_name, str)
            or not self.bot_name
            or not isinstance(self.window_seconds, int)
            or self.window_seconds < 1
            or not self.allowed_suffixes <= _SUPPORTED_SUFFIXES
        ):
            raise CollectorError("invalid_collector_profile")
        self._mentions: dict[tuple[str, str], list[_Mention]] = {}
        self._attachments: dict[tuple[str, str], list[_Attachment]] = {}

    @classmethod
    def for_test(
        cls,
        root: Path,
        client: LarkClientProtocol,
        *,
        bot_name: str = "Sync Bot",
        window_seconds: int = 180,
    ) -> "Collector":
        root = Path(root).resolve()
        profile = Profile(
            "test",
            root / "profile.yaml",
            root,
            {
                "workspace": {"state_directory": ".state"},
                "source": {
                    "chat_ids": ["oc_demo"],
                    "bot_name": bot_name,
                    "mention_window_seconds": window_seconds,
                    "attachment_types": ["txt", "md", "docx", "doc"],
                },
            },
        )
        return cls(profile, client)

    def scan_once(self) -> int:
        count = 0
        for chat_id in sorted(self.chat_ids):
            for message in self.client.recent_messages(chat_id):
                count += len(self.handle_message(message))
        return count

    def handle_message(self, message: dict[str, Any]) -> list[str]:
        """Record one event and enqueue a job when it completes a valid pair."""
        if not isinstance(message, dict):
            return []
        key = self._party_key(message)
        created_at = self._timestamp(message.get("created_at"))
        if key is None or created_at is None:
            return []

        jobs: list[str] = []
        mention = self._mention(message, created_at)
        if mention is not None:
            attachment = self._take_matching_attachment(key, created_at)
            if attachment is None:
                self._append(self._mentions, key, mention)
            else:
                jobs.append(self._stage_convert_enqueue(attachment))

        attachment = self._attachment(message, created_at)
        if attachment is not None:
            matching_mention = self._take_matching_mention(key, created_at)
            if matching_mention is None:
                self._append(self._attachments, key, attachment)
            else:
                jobs.append(self._stage_convert_enqueue(attachment))
        return jobs

    def _party_key(self, message: Mapping[str, Any]) -> tuple[str, str] | None:
        chat_id = message.get("chat_id")
        sender = message.get("sender")
        if chat_id not in self.chat_ids or not isinstance(sender, str) or not sender.strip():
            return None
        return chat_id, sender.strip()

    def _mention(self, message: dict[str, Any], created_at: float) -> _Mention | None:
        text = message.get("text")
        if not isinstance(text, str) or f"@{self.bot_name}" not in text:
            return None
        return _Mention(message, created_at)

    def _attachment(self, message: dict[str, Any], created_at: float) -> _Attachment | None:
        file_key = message.get("file_key")
        filename = message.get("filename")
        if not self._safe_file_key(file_key) or not isinstance(filename, str):
            return None
        sanitized = self._sanitize_filename(filename)
        if sanitized is None or Path(sanitized).suffix.lower() not in self.allowed_suffixes:
            return None
        return _Attachment(message, created_at, file_key, sanitized)

    def _take_matching_attachment(
        self, key: tuple[str, str], mention_at: float
    ) -> _Attachment | None:
        return self._take_match(self._attachments, key, mention_at)

    def _take_matching_mention(
        self, key: tuple[str, str], attachment_at: float
    ) -> _Mention | None:
        return self._take_match(self._mentions, key, attachment_at)

    def _take_match(self, records: dict[tuple[str, str], list[Any]], key: tuple[str, str], at: float) -> Any | None:
        candidates = records.get(key, [])
        match_index: int | None = None
        match_distance: float | None = None
        for index, candidate in enumerate(candidates):
            distance = abs(candidate.created_at - at)
            if distance <= self.window_seconds and (match_distance is None or distance < match_distance):
                match_index = index
                match_distance = distance
        if match_index is None:
            return None
        match = candidates.pop(match_index)
        if not candidates:
            records.pop(key, None)
        return match

    @staticmethod
    def _append(records: dict[tuple[str, str], list[Any]], key: tuple[str, str], value: Any) -> None:
        entries = records.setdefault(key, [])
        entries.append(value)
        if len(entries) > _MAX_BUFFERED_PER_PARTY:
            del entries[:-_MAX_BUFFERED_PER_PARTY]

    def _stage_convert_enqueue(self, attachment: _Attachment) -> str:
        message_id = attachment.message.get("id")
        if not self._safe_file_key(message_id):
            raise CollectorError("invalid_message_id")
        job_id = self._job_id(message_id, attachment.file_key)
        if (
            (self.queue.queue_root / f"{job_id}.json").is_file()
            or (self.queue.done_root / f"{job_id}.json").is_file()
        ):
            return job_id
        source_path = self.queue.staging_root / f"{job_id}_{attachment.filename}"
        markdown_path = self.queue.staging_root / f"{job_id}_markdown.md"
        try:
            self.client.download_file(attachment.file_key, source_path)
            convert_attachment(source_path, markdown_path)
            self.queue.enqueue(
                {
                    "job_id": job_id,
                    "message_id": message_id,
                    "chat_id": attachment.message["chat_id"],
                    "source_create_time": attachment.message["created_at"],
                    "sender_name": attachment.message["sender"],
                    "filename": attachment.filename,
                    "file_key": attachment.file_key,
                    "source_path": str(source_path),
                    "markdown_path": str(markdown_path),
                    "profile_id": self.profile.id,
                }
            )
        except QueueConflictError:
            return job_id
        except (ConversionError, OSError, ValueError) as error:
            raise CollectorError("attachment_staging_failed") from error
        return job_id

    @staticmethod
    def _job_id(message_id: str, file_key: str) -> str:
        digest = hashlib.sha256(f"{message_id}\0{file_key}".encode("utf-8")).hexdigest()
        return f"job_{digest[:32]}"

    @staticmethod
    def _safe_file_key(value: object) -> bool:
        return (
            isinstance(value, str)
            and 0 < len(value) <= 256
            and all(character.isalnum() or character in "_-" for character in value)
        )

    @staticmethod
    def _sanitize_filename(value: str) -> str | None:
        name = value.replace("\\", "/").split("/")[-1].strip()
        name = _INVALID_FILENAME.sub("_", name).strip(". ")
        if not name or len(name) > 160 or name in {".", ".."}:
            return None
        return name

    @staticmethod
    def _timestamp(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
        return None
