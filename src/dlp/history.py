"""Bounded, redacted per-user download history and named profiles."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platformdirs import PlatformDirs

from .diagnostics import sanitize_message, sanitize_url
from .models import DownloadRequest, DownloadResult, JobState, Settings

_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")


def user_data_path() -> Path:
    return Path(PlatformDirs("dlp", appauthor=False).user_data_path)


@dataclass(frozen=True)
class HistoryEntry:
    """A display-safe record of one queue item."""

    job_id: str
    url: str
    state: JobState
    timestamp: str
    title: str | None = None
    output_path: str | None = None
    error: str | None = None
    profile: str | None = None
    retry_count: int = 0

    @classmethod
    def from_result(
        cls,
        request: DownloadRequest,
        result: DownloadResult,
        *,
        profile: str | None = None,
        retry_count: int = 0,
    ) -> HistoryEntry:
        return cls(
            job_id=request.job_id,
            url=sanitize_url(request.url),
            state=result.state,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            title=sanitize_message(result.title or "") or None,
            output_path=sanitize_message(result.output_path or "") or None,
            error=sanitize_message(result.error or "") or None,
            profile=profile,
            retry_count=max(0, retry_count),
        )

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> HistoryEntry:
        state = JobState(str(raw.get("state", JobState.FAILED.value)))
        return cls(
            job_id=str(raw.get("job_id", "")),
            url=sanitize_url(str(raw.get("url", ""))),
            state=state,
            timestamp=str(raw.get("timestamp", "")),
            title=_optional_text(raw.get("title")),
            output_path=_optional_text(raw.get("output_path")),
            error=_optional_text(raw.get("error")),
            profile=_optional_text(raw.get("profile")),
            retry_count=max(0, int(raw.get("retry_count", 0))),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "url": sanitize_url(self.url),
            "state": self.state.value,
            "timestamp": self.timestamp,
            "title": self.title,
            "output_path": self.output_path,
            "error": self.error,
            "profile": self.profile,
            "retry_count": self.retry_count,
        }


class HistoryRepository:
    """Store recent records as a bounded JSONL file with atomic rewrites."""

    def __init__(self, path: Path | None = None, *, max_entries: int = 500) -> None:
        self.path = path or user_data_path() / "history.jsonl"
        self.max_entries = max(1, max_entries)

    def load(self, *, limit: int | None = None) -> list[HistoryEntry]:
        if limit is not None and limit <= 0:
            return []
        if not self.path.exists():
            return []
        entries: list[HistoryEntry] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in reversed(lines):
            try:
                raw = json.loads(line)
                if isinstance(raw, dict):
                    if raw.get("job_id") and raw.get("timestamp"):
                        entries.append(HistoryEntry.from_mapping(raw))
            except (ValueError, TypeError, KeyError):
                continue
            if limit is not None and len(entries) >= max(0, limit):
                break
        return entries

    def append(self, entry: HistoryEntry) -> None:
        entries = self.load()
        entries.insert(0, entry)
        self._write(list(reversed(entries[: self.max_entries])))

    def record(
        self,
        request: DownloadRequest,
        result: DownloadResult,
        *,
        profile: str | None = None,
        retry_count: int = 0,
    ) -> None:
        self.append(
            HistoryEntry.from_result(
                request,
                result,
                profile=profile,
                retry_count=retry_count,
            )
        )

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return

    def _write(self, entries: list[HistoryEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="history.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for entry in entries:
                    handle.write(json.dumps(entry.to_mapping(), sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
        finally:
            temp_path = Path(temp_name)
            if temp_path.exists():
                temp_path.unlink()


class ProfileRepository:
    """Persist validated settings snapshots under the user's config directory."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or (
            Path(PlatformDirs("dlp", appauthor=False).user_config_path) / "profiles"
        )

    def names(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(
            path.stem
            for path in self.directory.glob("*.toml")
            if _PROFILE_NAME.fullmatch(path.stem)
        )

    def load(self, name: str) -> Settings:
        path = self._path(name)
        from .config import SettingsRepository

        return SettingsRepository(path).load()

    def save(self, name: str, settings: Settings) -> None:
        from .config import SettingsRepository

        SettingsRepository(self._path(name)).save(settings)

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)

    def _path(self, name: str) -> Path:
        if not _PROFILE_NAME.fullmatch(name):
            raise ValueError(
                "profile name must be 1-32 characters using letters, numbers, '.', '_' or '-'",
            )
        return self.directory / f"{name}.toml"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = sanitize_message(str(value)).strip()
    return text or None
