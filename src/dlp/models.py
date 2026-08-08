"""Data contracts shared by the CLI, Textual UI, and download engine."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(cookie|authorization|password|passwd|token|secret|api[_-]?key)\s*[:=]"
)
_SENSITIVE_EXTRA_FLAGS = {
    "--username",
    "-u",
    "--password",
    "-p",
    "--video-password",
    "--ap-password",
    "--add-header",
    "--add-headers",
    "--proxy",
    "--cookies",
    "--cookies-from-browser",
    "--netrc",
    "--netrc-location",
}


class JobState(str, Enum):
    QUEUED = "queued"
    BLOCKED = "blocked"
    EXTRACTING = "extracting"
    DOWNLOADING = "downloading"
    POST_PROCESSING = "post-processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class ProgressPhase(str, Enum):
    EXTRACTING = "extracting"
    DOWNLOADING = "downloading"
    POST_PROCESSING = "post-processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    BLOCKED = "blocked"


class InstallState(str, Enum):
    INSTALLED = "installed"
    DECLINED = "declined"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass
class Settings:
    """User preferences compiled into yt-dlp options for each job."""

    quality_mode: str = "best"
    format_selector: str = "bestvideo*+bestaudio/best"
    merge_output_format: str = "auto"
    output_directory: Path = field(default_factory=lambda: Path.home() / "Downloads" / "dlp")
    filename_template: str = "%(title)s [%(id)s].%(ext)s"
    download_archive: Path | None = None
    overwrite: str = "skip"
    resume_partial_files: bool = True
    retries: int = 3
    fragment_retries: int = 3
    concurrent_fragments: int = 1
    playlist_mode: str = "all"
    subtitles: str = "off"
    subtitle_languages: list[str] = field(default_factory=list)
    audio_only: bool = False
    audio_format: str = "best"
    audio_quality: str = "5"
    embed_metadata: bool = False
    write_thumbnail: bool = False
    embed_thumbnail: bool = False
    write_info_json: bool = False
    write_description: bool = False
    write_comments: bool = False
    cookies_from_browser: str | None = None
    cookies_file: Path | None = None
    proxy: str | None = None
    rate_limit: str | None = None
    socket_timeout: int | None = None
    external_downloader: str | None = None
    ffmpeg_path: Path | None = None
    ffprobe_path: Path | None = None
    js_runtime: str = "deno"
    extra_args: list[str] = field(default_factory=list)

    def clone(self) -> Settings:
        """Return an independent snapshot suitable for a queued job."""

        return replace(
            self,
            subtitle_languages=list(self.subtitle_languages),
            extra_args=list(self.extra_args),
        )

    def to_mapping(self) -> dict[str, Any]:
        _validate_proxy(self.proxy)
        _validate_extra_args_for_storage(self.extra_args)
        if self.external_downloader not in {None, "aria2c"}:
            raise ValueError("external_downloader must be 'aria2c' or empty")
        for key in ("audio_format", "merge_output_format"):
            if not _is_safe_option_text(getattr(self, key)):
                raise ValueError(f"{key} contains unsupported characters")
        result: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if value is None:
                continue
            if isinstance(value, Path):
                result[key] = str(value)
            elif isinstance(value, list):
                result[key] = list(value)
            else:
                result[key] = value
        return result

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> Settings:
        default_instance = cls()
        values = dict(default_instance.__dict__)
        for key in values:
            if key in raw:
                values[key] = raw[key]

        for key in (
            "output_directory",
            "download_archive",
            "cookies_file",
            "ffmpeg_path",
            "ffprobe_path",
        ):
            if values[key] is not None:
                values[key] = Path(str(values[key])).expanduser()

        if not isinstance(values["subtitle_languages"], list):
            raise ValueError("subtitle_languages must be a list")
        if not isinstance(values["extra_args"], list):
            raise ValueError("extra_args must be a list")
        values["subtitle_languages"] = [str(item) for item in values["subtitle_languages"]]
        values["extra_args"] = [str(item) for item in values["extra_args"]]
        _validate_proxy(values["proxy"])
        _validate_extra_args_for_storage(values["extra_args"])
        if values["socket_timeout"] is not None:
            values["socket_timeout"] = _bounded_int(
                values["socket_timeout"], "socket_timeout", minimum=1, maximum=86_400
            )
        for key in (
            "audio_only",
            "resume_partial_files",
            "embed_metadata",
            "write_thumbnail",
            "embed_thumbnail",
            "write_info_json",
            "write_description",
            "write_comments",
        ):
            values[key] = _strict_bool(values[key], key)

        values["retries"] = _nonnegative_int(values["retries"], "retries")
        values["fragment_retries"] = _nonnegative_int(
            values["fragment_retries"], "fragment_retries"
        )
        values["concurrent_fragments"] = _bounded_int(
            values["concurrent_fragments"], "concurrent_fragments", minimum=1, maximum=32
        )

        if values["quality_mode"] not in {"best", "custom"}:
            raise ValueError("quality_mode must be 'best' or 'custom'")
        if values["overwrite"] not in {"skip", "overwrite"}:
            raise ValueError("overwrite must be 'skip' or 'overwrite'")
        if values["playlist_mode"] not in {"all", "single"}:
            raise ValueError("playlist_mode must be 'all' or 'single'")
        if values["subtitles"] not in {"off", "manual", "auto"}:
            raise ValueError("subtitles must be 'off', 'manual', or 'auto'")
        if values["js_runtime"] not in {"auto", "deno"}:
            raise ValueError("js_runtime must be 'auto' or 'deno'")
        if values["external_downloader"] not in {None, "aria2c"}:
            raise ValueError("external_downloader must be 'aria2c' or empty")
        for key in ("audio_format", "merge_output_format"):
            if not _is_safe_option_text(values[key]):
                raise ValueError(f"{key} contains unsupported characters")

        return cls(**values)


def _validate_proxy(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError("proxy must be a string")
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise ValueError("proxy is not a valid URL") from exc
    if parts.scheme.lower() not in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}:
        raise ValueError("proxy must use http, https, socks4, or socks5")
    if not parts.hostname:
        raise ValueError("proxy must include a hostname")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError("proxy must include a valid port") from exc
    if parts.username or parts.password:
        raise ValueError("proxy credentials cannot be stored; use a credential-free proxy URL")


def _strict_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} must be zero or greater")
    return parsed


def _bounded_int(value: Any, field_name: str, *, minimum: int, maximum: int) -> int:
    parsed = _nonnegative_int(value, field_name)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return parsed


def _is_safe_option_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and not any(
        character.isspace() or character in {"/", "\\", "\x00"} for character in value
    )


def _validate_extra_args_for_storage(args: list[str]) -> None:
    for token in args:
        flag = token.split("=", 1)[0]
        if flag in _SENSITIVE_EXTRA_FLAGS or _SENSITIVE_ASSIGNMENT.search(token):
            raise ValueError("advanced arguments cannot contain credentials or sensitive headers")


@dataclass(frozen=True)
class DownloadRequest:
    job_id: str
    url: str
    settings: Settings


@dataclass
class ProgressEvent:
    job_id: str
    phase: ProgressPhase
    percent: float | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed_bytes: float | None = None
    eta_seconds: int | None = None
    title: str | None = None
    filename: str | None = None
    message: str = ""
    state: JobState | None = None


@dataclass(frozen=True)
class MediaInfo:
    """Small, display-safe metadata snapshot returned by an info request."""

    url: str
    title: str | None = None
    uploader: str | None = None
    channel: str | None = None
    duration_seconds: int | None = None
    webpage_url: str | None = None
    thumbnail_url: str | None = None
    extractor: str | None = None
    video_id: str | None = None
    is_playlist: bool = False
    item_count: int | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if value is not None
        }


@dataclass(frozen=True)
class FormatInfo:
    """Display-safe summary of one format returned by yt-dlp."""

    format_id: str
    ext: str | None = None
    resolution: str | None = None
    fps: float | None = None
    filesize: int | None = None
    tbr: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    note: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value is not None}


@dataclass
class QueueItem:
    request: DownloadRequest
    state: JobState = JobState.QUEUED
    progress: ProgressEvent | None = None
    output_path: str | None = None
    retry_count: int = 0
    error: str | None = None


@dataclass
class DownloadResult:
    job_id: str
    state: JobState
    output_path: str | None = None
    error: str | None = None
    diagnostics: tuple[str, ...] = ()
    title: str | None = None
    missing_dependencies: tuple[str, ...] = ()


@dataclass
class BatchResult:
    items: list[DownloadResult]

    @property
    def succeeded(self) -> bool:
        return bool(self.items) and all(item.state == JobState.COMPLETED for item in self.items)

    @property
    def failed_count(self) -> int:
        return sum(item.state in {JobState.FAILED, JobState.BLOCKED} for item in self.items)
