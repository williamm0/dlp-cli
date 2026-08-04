"""Batch URL input parsing."""

from __future__ import annotations

from pathlib import Path


def parse_batch_text(text: str) -> list[str]:
    """Return non-empty, non-comment lines in their original order."""

    urls: list[str] = []
    for line in text.splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith("#"):
            urls.append(candidate)
    return urls

def read_batch_file(path: Path) -> list[str]:
    return parse_batch_text(path.read_text(encoding="utf-8"))


def normalize_urls(values: list[str]) -> list[str]:
    """Normalize command-line URL values without imposing a site allowlist."""

    urls = [value.strip() for value in values if value.strip()]
    if not urls:
        raise ValueError("at least one URL is required")
    return urls
