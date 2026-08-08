import json
from pathlib import Path

from dlp import __version__
from dlp.cli import EXIT_DEPENDENCY, EXIT_OK, _exit_code, build_parser
from dlp.models import (
    BatchResult,
    DownloadResult,
    JobState,
    ProgressEvent,
    ProgressPhase,
)


def test_parser_exposes_scriptable_overrides() -> None:
    args = build_parser().parse_args(
        [
            "download",
            "--profile",
            "music",
            "--audio-only",
            "--audio-format",
            "mp3",
            "--json",
            "--dry-run",
            "https://example.com/video",
        ]
    )
    assert args.profile == "music"
    assert args.audio_only is True
    assert args.audio_format == "mp3"
    assert args.json is True
    assert args.dry_run is True


def test_version_is_centralized() -> None:
    assert __version__ == "0.2.0"


def test_blocked_jobs_have_dependency_exit_code() -> None:
    result = BatchResult([DownloadResult("1", JobState.BLOCKED)])
    assert _exit_code(result) == EXIT_DEPENDENCY
    assert EXIT_OK == 0


def test_history_json_is_parseable(tmp_path: Path, monkeypatch, capsys) -> None:
    from dlp import cli
    from dlp.history import HistoryEntry, HistoryRepository

    history = HistoryRepository(tmp_path / "history.jsonl")
    history.append(
        HistoryEntry(
            "job-1",
            "https://example.com/video",
            JobState.COMPLETED,
            "2026-08-08T00:00:00+00:00",
            title="Video",
        )
    )
    monkeypatch.setattr(cli, "HistoryRepository", lambda: history)
    assert cli.main(["history", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == "job-1"


def test_download_json_never_emits_prompt_text(tmp_path: Path, monkeypatch, capsys) -> None:
    from dlp import cli

    class FakeRunner:
        def __init__(self, *, result_sink=None) -> None:
            self.result_sink = result_sink

        def run(self, requests, emit, *, cancel_event, dependency_prompt):
            assert dependency_prompt is None
            request = requests[0]
            emit(
                ProgressEvent(
                    job_id=request.job_id,
                    phase=ProgressPhase.BLOCKED,
                    message="Missing dependency: ffmpeg",
                    state=JobState.BLOCKED,
                )
            )
            return BatchResult(
                [
                    DownloadResult(
                        request.job_id,
                        JobState.BLOCKED,
                        error="Missing dependency: ffmpeg",
                    )
                ]
            )

    monkeypatch.setattr(cli, "QueueRunner", FakeRunner)
    assert (
        cli.main(
            [
                "download",
                "--json",
                "--output",
                str(tmp_path),
                "https://example.com/video",
            ]
        )
        == EXIT_DEPENDENCY
    )
    lines = capsys.readouterr().out.splitlines()
    assert lines
    assert all(isinstance(json.loads(line), dict) for line in lines)
    assert not any(line.startswith("Missing for") for line in lines)


def test_doctor_ui_check_json_is_machine_readable(capsys) -> None:
    from dlp import cli

    assert cli.main(["doctor", "--ui-check", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ready": True, "type": "ui_check"}
