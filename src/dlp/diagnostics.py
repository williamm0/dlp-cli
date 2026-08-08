"""Small redaction helpers for user-visible status and error messages."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(cookie|authorization|password|passwd|token|secret|api[_-]?key)\s*[:=]\s*([^;\n]+)"
)


def sanitize_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme or not parts.netloc:
        return value
    try:
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return urlunsplit((parts.scheme, "[redacted]", parts.path, "", ""))
    if not hostname:
        return urlunsplit((parts.scheme, "[redacted]", parts.path, "", ""))
    safe_host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if port is not None:
        safe_host = f"{safe_host}:{port}"
    return urlunsplit((parts.scheme, safe_host, parts.path, "", ""))


def sanitize_message(value: str) -> str:
    redacted = _SENSITIVE_ASSIGNMENT.sub(r"\1=[redacted]", value)
    return re.sub(r"https?://[^\s]+", lambda match: sanitize_url(match.group(0)), redacted)


def sanitize_exception(exc: BaseException) -> str:
    return sanitize_message(str(exc)) or exc.__class__.__name__
