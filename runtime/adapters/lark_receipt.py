"""Render and send verified Feishu receipts without template code execution."""

from __future__ import annotations

import re
from typing import Any


class ReceiptError(ValueError):
    """A receipt could not be rendered or confirmed."""


_PLACEHOLDER = re.compile(r"{{([a-z_]+)}}")
_ALLOWED = frozenset(
    {
        "filename",
        "local_destinations",
        "github_destinations",
        "routes",
        "paused_participants",
        "commit",
    }
)


def render_receipt(template: str, result: dict[str, Any]) -> str:
    if not isinstance(template, str) or not template.strip():
        raise ReceiptError("receipt_template_required")
    names = set(_PLACEHOLDER.findall(template))
    if names - _ALLOWED:
        raise ReceiptError("receipt_template_placeholder_unknown")
    values = {name: _display(result.get(name)) for name in _ALLOWED}
    rendered = _PLACEHOLDER.sub(lambda match: values[match.group(1)], template)
    if not rendered.strip():
        raise ReceiptError("receipt_empty")
    return rendered


def send_receipt(client: Any, message_id: str, template: str, result: dict[str, Any]) -> str:
    rendered = render_receipt(template, result)
    response = client.reply_message(message_id, rendered)
    if not _response_ok(response):
        raise ReceiptError("receipt_failed")
    return rendered


def _response_ok(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    if response.get("ok") is True:
        return True
    return response.get("code") == 0


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)
