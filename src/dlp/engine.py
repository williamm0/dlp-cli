"""In-process yt-dlp execution with normalized progress events."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .diagnostics import sanitize_exception, sanitize_message
from .models import (
    DownloadRequest,
    DownloadResult,
    JobState,
    ProgressEvent,
    ProgressPhase,
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
            current_filename = data.get("filename") or current_filename
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
            percent = (downloaded / total * 100) if downloaded is not None and total else None
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
                ydl.download([request.url])
        except OptionValidationError as exc:
            message = sanitize_exception(exc)
            emit(_event(request, ProgressPhase.FAILED, message, JobState.FAILED))
            return DownloadResult(
                request.job_id,
                JobState.FAILED,
                error=message,
                diagnostics=tuple(logger.messages),
            )
        except BaseException as exc:
            if _is_canceled(exc, cancel_event):
                message = "Canceled"
                emit(_event(request, ProgressPhase.CANCELED, message, JobState.CANCELED))
                return DownloadResult(
                    request.job_id,
                    JobState.CANCELED,
                    error=message,
                    diagnostics=tuple(logger.messages),
                )
            message = sanitize_exception(exc)
            emit(_event(request, ProgressPhase.FAILED, message, JobState.FAILED))
            return DownloadResult(
                request.job_id,
                JobState.FAILED,
                error=message,
                diagnostics=tuple(logger.messages),
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
        )


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


def _is_canceled(exc: BaseException, cancel_event: threading.Event) -> bool:
    if cancel_event.is_set():
        return True
    return exc.__class__.__name__ == "DownloadCancelled"
