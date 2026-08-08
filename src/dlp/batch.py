"""Batch URL input parsing."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit


def parse_batch_text(text: str) -> list[str]:
    """Return non-empty, non-comment lines in their original order."""

    urls: list[str] = []
    for line in text.splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith("#"):
            urls.append(candidate)
    return urls

def read_batch_file(path: Path) -> list[str]:
    return normalize_urls(parse_batch_text(path.read_text(encoding="utf-8")))


def normalize_urls(values: list[str]) -> list[str]:
    """Normalize command-line URL values without imposing a site allowlist."""

    urls = [value.strip() for value in values if value.strip()]
    if not urls:
        raise ValueError("at least one URL is required")
    for url in urls:
        if any(ord(character) < 0x20 for character in url) or any(
            character.isspace() for character in url
        ):
            raise ValueError("URLs cannot contain whitespace or control characters")
        try:
            parts = urlsplit(url)
        except ValueError as exc:
            raise ValueError(f"invalid URL: {url}") from exc
        if not parts.scheme:
            raise ValueError(f"invalid URL (missing scheme): {url}")
        if parts.scheme.lower() in {"file", "data", "javascript", "blob"}:
            raise ValueError(f"unsupported URL scheme: {parts.scheme}")
        if parts.scheme.lower() in {"http", "https", "ftp"} and not parts.netloc:
            raise ValueError(f"invalid URL (missing host): {url}")
    return urls
