"""Textual screens for interactive downloads and saved settings."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterable
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from ..batch import parse_batch_text
from ..config import SettingsRepository, default_output_directory
from ..dependencies import DependencyManager, DependencyStatus
from ..diagnostics import sanitize_message, sanitize_url
from ..formatting import event_summary, format_eta, format_percent, format_speed
from ..models import (
    BatchResult,
    DownloadRequest,
    JobState,
    ProgressEvent,
    ProgressPhase,
    QueueItem,
    Settings,
)
from ..options import split_extra_args, validate_filename_template
from ..queue import QueueRunner


class DependencyDialog(ModalScreen[str | None]):
    """Ask for consent before invoking an OS package manager."""

    def __init__(self, statuses: list[DependencyStatus]) -> None:
        super().__init__()
        self.statuses = statuses

    def compose(self) -> ComposeResult:
        names = ", ".join(status.name.value for status in self.statuses)
        commands = "\n".join(
            "\n".join(
                (
                    f"{status.name.value}: {status.reason}",
                    f"  Install with: {_install_command_text(status)}",
                )
            )
            for status in self.statuses
        )
        with Container(id="dependency-dialog"):
            yield Label("Missing download dependencies", classes="section-title")
            yield Label(f"The selected job needs: {names}")
            yield Label("The app will ask the operating system package manager for consent.")
            yield Static(commands, classes="muted")
            with Horizontal(classes="button-row"):
                yield Button("Install", id="install-dependencies", variant="primary")
                yield Button("Retry", id="retry-dependencies")
                yield Button("Skip", id="skip-dependencies")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id)


def _install_command_text(status: DependencyStatus) -> str:
    if status.install_command:
        return " ".join(status.install_command)
    return status.manual_command or "No supported installer"


def _table_row_job_id(table_row: object) -> str | None:
    key = getattr(table_row, "key", None)
    value = getattr(key, "value", None)
    return value if isinstance(value, str) else None


class DownloaderApp(App[None]):
    """Queue-first terminal UI."""

    CSS_PATH = "styles.tcss"
    TITLE = "DLP"
    SUB_TITLE = "Clean yt-dlp downloads"

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel", show=True),
        Binding("r", "retry", "Retry", show=True),
        Binding("s", "focus_settings", "Settings", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, *, settings_only: bool = False) -> None:
        super().__init__()
        self.settings_only = settings_only
        self.repository = SettingsRepository()
        self.settings = self.repository.load()
        self.dependency_manager = DependencyManager()
        self.queue_runner = QueueRunner(dependency_manager=self.dependency_manager)
        self.items: dict[str, QueueItem] = {}
        self.pending_requests: list[DownloadRequest] = []
        self.pending_missing: list[DependencyStatus] = []
        self.cancel_event = threading.Event()
        self._run_active = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="settings-tab" if self.settings_only else "home-tab", id="tabs"):
            with TabPane("Download", id="home-tab"):
                yield Label("Paste one or more URLs", classes="section-title")
                yield TextArea(id="url-input", language=None)
                yield Label(
                    self._home_summary(),
                    id="home-summary",
                    classes="muted",
                )
                with Horizontal(classes="button-row"):
                    yield Button("Add to queue", id="add-button", variant="primary")
                    yield Button("Cancel active job", id="cancel-button")
                yield Static("Ready", id="home-status", classes="status")

            with TabPane("Queue", id="queue-tab"):
                yield Label("Download queue", classes="section-title")
                yield DataTable(id="queue-table", cursor_type="row")
                yield Button("Retry selected", id="retry-button")
                yield Static("No jobs yet", id="queue-status", classes="status")

            with TabPane("Settings", id="settings-tab"):
                yield from self._settings_form()

            with TabPane("Dependencies", id="dependencies-tab"):
                yield Label("Dependency status", classes="section-title")
                yield Static(self._dependency_summary(), id="dependency-status", classes="status")
                yield Button("Refresh", id="refresh-dependencies", variant="primary")

        yield Footer()

    def _settings_form(self) -> Iterable[Widget]:
        yield Label("Saved settings", classes="section-title")
        with VerticalScroll():
            yield Label("General", classes="subsection")
            yield Checkbox(
                "Resume partial files",
                value=self.settings.resume_partial_files,
                id="resume-partial-files",
            )
            with Horizontal(classes="form-row"):
                yield Label("Retries", classes="form-label")
                yield Input(
                    str(self.settings.retries),
                    id="retries",
                    classes="form-control",
                )

            yield Label("Quality and format", classes="subsection")
            with Horizontal(classes="form-row"):
                yield Label("Quality", classes="form-label")
                yield Select(
                    [("Best quality", "best"), ("Custom format", "custom")],
                    value=self.settings.quality_mode,
                    id="quality-select",
                    classes="form-control",
                )
            with Horizontal(classes="form-row"):
                yield Label("Format selector", classes="form-label")
                yield Input(
                    self.settings.format_selector,
                    id="format-selector",
                    classes="form-control",
                )
            with Horizontal(classes="form-row"):
                yield Label("Merge format", classes="form-label")
                yield Input(
                    self.settings.merge_output_format,
                    id="merge-output-format",
                    classes="form-control",
                    placeholder="auto, mp4, mkv, webm",
                )

            yield Label("Output", classes="subsection")
            with Horizontal(classes="form-row"):
                yield Label("Output folder", classes="form-label")
                yield Input(
                    str(self.settings.output_directory),
                    id="output-directory",
                    classes="form-control",
                )
            with Horizontal(classes="form-row"):
                yield Label("Filename template", classes="form-label")
                yield Input(
                    self.settings.filename_template,
                    id="filename-template",
                    classes="form-control",
                )

            yield Label("Subtitles and metadata", classes="subsection")
            with Horizontal(classes="form-row"):
                yield Label("Subtitles", classes="form-label")
                yield Select(
                    [("Off", "off"), ("Manual", "manual"), ("Automatic", "auto")],
                    value=self.settings.subtitles,
                    id="subtitles-select",
                    classes="form-control",
                )
            with Horizontal(classes="form-row"):
                yield Label("Subtitle languages", classes="form-label")
                yield Input(
                    ", ".join(self.settings.subtitle_languages),
                    id="subtitle-languages",
                    classes="form-control",
                    placeholder="en, no",
                )
            with Horizontal(classes="form-row"):
                yield Label("Playlist", classes="form-label")
                yield Select(
                    [("Download playlist", "all"), ("Single item", "single")],
                    value=self.settings.playlist_mode,
                    id="playlist-select",
                    classes="form-control",
                )
            with Horizontal(classes="form-row"):
                yield Label("Overwrite", classes="form-label")
                yield Select(
                    [("Skip existing files", "skip"), ("Overwrite files", "overwrite")],
                    value=self.settings.overwrite,
                    id="overwrite-select",
                    classes="form-control",
                )
            yield Checkbox("Audio only", value=self.settings.audio_only, id="audio-only")
            yield Checkbox(
                "Embed metadata",
                value=self.settings.embed_metadata,
                id="embed-metadata",
            )
            yield Checkbox(
                "Write thumbnail",
                value=self.settings.write_thumbnail,
                id="write-thumbnail",
            )
            yield Checkbox(
                "Embed thumbnail",
                value=self.settings.embed_thumbnail,
                id="embed-thumbnail",
            )
            with Horizontal(classes="form-row"):
                yield Label("Audio format", classes="form-label")
                yield Input(
                    self.settings.audio_format,
                    id="audio-format",
                    classes="form-control",
                    placeholder="best, mp3, opus",
                )
            with Horizontal(classes="form-row"):
                yield Label("Audio quality", classes="form-label")
                yield Input(
                    self.settings.audio_quality,
                    id="audio-quality",
                    classes="form-control",
                )

            yield Label("Authentication and network", classes="subsection")
            yield Label("Advanced yt-dlp arguments, one shell-style line", classes="form-label")
            yield TextArea(" ".join(self.settings.extra_args), id="extra-args")
            with Horizontal(classes="form-row"):
                yield Label("Browser cookies", classes="form-label")
                yield Input(
                    self.settings.cookies_from_browser or "",
                    id="cookies-browser",
                    classes="form-control",
                    placeholder="Chrome, Edge, Firefox, Safari",
                )
            with Horizontal(classes="form-row"):
                yield Label("Cookie file", classes="form-label")
                yield Input(
                    str(self.settings.cookies_file or ""),
                    id="cookies-file",
                    classes="form-control",
                )
            with Horizontal(classes="form-row"):
                yield Label("Proxy", classes="form-label")
                yield Input(
                    self.settings.proxy or "",
                    id="proxy",
                    classes="form-control",
                )
            with Horizontal(classes="form-row"):
                yield Label("Rate limit", classes="form-label")
                yield Input(
                    self.settings.rate_limit or "",
                    id="rate-limit",
                    classes="form-control",
                    placeholder="2M",
                )
            with Horizontal(classes="form-row"):
                yield Label("Socket timeout", classes="form-label")
                yield Input(
                    str(self.settings.socket_timeout or ""),
                    id="socket-timeout",
                    classes="form-control",
                    placeholder="seconds",
                )
            with Horizontal(classes="form-row"):
                yield Label("External downloader", classes="form-label")
                yield Select(
                    [("Built-in", None), ("aria2c", "aria2c")],
                    value=self.settings.external_downloader,
                    id="external-downloader",
                    classes="form-control",
                )

            yield Label("Dependencies", classes="subsection")
            with Horizontal(classes="form-row"):
                yield Label("JavaScript runtime", classes="form-label")
                yield Select(
                    [("Deno", "deno"), ("Automatic", "auto")],
                    value=self.settings.js_runtime,
                    id="js-runtime",
                    classes="form-control",
                )
            with Horizontal(classes="form-row"):
                yield Label("ffmpeg path", classes="form-label")
                yield Input(
                    str(self.settings.ffmpeg_path or ""),
                    id="ffmpeg-path",
                    classes="form-control",
                )
            with Horizontal(classes="form-row"):
                yield Label("ffprobe path", classes="form-label")
                yield Input(
                    str(self.settings.ffprobe_path or ""),
                    id="ffprobe-path",
                    classes="form-control",
                )

            with Horizontal(classes="button-row"):
                yield Button("Save settings", id="save-settings", variant="primary")
                yield Button("Reset defaults", id="reset-settings")
            yield Static(
                "Settings are loaded for the next job.",
                id="settings-status",
                classes="status",
            )

    def on_mount(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.add_columns("Title", "State", "Progress", "Speed", "ETA", "Result")
        self._refresh_dependencies()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "add-button":
            self._queue_from_input()
        elif button_id == "cancel-button":
            self.action_cancel()
        elif button_id == "retry-button":
            self.action_retry()
        elif button_id == "save-settings":
            self._save_settings()
        elif button_id == "reset-settings":
            self._reset_settings()
        elif button_id == "refresh-dependencies":
            self._refresh_dependencies()

    def _queue_from_input(self) -> None:
        text = self.query_one("#url-input", TextArea).text
        urls = parse_batch_text(text)
        if not urls:
            self.query_one("#home-status", Static).update("Paste at least one URL.")
            return
        requests = [
            DownloadRequest(uuid.uuid4().hex[:8], url, self.settings.clone()) for url in urls
        ]
        for request in requests:
            self.items[request.job_id] = QueueItem(request)
        self._refresh_queue()
        self._start_or_prompt(requests)

    def _start_or_prompt(self, requests: list[DownloadRequest]) -> None:
        missing = self._missing_for_requests(requests)
        if missing:
            self.pending_requests = requests
            self.pending_missing = missing
            self.push_screen(DependencyDialog(missing), self._dependency_decision)
            return
        self._start_download_worker(requests)

    def _missing_for_requests(self, requests: list[DownloadRequest]) -> list[DependencyStatus]:
        statuses: dict[str, DependencyStatus] = {}
        for request in requests:
            for status in self.dependency_manager.check_for_request(request.url, request.settings):
                if not status.available:
                    statuses[status.name.value] = status
        return list(statuses.values())

    def _dependency_decision(self, decision: str | None) -> None:
        if decision == "install-dependencies":
            self._install_and_start()
        elif decision == "retry-dependencies":
            self._retry_pending_requests()
        else:
            requests = list(self.pending_requests)
            self.pending_requests = []
            self.pending_missing = []
            self._start_download_worker(requests)

    @work(thread=True, exclusive=True)
    def _install_and_start(self) -> None:
        results = [
            self.dependency_manager.install(status.name, consent=True)
            for status in self.pending_missing
        ]
        message = "; ".join(result.message for result in results)
        requests = list(self.pending_requests)
        self.call_from_thread(self._after_install, message, requests)

    def _after_install(self, message: str, requests: list[DownloadRequest]) -> None:
        self.pending_requests = requests
        self.pending_missing = []
        self.query_one("#home-status", Static).update(message)
        self._retry_pending_requests()

    def _retry_pending_requests(self) -> None:
        requests = list(self.pending_requests)
        missing = self._missing_for_requests(requests)
        if missing:
            self.pending_missing = missing
            self.push_screen(DependencyDialog(missing), self._dependency_decision)
            return
        self.pending_requests = []
        self.pending_missing = []
        self._start_download_worker(requests)

    @work(thread=True, exclusive=True)
    def _start_download_worker(self, requests: list[DownloadRequest]) -> None:
        self._run_active = True
        self.cancel_event.clear()
        result = self.queue_runner.run(
            requests,
            self._emit_from_worker,
            cancel_event=self.cancel_event,
        )
        self.call_from_thread(self._finish_download, result)

    def _emit_from_worker(self, event: ProgressEvent) -> None:
        self.call_from_thread(self._handle_event, event)

    def _handle_event(self, event: ProgressEvent) -> None:
        item = self.items.get(event.job_id)
        if item is None:
            return
        item.progress = event
        if event.state:
            item.state = event.state
        if event.phase in {ProgressPhase.FAILED, ProgressPhase.BLOCKED, ProgressPhase.CANCELED}:
            item.error = event.message
        if event.filename:
            item.output_path = event.filename
        self._refresh_queue()
        self.query_one("#home-status", Static).update(event_summary(event))

    def _finish_download(self, result: BatchResult) -> None:
        self._run_active = False
        if any(item.state == JobState.CANCELED for item in result.items):
            message = "Canceled active job."
        elif result.failed_count:
            message = f"Finished with {result.failed_count} failed or blocked job(s)."
        else:
            message = f"Completed {len(result.items)} job(s)."
        self.query_one("#queue-status", Static).update(message)
        self.query_one("#home-status", Static).update(message)

    def _refresh_queue(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.clear()
        for item in self.items.values():
            event = item.progress
            title = sanitize_message(
                (event.title if event else None) or sanitize_url(item.request.url)
            )
            progress = format_percent(event.percent if event else None)
            speed = format_speed(event.speed_bytes) if event and event.speed_bytes else "-"
            eta = format_eta(event.eta_seconds) if event and event.eta_seconds is not None else "-"
            result = sanitize_message(item.error or item.output_path or "-")
            table.add_row(
                title[:48],
                item.state.value,
                progress,
                speed,
                eta,
                result[:48],
                key=item.request.job_id,
            )

    def _save_settings(self) -> None:
        try:
            quality = str(self.query_one("#quality-select", Select).value)
            subtitles = str(self.query_one("#subtitles-select", Select).value)
            extra = split_extra_args(self.query_one("#extra-args", TextArea).text)
            self.settings.quality_mode = quality
            self.settings.subtitles = subtitles
            self.settings.format_selector = self.query_one("#format-selector", Input).value.strip()
            if quality == "best":
                self.settings.format_selector = "bestvideo*+bestaudio/best"
            elif not self.settings.format_selector:
                raise ValueError("custom format selector cannot be empty")
            self.settings.merge_output_format = (
                self.query_one("#merge-output-format", Input).value.strip() or "auto"
            )
            self.settings.output_directory = Path(
                self.query_one("#output-directory", Input).value
            ).expanduser()
            filename_template = self.query_one("#filename-template", Input).value.strip()
            self.settings.filename_template = validate_filename_template(filename_template)
            self.settings.resume_partial_files = self.query_one(
                "#resume-partial-files", Checkbox
            ).value
            self.settings.retries = max(0, int(self.query_one("#retries", Input).value.strip()))
            self.settings.audio_only = self.query_one("#audio-only", Checkbox).value
            self.settings.embed_metadata = self.query_one("#embed-metadata", Checkbox).value
            self.settings.write_thumbnail = self.query_one("#write-thumbnail", Checkbox).value
            self.settings.embed_thumbnail = self.query_one("#embed-thumbnail", Checkbox).value
            self.settings.playlist_mode = str(self.query_one("#playlist-select", Select).value)
            self.settings.overwrite = str(self.query_one("#overwrite-select", Select).value)
            self.settings.subtitle_languages = [
                item.strip()
                for item in self.query_one("#subtitle-languages", Input).value.split(",")
                if item.strip()
            ]
            self.settings.audio_format = (
                self.query_one("#audio-format", Input).value.strip() or "best"
            )
            self.settings.audio_quality = (
                self.query_one("#audio-quality", Input).value.strip() or "5"
            )
            self.settings.extra_args = extra
            browser = self.query_one("#cookies-browser", Input).value.strip()
            cookies = self.query_one("#cookies-file", Input).value.strip()
            proxy = self.query_one("#proxy", Input).value.strip()
            self.settings.cookies_from_browser = browser or None
            self.settings.cookies_file = Path(cookies).expanduser() if cookies else None
            self.settings.proxy = proxy or None
            rate_limit = self.query_one("#rate-limit", Input).value.strip()
            socket_timeout = self.query_one("#socket-timeout", Input).value.strip()
            self.settings.rate_limit = rate_limit or None
            self.settings.socket_timeout = max(1, int(socket_timeout)) if socket_timeout else None
            external_downloader = self.query_one("#external-downloader", Select).value
            self.settings.external_downloader = (
                str(external_downloader) if external_downloader else None
            )
            self.settings.js_runtime = str(self.query_one("#js-runtime", Select).value)
            ffmpeg = self.query_one("#ffmpeg-path", Input).value.strip()
            ffprobe = self.query_one("#ffprobe-path", Input).value.strip()
            self.settings.ffmpeg_path = Path(ffmpeg).expanduser() if ffmpeg else None
            self.settings.ffprobe_path = Path(ffprobe).expanduser() if ffprobe else None
            self.repository.save(self.settings)
            self.query_one("#settings-status", Static).update(f"Saved to {self.repository.path}")
            self.query_one("#home-summary", Label).update(self._home_summary())
        except (ValueError, OSError) as exc:
            self.query_one("#settings-status", Static).update(f"Settings error: {exc}")

    def _reset_settings(self) -> None:
        self.settings = Settings(output_directory=default_output_directory())
        self._sync_settings_form()
        self.query_one("#settings-status", Static).update("Defaults restored. Save to keep them.")

    def _sync_settings_form(self) -> None:
        self.query_one("#quality-select", Select).value = self.settings.quality_mode
        self.query_one("#format-selector", Input).value = self.settings.format_selector
        self.query_one("#merge-output-format", Input).value = self.settings.merge_output_format
        self.query_one("#subtitles-select", Select).value = self.settings.subtitles
        self.query_one("#subtitle-languages", Input).value = ", ".join(
            self.settings.subtitle_languages
        )
        self.query_one("#playlist-select", Select).value = self.settings.playlist_mode
        self.query_one("#overwrite-select", Select).value = self.settings.overwrite
        self.query_one("#output-directory", Input).value = str(self.settings.output_directory)
        self.query_one("#filename-template", Input).value = self.settings.filename_template
        self.query_one("#resume-partial-files", Checkbox).value = self.settings.resume_partial_files
        self.query_one("#retries", Input).value = str(self.settings.retries)
        self.query_one("#audio-only", Checkbox).value = self.settings.audio_only
        self.query_one("#embed-metadata", Checkbox).value = self.settings.embed_metadata
        self.query_one("#write-thumbnail", Checkbox).value = self.settings.write_thumbnail
        self.query_one("#embed-thumbnail", Checkbox).value = self.settings.embed_thumbnail
        self.query_one("#audio-format", Input).value = self.settings.audio_format
        self.query_one("#audio-quality", Input).value = self.settings.audio_quality
        self.query_one("#extra-args", TextArea).text = " ".join(self.settings.extra_args)
        self.query_one("#cookies-browser", Input).value = self.settings.cookies_from_browser or ""
        self.query_one("#cookies-file", Input).value = str(self.settings.cookies_file or "")
        self.query_one("#proxy", Input).value = self.settings.proxy or ""
        self.query_one("#rate-limit", Input).value = self.settings.rate_limit or ""
        self.query_one("#socket-timeout", Input).value = str(self.settings.socket_timeout or "")
        self.query_one("#external-downloader", Select).value = self.settings.external_downloader
        self.query_one("#js-runtime", Select).value = self.settings.js_runtime
        self.query_one("#ffmpeg-path", Input).value = str(self.settings.ffmpeg_path or "")
        self.query_one("#ffprobe-path", Input).value = str(self.settings.ffprobe_path or "")
        self.query_one("#home-summary", Label).update(self._home_summary())

    def _home_summary(self) -> str:
        quality = "Best" if self.settings.quality_mode == "best" else "Custom"
        return f"{quality} quality • saves to {self.settings.output_directory}"

    def _refresh_dependencies(self) -> None:
        statuses = self.dependency_manager.check_for_request(
            "https://www.youtube.com/", self.settings
        )
        self.query_one("#dependency-status", Static).update(self._dependency_summary(statuses))

    def _dependency_summary(self, statuses: list[DependencyStatus] | None = None) -> str:
        statuses = statuses or self.dependency_manager.check_for_request(
            "https://www.youtube.com/", self.settings
        )
        return "\n".join(
            f"{status.name.value}: {'ready' if status.available else 'missing'}"
            for status in sorted(statuses, key=lambda item: item.name.value)
        )

    def action_cancel(self) -> None:
        if self._run_active:
            self.cancel_event.set()
            self.query_one("#home-status", Static).update("Canceling active job...")

    def action_retry(self) -> None:
        if self._run_active:
            self.query_one("#queue-status", Static).update("Wait for the active job to finish.")
            return
        table = self.query_one("#queue-table", DataTable)
        row = table.cursor_row
        rows = list(table.ordered_rows)
        if row < 0 or row >= len(rows):
            row = next(
                (
                    index
                    for index, table_row in enumerate(rows)
                    if self._is_retryable_row(table_row)
                ),
                -1,
            )
        if row < 0 or row >= len(rows):
            self.query_one("#queue-status", Static).update("Select a failed job to retry.")
            return
        job_id = _table_row_job_id(rows[row])
        if job_id is None:
            self.query_one("#queue-status", Static).update("Select a failed job to retry.")
            return
        item = self.items.get(job_id)
        if item is None or item.state not in {
            JobState.FAILED,
            JobState.BLOCKED,
            JobState.CANCELED,
        }:
            self.query_one("#queue-status", Static).update("Select a failed job to retry.")
            return
        item.retry_count += 1
        item.state = JobState.QUEUED
        item.progress = None
        item.error = None
        item.output_path = None
        item.request = DownloadRequest(
            job_id=item.request.job_id,
            url=item.request.url,
            settings=item.request.settings.clone(),
        )
        self._refresh_queue()
        self._start_or_prompt([item.request])

    def _is_retryable_row(self, table_row: object) -> bool:
        job_id = _table_row_job_id(table_row)
        item = self.items.get(job_id) if job_id else None
        return item is not None and item.state in {
            JobState.FAILED,
            JobState.BLOCKED,
            JobState.CANCELED,
        }

    def action_focus_settings(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "settings-tab"
