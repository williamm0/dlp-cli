"""Small redaction helpers for user-visible status and error messages."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(cookie|authorization|password|passwd|token|secret|api[_-]?key)\s*[:=]\s*([^;\n]+)"
)
_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s]+")
_TERMINAL_ESCAPE = re.compile(
    r"\x1b\](?:[^\x07\x1b]|\x1b(?!\\))*?(?:\x07|\x1b\\)|"
    r"\x1b\[[0-?]*[ -/]*[@-~]"
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\r\n\t]")


def sanitize_url(value: str) -> str:
    value = _TERMINAL_ESCAPE.sub("", value)
    value = _CONTROL_CHARS.sub("", value)
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
    safe = _TERMINAL_ESCAPE.sub(" ", str(value))
    safe = _CONTROL_CHARS.sub(" ", safe)
    redacted = _SENSITIVE_ASSIGNMENT.sub(r"\1=[redacted]", safe)
    return _URL.sub(lambda match: sanitize_url(match.group(0)), redacted)


def sanitize_exception(exc: BaseException) -> str:
    return sanitize_message(str(exc)) or exc.__class__.__name__
