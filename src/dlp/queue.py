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
ResultSink = Callable[[DownloadRequest, DownloadResult], None]


class QueueRunner:
    def __init__(
        self,
        engine: DownloadEngine | None = None,
        dependency_manager: DependencyManager | None = None,
        result_sink: ResultSink | None = None,
    ) -> None:
        self.engine = engine or DownloadEngine()
        self.dependency_manager = dependency_manager or DependencyManager()
        self.result_sink = result_sink

    def run(
        self,
        requests: Sequence[DownloadRequest],
        emit: Callable[[ProgressEvent], None],
        cancel_event: threading.Event | None = None,
        dependency_prompt: DependencyPrompt | None = None,
    ) -> BatchResult:
        cancel_event = cancel_event or threading.Event()
        results: list[DownloadResult] = []
        for index, request in enumerate(requests):
            if cancel_event.is_set():
                for pending in requests[index:]:
                    result = _canceled_result(pending, emit)
                    results.append(result)
                    self._sink(pending, result)
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
                result = DownloadResult(request.job_id, JobState.BLOCKED, error=message)
                results.append(result)
                self._sink(request, result)
                continue

            result = self.engine.run(request, emit, cancel_event)
            results.append(result)
            self._sink(request, result)
            if result.state == JobState.CANCELED:
                for pending in requests[index + 1 :]:
                    canceled = _canceled_result(pending, emit)
                    results.append(canceled)
                    self._sink(pending, canceled)
                break
        return BatchResult(results)

    def _sink(self, request: DownloadRequest, result: DownloadResult) -> None:
        if self.result_sink is None:
            return
        try:
            self.result_sink(request, result)
        except (OSError, ValueError):
            # A history file must never turn a completed download into a failure.
            return


def _canceled_result(
    request: DownloadRequest,
    emit: Callable[[ProgressEvent], None],
) -> DownloadResult:
    message = "Canceled"
    emit(
        ProgressEvent(
            job_id=request.job_id,
            phase=ProgressPhase.CANCELED,
            message=message,
            state=JobState.CANCELED,
        )
    )
    return DownloadResult(request.job_id, JobState.CANCELED, error=message)


def _missing_message(statuses: list[DependencyStatus]) -> str:
    names = ", ".join(status.name.value for status in statuses)
    return sanitize_message(f"Missing dependency: {names}")
