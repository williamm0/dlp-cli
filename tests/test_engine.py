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
