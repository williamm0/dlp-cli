from pathlib import Path

from dlp.dependencies import (
    DependencyManager,
    DependencyName,
    is_youtube_url,
    required_dependencies,
)
from dlp.models import Settings


def test_youtube_detection_covers_common_hosts() -> None:
    assert is_youtube_url("https://www.youtube.com/watch?v=1")
    assert is_youtube_url("https://youtu.be/1")
    assert is_youtube_url("https://music.youtube.com/watch?v=1")
    assert not is_youtube_url("https://example.com/video")
    assert not is_youtube_url("https://notyoutube.com/video")


def test_best_youtube_request_requires_ffmpeg_and_deno() -> None:
    requirements = required_dependencies("https://youtu.be/1", Settings())
    assert {DependencyName.FFMPEG, DependencyName.FFPROBE, DependencyName.DENO} <= requirements


def test_aria2c_is_checked_when_selected() -> None:
    requirements = required_dependencies(
        "https://example.com/video",
        Settings(quality_mode="custom", external_downloader="aria2c"),
    )

    assert DependencyName.ARIA2C in requirements


def test_install_without_consent_does_not_run_a_command() -> None:
    calls: list[tuple[object, ...]] = []

    def runner(*args: object, **kwargs: object) -> object:
        calls.append(args)
        return object()

    manager = DependencyManager(runner=runner)
    result = manager.install(DependencyName.DENO, consent=False)

    assert result.state.value == "declined"
    assert calls == []


def test_status_can_use_a_configured_ffmpeg_path(tmp_path: Path) -> None:
    binary = tmp_path / "ffmpeg"
    binary.write_text("placeholder", encoding="utf-8")
    status = DependencyManager().check(
        [DependencyName.FFMPEG],
        Settings(ffmpeg_path=binary),
    )[0]
    assert status.available is True
    assert status.path == str(binary)
