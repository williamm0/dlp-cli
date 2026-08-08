import threading
from typing import Any

from dlp.engine import DownloadEngine
from dlp.models import DownloadRequest, JobState, ProgressPhase, Settings


class FakeYoutubeDL:
    last_options: dict[str, Any] | None = None

    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        FakeYoutubeDL.last_options = options

    def __enter__(self) -> "FakeYoutubeDL":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def download(self, _urls: list[str]) -> int:
        hook = self.options["progress_hooks"][0]
        hook(
            {
                "status": "downloading",
                "downloaded_bytes": 50,
                "total_bytes": 100,
                "speed": 1024.0,
                "eta": 2,
                "filename": "/tmp/video.part",
                "info": {"title": "Test video"},
            }
        )
        hook({"status": "finished", "filename": "/tmp/video.mp4", "info": {"title": "Test video"}})
        return 0


def test_engine_emits_progress_without_printing_yt_dlp_logs() -> None:
    request = DownloadRequest("job-1", "https://example.com/video", Settings())
    events = []

    result = DownloadEngine(FakeYoutubeDL).run(request, events.append, threading.Event())

    assert result.state == JobState.COMPLETED
    assert [event.phase for event in events] == [
        ProgressPhase.EXTRACTING,
        ProgressPhase.DOWNLOADING,
        ProgressPhase.POST_PROCESSING,
        ProgressPhase.COMPLETED,
    ]
    assert events[1].percent == 50.0
    assert events[1].speed_bytes == 1024.0


class FailingYoutubeDL(FakeYoutubeDL):
    def __init__(self, options: dict[str, Any], *, error: Exception | None = None) -> None:
        super().__init__(options)
        self.error = error

    def download(self, _urls: list[str]) -> int:
        if self.error:
            raise self.error
        return 1


def test_engine_turns_nonzero_yt_dlp_result_into_a_failed_result() -> None:
    request = DownloadRequest("job-1", "https://example.com/video", Settings())
    result = DownloadEngine(FailingYoutubeDL).run(request, lambda _event: None, threading.Event())

    assert result.state == JobState.FAILED
    assert result.error == "yt-dlp exited with status 1"


def test_engine_sanitizes_extractor_errors() -> None:
    request = DownloadRequest("job-1", "https://example.com/video", Settings())

    def factory(options: dict[str, Any]) -> FailingYoutubeDL:
        return FailingYoutubeDL(
            options,
            error=RuntimeError("Authorization: Bearer top-secret"),
        )

    result = DownloadEngine(factory).run(request, lambda _event: None, threading.Event())

    assert result.state == JobState.FAILED
    assert "top-secret" not in (result.error or "")


def test_engine_honors_cancellation_from_a_progress_hook() -> None:
    request = DownloadRequest("job-1", "https://example.com/video", Settings())
    cancel_event = threading.Event()
    cancel_event.set()
    events = []

    result = DownloadEngine(FakeYoutubeDL).run(request, events.append, cancel_event)

    assert result.state == JobState.CANCELED
    assert events[-1].phase == ProgressPhase.CANCELED


class EmptyMetadataYoutubeDL:
    def __init__(self, _options: dict[str, Any]) -> None:
        pass

    def __enter__(self) -> "EmptyMetadataYoutubeDL":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def extract_info(self, _url: str, download: bool = False) -> object:
        assert download is False
        return None


def test_engine_rejects_missing_metadata() -> None:
    try:
        DownloadEngine(EmptyMetadataYoutubeDL).extract_info("https://example.com/video")
    except RuntimeError as exc:
        assert str(exc) == "yt-dlp returned no metadata"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected missing metadata error")
