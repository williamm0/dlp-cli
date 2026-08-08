"""In-process yt-dlp execution with normalized progress events."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .diagnostics import sanitize_exception, sanitize_message
from .models import (
    DownloadRequest,
    DownloadResult,
    FormatInfo,
    JobState,
    MediaInfo,
    ProgressEvent,
    ProgressPhase,
    Settings,
)
from .options import OptionValidationError, compile_ydl_options


class DiagnosticLogger:
    """Capture yt-dlp messages without echoing its normal terminal output."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, message: str) -> None:
        if message.startswith("[debug]"):
            return
        self.messages.append(sanitize_message(message))

    def warning(self, message: str) -> None:
        self.messages.append(sanitize_message(message))

    def error(self, message: str) -> None:
        self.messages.append(sanitize_message(message))


class DownloadEngine:
    def __init__(self, ytdlp_factory: Any | None = None) -> None:
        self._ytdlp_factory = ytdlp_factory

    def run(
        self,
        request: DownloadRequest,
        emit: Callable[[ProgressEvent], None],
        cancel_event: threading.Event,
    ) -> DownloadResult:
        logger = DiagnosticLogger()
        current_title: str | None = None
        current_filename: str | None = None

        def progress_hook(data: dict[str, Any]) -> None:
            nonlocal current_title, current_filename
            if cancel_event.is_set():
                try:
                    from yt_dlp.utils import DownloadCancelled

                    raise DownloadCancelled("Canceled by user")
                except ImportError:
                    raise RuntimeError("Canceled by user") from None

            info = data.get("info") or {}
            current_title = info.get("title") or current_title
            current_filename = _event_filename(data) or current_filename
            status = data.get("status")
            if status == "finished":
                emit(
                    ProgressEvent(
                        job_id=request.job_id,
                        phase=ProgressPhase.POST_PROCESSING,
                        percent=100.0,
                        title=current_title,
                        filename=current_filename,
                        message="Post-processing",
                        state=JobState.POST_PROCESSING,
                    )
                )
                return
            if status != "downloading":
                emit(
                    ProgressEvent(
                        job_id=request.job_id,
                        phase=ProgressPhase.EXTRACTING,
                        title=current_title,
                        message="Extracting",
                        state=JobState.EXTRACTING,
                    )
                )
                return

            downloaded = _int_or_none(data.get("downloaded_bytes"))
            total = _int_or_none(data.get("total_bytes")) or _int_or_none(
                data.get("total_bytes_estimate")
            )
            percent = (
                _clamp_percent(downloaded / total * 100)
                if downloaded is not None and total
                else None
            )
            emit(
                ProgressEvent(
                    job_id=request.job_id,
                    phase=ProgressPhase.DOWNLOADING,
                    percent=percent,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    speed_bytes=_float_or_none(data.get("speed")),
                    eta_seconds=_int_or_none(data.get("eta")),
                    title=current_title,
                    filename=current_filename,
                    message="Downloading",
                    state=JobState.DOWNLOADING,
                )
            )

        def postprocessor_hook(data: dict[str, Any]) -> None:
            if cancel_event.is_set():
                try:
                    from yt_dlp.utils import DownloadCancelled

                    raise DownloadCancelled("Canceled by user")
                except ImportError:
                    raise RuntimeError("Canceled by user") from None
            status = data.get("status", "processing")
            nonlocal current_filename
            current_filename = _event_filename(data) or current_filename
            emit(
                ProgressEvent(
                    job_id=request.job_id,
                    phase=ProgressPhase.POST_PROCESSING,
                    percent=100.0,
                    title=current_title,
                    filename=current_filename,
                    message="Post-processing" if status != "finished" else "Finalizing",
                    state=JobState.POST_PROCESSING,
                )
            )

        try:
            ytdlp_factory = self._ytdlp_factory
            if ytdlp_factory is None:
                import yt_dlp

                ytdlp_factory = yt_dlp.YoutubeDL
            options = compile_ydl_options(request.settings, progress_hook, logger)
            options["postprocessor_hooks"] = [postprocessor_hook]
            emit(
                ProgressEvent(
                    job_id=request.job_id,
                    phase=ProgressPhase.EXTRACTING,
                    message="Extracting",
                    state=JobState.EXTRACTING,
                )
            )
            with ytdlp_factory(options) as ydl:
                result_code = ydl.download([request.url])
                if result_code not in (None, 0):
                    raise RuntimeError(f"yt-dlp exited with status {result_code}")
        except OptionValidationError as exc:
            message = sanitize_exception(exc)
            emit(_event(request, ProgressPhase.FAILED, message, JobState.FAILED))
            return DownloadResult(
                request.job_id,
                JobState.FAILED,
                error=message,
                diagnostics=tuple(logger.messages),
                title=current_title,
            )
        except Exception as exc:
            if _is_canceled(exc, cancel_event):
                message = "Canceled"
                emit(_event(request, ProgressPhase.CANCELED, message, JobState.CANCELED))
                return DownloadResult(
                    request.job_id,
                    JobState.CANCELED,
                    error=message,
                    diagnostics=tuple(logger.messages),
                    title=current_title,
                )
            message = sanitize_exception(exc)
            emit(_event(request, ProgressPhase.FAILED, message, JobState.FAILED))
            return DownloadResult(
                request.job_id,
                JobState.FAILED,
                error=message,
                diagnostics=tuple(logger.messages),
                title=current_title,
            )

        emit(
            ProgressEvent(
                job_id=request.job_id,
                phase=ProgressPhase.COMPLETED,
                percent=100.0,
                title=current_title,
                filename=current_filename,
                message="Completed",
                state=JobState.COMPLETED,
            )
        )
        return DownloadResult(
            request.job_id,
            JobState.COMPLETED,
            output_path=current_filename,
            diagnostics=tuple(logger.messages),
            title=current_title,
        )

    def extract_info(
        self,
        url: str,
        settings: Settings | None = None,
        *,
        flat_playlist: bool = False,
    ) -> MediaInfo:
        """Extract a small safe metadata snapshot without transferring media."""

        from .diagnostics import sanitize_url

        active_settings = settings or Settings()
        logger = DiagnosticLogger()
        try:
            ytdlp_factory = self._ytdlp_factory
            if ytdlp_factory is None:
                import yt_dlp

                ytdlp_factory = yt_dlp.YoutubeDL
            options = compile_ydl_options(active_settings, lambda _data: None, logger)
            options["skip_download"] = True
            if flat_playlist:
                options["extract_flat"] = True
                options.pop("noplaylist", None)
            with ytdlp_factory(options) as ydl:
                raw = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise RuntimeError(sanitize_exception(exc)) from exc

        if not isinstance(raw, dict):
            raise RuntimeError("yt-dlp returned no metadata")
        entries = raw.get("entries")
        item_count = raw.get("playlist_count")
        if item_count is None and isinstance(entries, (list, tuple)):
            item_count = len(entries)
        return MediaInfo(
            url=sanitize_url(str(raw.get("webpage_url") or url)),
            title=_safe_text(raw.get("title")),
            uploader=_safe_text(raw.get("uploader")),
            channel=_safe_text(raw.get("channel")),
            duration_seconds=_int_or_none(raw.get("duration")),
            webpage_url=sanitize_url(str(raw["webpage_url"])) if raw.get("webpage_url") else None,
            thumbnail_url=sanitize_url(str(raw["thumbnail"])) if raw.get("thumbnail") else None,
            extractor=_safe_text(raw.get("extractor_key") or raw.get("extractor")),
            video_id=_safe_text(raw.get("id")),
            is_playlist=bool(raw.get("_type") == "playlist" or entries is not None),
            item_count=_int_or_none(item_count),
        )

    def list_formats(
        self,
        url: str,
        settings: Settings | None = None,
    ) -> list[FormatInfo]:
        """Return a sanitized format inventory without downloading media."""

        active_settings = settings or Settings()
        logger = DiagnosticLogger()
        try:
            ytdlp_factory = self._ytdlp_factory
            if ytdlp_factory is None:
                import yt_dlp

                ytdlp_factory = yt_dlp.YoutubeDL
            options = compile_ydl_options(active_settings, lambda _data: None, logger)
            options["skip_download"] = True
            with ytdlp_factory(options) as ydl:
                raw = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise RuntimeError(sanitize_exception(exc)) from exc

        if not isinstance(raw, dict):
            raise RuntimeError("yt-dlp returned no metadata")
        values = raw.get("formats")
        if not isinstance(values, (list, tuple)):
            return []
        formats: list[FormatInfo] = []
        for value in values:
            if not isinstance(value, dict) or not value.get("format_id"):
                continue
            formats.append(
                FormatInfo(
                    format_id=_safe_text(value.get("format_id")) or "?",
                    ext=_safe_text(value.get("ext")),
                    resolution=_safe_text(value.get("resolution")),
                    fps=_float_or_none(value.get("fps")),
                    filesize=_int_or_none(value.get("filesize") or value.get("filesize_approx")),
                    tbr=_float_or_none(value.get("tbr")),
                    video_codec=_safe_text(value.get("vcodec")),
                    audio_codec=_safe_text(value.get("acodec")),
                    note=_safe_text(value.get("format_note")),
                )
            )
        return formats


def _event(
    request: DownloadRequest,
    phase: ProgressPhase,
    message: str,
    state: JobState,
) -> ProgressEvent:
    return ProgressEvent(
        job_id=request.job_id,
        phase=phase,
        message=message,
        state=state,
    )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _clamp_percent(value: float) -> float:
    return max(0.0, min(100.0, value))


def _event_filename(data: dict[str, Any]) -> str | None:
    info = data.get("info_dict") or data.get("info") or {}
    values = (data.get("filepath"), data.get("filename"), info.get("_filename"))
    for value in values:
        if value:
            return str(value)
    return None


def _safe_text(value: Any) -> str | None:
    if value is None:
        return None
    text = sanitize_message(str(value)).strip()
    return text or None


def _is_canceled(exc: BaseException, cancel_event: threading.Event) -> bool:
    if cancel_event.is_set():
        return True
    return exc.__class__.__name__ == "DownloadCancelled"
