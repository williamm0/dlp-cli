"""Serial queue orchestration and dependency gating."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from .dependencies import DependencyManager, DependencyStatus
from .diagnostics import sanitize_message
from .engine import DownloadEngine
from .models import (
    BatchResult,
    DownloadRequest,
    DownloadResult,
    JobState,
    ProgressEvent,
    ProgressPhase,
)

DependencyPrompt = Callable[[DownloadRequest, list[DependencyStatus]], bool]


class QueueRunner:
    def __init__(
        self,
        engine: DownloadEngine | None = None,
        dependency_manager: DependencyManager | None = None,
    ) -> None:
        self.engine = engine or DownloadEngine()
        self.dependency_manager = dependency_manager or DependencyManager()

    def run(
        self,
        requests: Sequence[DownloadRequest],
        emit: Callable[[ProgressEvent], None],
        cancel_event: threading.Event | None = None,
        dependency_prompt: DependencyPrompt | None = None,
    ) -> BatchResult:
        cancel_event = cancel_event or threading.Event()
        results: list[DownloadResult] = []
        for request in requests:
            if cancel_event.is_set():
                message = "Canceled"
                emit(
                    ProgressEvent(
                        job_id=request.job_id,
                        phase=ProgressPhase.CANCELED,
                        message=message,
                        state=JobState.CANCELED,
                    )
                )
                results.append(DownloadResult(request.job_id, JobState.CANCELED, error=message))
                break
            missing = [
                status
                for status in self.dependency_manager.check_for_request(
                    request.url,
                    request.settings,
                )
                if not status.available
            ]
            if missing and dependency_prompt and dependency_prompt(request, missing):
                for status in missing:
                    self.dependency_manager.install(status.name, consent=True)
                missing = [
                    status
                    for status in self.dependency_manager.check_for_request(
                        request.url,
                        request.settings,
                    )
                    if not status.available
                ]
            if missing:
                message = _missing_message(missing)
                emit(
                    ProgressEvent(
                        job_id=request.job_id,
                        phase=ProgressPhase.BLOCKED,
                        message=message,
                        state=JobState.BLOCKED,
                    )
                )
                results.append(DownloadResult(request.job_id, JobState.BLOCKED, error=message))
                continue

            result = self.engine.run(request, emit, cancel_event)
            results.append(result)
            if result.state == JobState.CANCELED:
                break
        return BatchResult(results)


def _missing_message(statuses: list[DependencyStatus]) -> str:
    names = ", ".join(status.name.value for status in statuses)
    return sanitize_message(f"Missing dependency: {names}")
