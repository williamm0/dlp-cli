"""Compile saved settings and guarded advanced yt-dlp options."""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .models import Settings


class OptionValidationError(ValueError):
    """Raised when advanced options would conflict with the app boundary."""


_FORBIDDEN_FLAGS = {
    "-o",
    "--output",
    "--paths",
    "-P",
    "--batch-file",
    "-a",
    "--progress",
    "--no-progress",
    "--progress-template",
    "--quiet",
    "--no-quiet",
    "--no-warnings",
    "--logger",
    "--verbose",
    "-v",
    "--print",
    "--print-to-file",
    "--exec",
    "--exec-before-download",
    "--exec-after-download",
    "--exec-cmd",
    "--simulate",
    "-s",
    "--username",
    "-u",
    "--password",
    "-p",
    "--video-password",
    "--ap-password",
    "--add-header",
    "--add-headers",
    "--proxy",
    "--ignore-config",
    "--no-config",
    "--use-postprocessor",
    "--postprocessor-args",
    "--ppa",
    "--plugin-dirs",
    "--config-location",
    "--config-locations",
    "--no-config-locations",
    "--no-plugin-dirs",
    "--downloader",
    "--downloader-args",
}


def split_extra_args(value: str) -> list[str]:
    try:
        return shlex.split(value)
    except ValueError as exc:
        raise OptionValidationError(f"invalid extra arguments: {exc}") from exc


def validate_extra_args(args: Sequence[str]) -> list[str]:
    normalized = [str(arg) for arg in args]
    for token in normalized:
        if not token.startswith("-"):
            continue
        flag = token.split("=", 1)[0]
        compact_short_flag = (
            token[:2] if token.startswith("-") and not token.startswith("--") else ""
        )
        if flag in _FORBIDDEN_FLAGS or compact_short_flag in {"-o", "-P", "-a", "-s", "-v"}:
            raise OptionValidationError(f"extra argument is controlled by dlp: {flag}")
    return normalized


def validate_filename_template(template: str) -> str:
    """Keep the user-controlled portion of the output path inside the folder."""

    value = template.strip()
    if not value:
        raise OptionValidationError("filename template cannot be empty")
    if "\x00" in value or "/" in value or "\\" in value:
        raise OptionValidationError("filename template cannot contain path separators")
    if Path(value).is_absolute() or re.match(r"^[A-Za-z]:", value):
        raise OptionValidationError("filename template must be relative")
    if value in {".", ".."}:
        raise OptionValidationError("filename template cannot be a directory")
    return value


def _output_template(settings: Settings) -> str:
    return str(
        Path(settings.output_directory).expanduser()
        / validate_filename_template(settings.filename_template)
    )


def _base_options(settings: Settings, progress_hook: Any, logger: Any) -> dict[str, Any]:
    options: dict[str, Any] = {
        "format": settings.format_selector,
        "outtmpl": {"default": _output_template(settings)},
        "overwrites": settings.overwrite == "overwrite",
        "continuedl": settings.resume_partial_files,
        "retries": settings.retries,
        "ignoreconfig": True,
        "quiet": True,
        "no_warnings": True,
        "logger": logger,
        "progress_hooks": [progress_hook],
    }
    if settings.playlist_mode == "single":
        options["noplaylist"] = True
    if settings.merge_output_format != "auto":
        options["merge_output_format"] = settings.merge_output_format
    if settings.rate_limit:
        options["ratelimit"] = settings.rate_limit
    if settings.socket_timeout:
        options["socket_timeout"] = settings.socket_timeout
    if settings.external_downloader:
        options["external_downloader"] = settings.external_downloader
    if settings.proxy:
        options["proxy"] = settings.proxy
    if settings.cookies_file:
        options["cookiefile"] = str(settings.cookies_file.expanduser())
    if settings.cookies_from_browser:
        options["cookiesfrombrowser"] = (settings.cookies_from_browser,)
    if settings.ffmpeg_path:
        options["ffmpeg_location"] = str(settings.ffmpeg_path.expanduser())
    elif settings.ffprobe_path:
        # yt-dlp accepts one ffmpeg location and discovers ffprobe beside it.
        options["ffmpeg_location"] = str(settings.ffprobe_path.expanduser().parent)
    if settings.js_runtime != "auto":
        options["js_runtimes"] = {settings.js_runtime: {"path": None}}
    if settings.subtitles == "manual":
        options["writesubtitles"] = True
        options["subtitleslangs"] = settings.subtitle_languages or ["en"]
    elif settings.subtitles == "auto":
        options["writeautomaticsub"] = True
        options["subtitleslangs"] = settings.subtitle_languages or ["en"]
    if settings.embed_metadata:
        options["addmetadata"] = True
    if settings.write_thumbnail:
        options["writethumbnail"] = True
    if settings.embed_thumbnail:
        options["embedthumbnail"] = True
    if settings.audio_only:
        options["format"] = f"{settings.audio_format}/bestaudio/best"
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": settings.audio_format,
                "preferredquality": settings.audio_quality,
            }
        ]
    return options


def _parse_advanced_options(args: Sequence[str]) -> dict[str, Any]:
    if not args:
        return {}
    validate_extra_args(args)
    try:
        import yt_dlp

        _, _, urls, parsed_options = yt_dlp.parse_options(list(args))
    except SystemExit as exc:
        raise OptionValidationError("yt-dlp rejected the advanced options") from exc
    except Exception as exc:
        raise OptionValidationError(f"could not parse advanced options: {exc}") from exc
    if urls:
        raise OptionValidationError("advanced options cannot contain URLs")
    return dict(parsed_options)


def compile_ydl_options(
    settings: Settings,
    progress_hook: Any,
    logger: Any,
) -> dict[str, Any]:
    """Compile settings, allowing safe advanced options while preserving UI ownership."""

    base = _base_options(settings, progress_hook, logger)
    advanced = _parse_advanced_options(settings.extra_args)
    protected = {
        key: base[key]
        for key in ("outtmpl", "ignoreconfig", "quiet", "no_warnings", "logger", "progress_hooks")
    }
    result = {**base, **advanced}
    result.update(protected)
    return result
