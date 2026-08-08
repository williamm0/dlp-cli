from pathlib import Path

import pytest

from dlp.history import HistoryRepository, ProfileRepository
from dlp.models import DownloadRequest, DownloadResult, JobState, Settings


def test_history_redacts_urls_errors_and_retains_recent_entries(tmp_path: Path) -> None:
    repository = HistoryRepository(tmp_path / "history.jsonl", max_entries=2)
    for index in range(3):
        request = DownloadRequest(
            str(index),
            f"https://example.com/video?token=secret-{index}",
            Settings(),
        )
        result = DownloadResult(
            request.job_id,
            JobState.FAILED,
            error="Authorization: Bearer secret-token",
        )
        repository.record(request, result)

    entries = repository.load()
    assert len(entries) == 2
    assert entries[0].job_id == "2"
    assert "?" not in entries[0].url
    assert "secret-token" not in (entries[0].error or "")


def test_history_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    path.write_text("not json\n{}\n", encoding="utf-8")
    assert HistoryRepository(path).load() == []


def test_profiles_round_trip_and_validate_names(tmp_path: Path) -> None:
    repository = ProfileRepository(tmp_path / "profiles")
    settings = Settings(output_directory=tmp_path / "downloads", audio_only=True)
    repository.save("podcasts", settings)

    assert repository.names() == ["podcasts"]
    loaded = repository.load("podcasts")
    assert loaded.output_directory == tmp_path / "downloads"
    assert loaded.audio_only is True

    with pytest.raises(ValueError, match="profile name"):
        repository.save("../escape", settings)
