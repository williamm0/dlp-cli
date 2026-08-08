import threading

import pytest

from dlp.dependencies import DependencyName, required_dependencies
from dlp.diagnostics import sanitize_message
from dlp.engine import DownloadEngine
from dlp.models import DownloadRequest, DownloadResult, JobState, Settings
from dlp.options import OptionValidationError, validate_extra_args
from dlp.queue import QueueRunner


def test_runtime_redaction_handles_header_style_secrets() -> None:
    message = sanitize_message("Authorization: Bearer secret-token; Cookie: SID=abcd")
    assert "secret-token" not in message
    assert "abcd" not in message
    assert "[redacted]" in message


def test_runtime_redaction_handles_non_http_urls_and_terminal_controls() -> None:
    message = sanitize_message(
        "ftp://user:password@example.com/file\x1b]52;c;secret\x07"
    )
    assert "user" not in message
    assert "password" not in message
    assert "secret" not in message
    assert "\x1b" not in message


def test_settings_reject_wrong_boolean_and_proxy_types() -> None:
    with pytest.raises(ValueError, match="audio_only"):
        Settings.from_mapping({"audio_only": "false"})
    with pytest.raises(ValueError, match="proxy"):
        Settings.from_mapping({"proxy": "localhost:8080"})


def test_advanced_args_cannot_add_exec_postprocessors() -> None:
    with pytest.raises(OptionValidationError, match="controlled"):
        validate_extra_args(["--use-postprocessor", "Exec:cmd=echo unsafe"])
    with pytest.raises(OptionValidationError, match="controlled"):
        validate_extra_args(["--add-headers", "Authorization: Bearer secret"])
    with pytest.raises(OptionValidationError, match="controlled"):
        validate_extra_args(["--plugin-dirs", "/tmp/plugins"])
    with pytest.raises(OptionValidationError, match="controlled"):
        validate_extra_args(["--downloader", "/tmp/custom-downloader"])


def test_custom_split_format_requires_media_tools() -> None:
    settings = Settings(quality_mode="custom", format_selector="bestvideo+bestaudio")
    required = required_dependencies("https://example.com/video", settings)
    assert DependencyName.FFMPEG in required
    assert DependencyName.FFPROBE in required


def test_non_youtube_request_does_not_require_youtube_ejs() -> None:
    required = required_dependencies("https://example.com/video", Settings(quality_mode="custom"))
    assert DependencyName.YTDLP_EJS not in required


def test_external_downloader_is_restricted_to_supported_binary() -> None:
    with pytest.raises(ValueError, match="external_downloader"):
        Settings.from_mapping({"external_downloader": "/tmp/custom"})


class MetadataYoutubeDL:
    last_options = None

    def __init__(self, options):
        self.options = options
        type(self).last_options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def extract_info(self, url, download=False):
        assert download is False
        return {
            "webpage_url": url,
            "title": "A title",
            "uploader": "A creator",
            "duration": 91,
            "extractor_key": "Example",
            "id": "abc123",
            "formats": [
                {
                    "format_id": "137",
                    "ext": "mp4",
                    "resolution": "1080p",
                    "fps": 30,
                    "filesize": 1024,
                    "vcodec": "avc1",
                    "acodec": "none",
                    "format_note": "1080p",
                }
            ],
        }


def test_engine_extracts_display_safe_metadata() -> None:
    info = DownloadEngine(MetadataYoutubeDL).extract_info(
        "https://example.com/video?token=secret"
    )
    assert info.title == "A title"
    assert info.duration_seconds == 91
    assert "?" not in info.url


def test_engine_flat_playlist_uses_flat_extraction() -> None:
    DownloadEngine(MetadataYoutubeDL).extract_info(
        "https://example.com/playlist",
        flat_playlist=True,
    )
    assert MetadataYoutubeDL.last_options is not None
    assert MetadataYoutubeDL.last_options["extract_flat"] is True
    assert "noplaylist" not in MetadataYoutubeDL.last_options


def test_engine_lists_display_safe_formats() -> None:
    formats = DownloadEngine(MetadataYoutubeDL).list_formats("https://example.com/video")

    assert len(formats) == 1
    assert formats[0].format_id == "137"
    assert formats[0].resolution == "1080p"
    assert formats[0].filesize == 1024


class PostProcessYoutubeDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def download(self, _urls):
        hook = self.options["progress_hooks"][0]
        hook(
            {
                "status": "downloading",
                "downloaded_bytes": 1,
                "total_bytes": 2,
                "filename": "/tmp/video.part",
                "info": {"title": "Test"},
            }
        )
        hook({"status": "finished", "filename": "/tmp/video.webm", "info": {"title": "Test"}})
        self.options["postprocessor_hooks"][0](
            {"status": "finished", "filepath": "/tmp/video.mp3", "info_dict": {"title": "Test"}}
        )
        return 0


def test_engine_returns_postprocessed_output_path() -> None:
    request = DownloadRequest("job-1", "https://example.com/video", Settings(audio_only=True))
    result = DownloadEngine(PostProcessYoutubeDL).run(
        request, lambda _event: None, threading.Event()
    )
    assert result.state == JobState.COMPLETED
    assert result.output_path == "/tmp/video.mp3"


def test_queue_marks_all_trailing_items_canceled() -> None:
    class CancelingEngine:
        def run(self, request, emit, cancel_event):
            cancel_event.set()
            return DownloadResult(request.job_id, JobState.CANCELED, error="Canceled")

    class ReadyDependencies:
        def check_for_request(self, _url, _settings):
            return []

    requests = [
        DownloadRequest(str(index), f"https://example.com/{index}", Settings())
        for index in range(3)
    ]
    result = QueueRunner(CancelingEngine(), ReadyDependencies()).run(
        requests, lambda _event: None
    )
    assert [item.state for item in result.items] == [
        JobState.CANCELED,
        JobState.CANCELED,
        JobState.CANCELED,
    ]
