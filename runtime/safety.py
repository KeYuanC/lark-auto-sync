"""Small validation helpers shared by the runtime."""

import re


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def require_safe_identifier(value: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise ValueError("unsafe_identifier")
    return value
