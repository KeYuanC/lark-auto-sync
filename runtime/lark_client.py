"""Restricted adapter for the small Lark CLI surface the collector needs."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any


_SAFE_REMOTE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")


class LarkClientError(RuntimeError):
    """A fixed Lark CLI operation could not be completed safely."""


class LarkClient:
    """A narrow, injectable adapter with no arbitrary command interface."""

    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable or self._discover_executable()
        self._file_messages: dict[str, str] = {}

    def recent_messages(self, chat_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        self._require_remote_id(chat_id, "chat_id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise LarkClientError("invalid_page_size")
        payload = self._run_cli(
            ("im", "+chat-messages-list", "--chat-id", chat_id, "--page-size", str(limit), "--format", "json", "--as", "user"),
            timeout=60,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        messages = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(messages, list):
            return []
        return [message for message in messages if isinstance(message, dict)]

    def download_file(self, file_key: str, destination: Path) -> None:
        self._require_remote_id(file_key, "file_key")
        message_id = self._file_messages.get(file_key)
        if message_id is None:
            raise LarkClientError("file_message_unknown")
        destination = Path(destination)
        if not destination.name or destination.name in {".", ".."}:
            raise LarkClientError("invalid_destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._run_cli(
            (
                "im",
                "+messages-resources-download",
                "--message-id",
                message_id,
                "--file-key",
                file_key,
                "--type",
                "file",
                "--output",
                str(destination),
                "--as",
                "user",
            ),
            timeout=120,
        )
        if not destination.is_file():
            raise LarkClientError("download_output_missing")

    def reply_message(self, message_id: str, text: str) -> dict[str, Any]:
        self._require_remote_id(message_id, "message_id")
        if not isinstance(text, str) or not text:
            raise LarkClientError("invalid_reply_text")
        return self._run_cli(
            ("im", "+messages-reply", "--message-id", message_id, "--text", text, "--as", "user", "--format", "json"),
            timeout=60,
        )

    def auth_status(self) -> dict[str, Any]:
        return self._run_cli(("auth", "status", "--json"), timeout=30)

    def _remember_file_message(self, file_key: str, message_id: str) -> None:
        """Bind a resource returned by Lark to its source message internally."""
        self._require_remote_id(file_key, "file_key")
        self._require_remote_id(message_id, "message_id")
        self._file_messages[file_key] = message_id

    @staticmethod
    def _discover_executable() -> str:
        found = shutil.which("lark-cli") or shutil.which("lark-cli.cmd")
        if found:
            return found
        raise LarkClientError("lark_cli_not_found")

    @staticmethod
    def _require_remote_id(value: str, field: str) -> None:
        if not isinstance(value, str) or not _SAFE_REMOTE_ID.fullmatch(value):
            raise LarkClientError(f"invalid_{field}")

    def _run_cli(self, arguments: tuple[str, ...], *, timeout: int) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                [self._executable, *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise LarkClientError("lark_cli_unavailable") from error
        output = completed.stdout.strip()
        if completed.returncode != 0:
            raise LarkClientError("lark_cli_failed")
        if not output:
            return {}
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as error:
            raise LarkClientError("lark_cli_invalid_json") from error
        if not isinstance(payload, dict):
            raise LarkClientError("lark_cli_invalid_json")
        return payload
