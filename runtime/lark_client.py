"""Narrow, injectable adapter around the locally installed ``lark-cli``."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from typing import Any


class LarkClientError(RuntimeError):
    """The fixed local Lark client operation could not be completed."""


class LarkClient:
    """Expose only the Lark operations needed by the sync runtime.

    The class intentionally has no public generic command method. All CLI
    arguments are fixed by one of the methods below, with message and file
    identifiers treated as data rather than command fragments.
    """

    def __init__(self, executable: Path | None = None, *, timeout_seconds: int = 30) -> None:
        if timeout_seconds < 1:
            raise ValueError("invalid_timeout")
        self.executable = Path(executable) if executable is not None else self._discover()
        self.timeout_seconds = timeout_seconds

    def recent_messages(self, chat_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(chat_id, str) or not chat_id or limit < 1 or limit > 500:
            raise ValueError("invalid_recent_messages_request")
        result = self._run_json(
            ["im", "message", "list", "--chat-id", chat_id, "--limit", str(limit), "--json"]
        )
        if isinstance(result, list):
            messages = result
        elif isinstance(result, dict):
            messages = result.get("items", result.get("data", []))
        else:
            raise LarkClientError("invalid_lark_response")
        if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
            raise LarkClientError("invalid_lark_response")
        return messages

    def download_file(self, file_key: str, destination: Path) -> None:
        if not _safe_identifier(file_key):
            raise ValueError("invalid_file_key")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            ["im", "file", "download", "--file-key", file_key, "--output", str(destination)]
        )
        if not destination.is_file():
            raise LarkClientError("download_output_missing")

    def reply_message(self, message_id: str, text: str) -> dict[str, Any]:
        if not _safe_identifier(message_id) or not isinstance(text, str) or not text:
            raise ValueError("invalid_reply_request")
        result = self._run_json(
            ["im", "message", "reply", "--message-id", message_id, "--text", text, "--json"]
        )
        if not isinstance(result, dict):
            raise LarkClientError("invalid_lark_response")
        return result

    def auth_status(self) -> dict[str, Any]:
        result = self._run_json(["auth", "status", "--json"])
        if not isinstance(result, dict):
            raise LarkClientError("invalid_lark_response")
        return result

    @staticmethod
    def _discover() -> Path:
        for name in ("lark-cli.exe", "lark-cli"):
            found = shutil.which(name)
            if found:
                return Path(found)
        raise LarkClientError("lark_cli_not_found")

    def _run_json(self, arguments: list[str]) -> Any:
        completed = self._run(arguments)
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise LarkClientError("invalid_lark_json") from error

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                [str(self.executable), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise LarkClientError("lark_cli_unavailable") from error
        if completed.returncode != 0:
            raise LarkClientError(f"lark_cli_failed:{completed.returncode}")
        return completed


def _safe_identifier(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 256:
        return False
    return all(character.isalnum() or character in "_-" for character in value)
