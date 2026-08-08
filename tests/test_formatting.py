import json

from dlp.cli import ConsoleRenderer
from dlp.formatting import event_summary, format_bytes, format_eta, format_percent, format_speed
from dlp.models import JobState, ProgressEvent, ProgressPhase


def test_formatting_handles_bytes_speed_eta_and_percent() -> None:
    assert format_bytes(1024) == "1.0 KiB"
    assert format_speed(2048) == "2.0 KiB/s"
    assert format_eta(61) == "01:01"
    assert format_percent(42.25) == " 42.2%"
    assert event_summary(
        ProgressEvent(
            "job-1",
            ProgressPhase.DOWNLOADING,
            percent=42.25,
            speed_bytes=2048,
            eta_seconds=61,
            message="Downloading",
        )
    ) == "Downloading |  42.2% | 2.0 KiB/s | ETA 01:01"


def test_json_renderer_emits_one_machine_readable_event(capsys) -> None:
    renderer = ConsoleRenderer(json_mode=True)
    renderer.emit(
        ProgressEvent(
            "job-1",
            ProgressPhase.COMPLETED,
            percent=100,
            state=JobState.COMPLETED,
            message="Completed",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["type"] == "progress"
    assert payload["state"] == "completed"
