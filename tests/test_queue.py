import threading

from dlp.dependencies import DependencyName, DependencyStatus
from dlp.models import DownloadRequest, DownloadResult, JobState, ProgressPhase, Settings
from dlp.queue import QueueRunner


class FakeEngine:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def run(self, request, emit, cancel_event):
        self.urls.append(request.url)
        return DownloadResult(request.job_id, JobState.COMPLETED)


class ReadyDependencies:
    def check_for_request(self, _url, _settings):
        return []


def test_queue_continues_after_engine_failure() -> None:
    class Engine:
        def run(self, request, emit, cancel_event):
            state = JobState.FAILED if request.url.endswith("bad") else JobState.COMPLETED
            return DownloadResult(request.job_id, state)

    requests = [
        DownloadRequest("1", "https://example.com/bad", Settings()),
        DownloadRequest("2", "https://example.com/good", Settings()),
    ]
    result = QueueRunner(Engine(), ReadyDependencies()).run(requests, lambda _: None)

    assert [item.state for item in result.items] == [JobState.FAILED, JobState.COMPLETED]


def test_queue_blocks_missing_dependency_without_prompt() -> None:
    status = DependencyStatus(
        DependencyName.FFMPEG,
        available=False,
        required=True,
        reason="needed",
    )

    class MissingDependencies:
        def check_for_request(self, _url, _settings):
            return [status]

    engine = FakeEngine()
    request = DownloadRequest("1", "https://example.com/video", Settings())
    result = QueueRunner(engine, MissingDependencies()).run([request], lambda _: None)

    assert result.items[0].state == JobState.BLOCKED
    assert engine.urls == []


def test_queue_reports_cancellation_before_start() -> None:
    engine = FakeEngine()
    request = DownloadRequest("1", "https://example.com/video", Settings())
    events = []
    cancel_event = threading.Event()
    cancel_event.set()

    result = QueueRunner(engine, ReadyDependencies()).run(
        [request], events.append, cancel_event=cancel_event
    )

    assert result.items[0].state == JobState.CANCELED
    assert events[0].phase == ProgressPhase.CANCELED
    assert engine.urls == []


def test_queue_installs_after_consent_and_rechecks_dependencies() -> None:
    status = DependencyStatus(
        DependencyName.FFMPEG,
        available=False,
        required=True,
        reason="needed",
        install_command=("brew", "install", "ffmpeg"),
    )

    class InstallingDependencies:
        def __init__(self) -> None:
            self.checks = 0
            self.installs = []

        def check_for_request(self, _url, _settings):
            self.checks += 1
            return [status] if self.checks == 1 else []

        def install(self, name, consent):
            self.installs.append((name, consent))

    dependencies = InstallingDependencies()
    engine = FakeEngine()
    request = DownloadRequest("1", "https://example.com/video", Settings())
    result = QueueRunner(engine, dependencies).run(
        [request],
        lambda _event: None,
        dependency_prompt=lambda _request, _missing: True,
    )

    assert result.items[0].state == JobState.COMPLETED
    assert engine.urls == [request.url]
    assert dependencies.installs == [(DependencyName.FFMPEG, True)]
