"""Textual screens for interactive downloads and saved settings."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterable
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
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

from ..batch import normalize_urls, parse_batch_text
from ..config import ConfigError, SettingsRepository, default_output_directory
from ..dependencies import DependencyManager, DependencyStatus
from ..diagnostics import sanitize_message, sanitize_url
from ..engine import DownloadEngine
from ..formatting import event_summary, format_eta, format_percent, format_speed
from ..history import HistoryEntry, HistoryRepository, ProfileRepository
from ..models import (
    BatchResult,
    DownloadRequest,
    FormatInfo,
    JobState,
    MediaInfo,
    ProgressEvent,
    ProgressPhase,
    QueueItem,
    Settings,
)
from ..options import compile_ydl_options, split_extra_args, validate_filename_template
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
            with VerticalScroll(id="dependency-command-scroll"):
                yield Static(commands, classes="muted")
            with Vertical(id="dependency-actions"):
                yield Button("Install", id="install-dependencies", variant="primary")
                yield Button("Retry", id="retry-dependencies")
                yield Button("Skip", id="skip-dependencies")

    def on_mount(self) -> None:
        # Retrying is the least surprising default; installing is always an
        # explicit keyboard action.
        self.query_one("#retry-dependencies", Button).focus()

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
        Binding("ctrl+r", "retry", "Retry", show=True),
        Binding("ctrl+s", "focus_settings", "Settings", show=True),
        Binding("ctrl+h", "focus_history", "History", show=True),
        Binding("ctrl+d", "focus_download", "Download", show=True),
        Binding("ctrl+f", "focus_filter", "Filter queue", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
    ]

    def __init__(self, *, settings_only: bool = False) -> None:
        super().__init__()
        self.settings_only = settings_only
        self.repository = SettingsRepository()
        self.settings = self.repository.load_or_default()
        self.profile_repository = ProfileRepository()
        self.history_repository = HistoryRepository()
        self.active_profile: str | None = None
        self.queue_filter = ""
        self.history_entries: list[HistoryEntry] = []
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
                with Horizontal(classes="form-row"):
                    yield Label("Profile", classes="form-label")
                    yield Select(
                        [
                            ("Default settings", ""),
                            *[(name, name) for name in self.profile_repository.names()],
                        ],
                        value="",
                        id="profile-select",
                        classes="form-control",
                    )
                yield Label(
                    self._home_summary(),
                    id="home-summary",
                    classes="muted",
                )
                with Horizontal(classes="button-row"):
                    yield Button("Add to queue", id="add-button", variant="primary")
                    yield Button("Preview", id="preview-button")
                    yield Button("Formats", id="formats-button")
                    yield Button("Cancel active job", id="cancel-button")
                yield Static("Ready", id="home-status", classes="status")
                yield Static(
                    "Metadata preview appears here.", id="preview-status", classes="status"
                )
                yield Static(
                    "Format list appears here.", id="formats-status", classes="status"
                )

            with TabPane("Queue", id="queue-tab"):
                yield Label("Download queue", classes="section-title")
                yield Input(
                    placeholder="Filter by title, URL, or state",
                    id="queue-filter",
                )
                yield DataTable(id="queue-table", cursor_type="row")
                yield Button("Retry selected", id="retry-button")
                yield Static("No jobs yet", id="queue-status", classes="status")

            with TabPane("History", id="history-tab"):
                yield Label("Recent downloads", classes="section-title")
                yield DataTable(id="history-table", cursor_type="row")
                yield Button("Clear history", id="clear-history")
                yield Static(
                    "History is stored locally and redacted.",
                    id="history-status",
                    classes="status",
                )

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
            with Horizontal(classes="form-row"):
                yield Label("Fragment retries", classes="form-label")
                yield Input(
                    str(self.settings.fragment_retries),
                    id="fragment-retries",
                    classes="form-control",
                )
            with Horizontal(classes="form-row"):
                yield Label("Parallel fragments", classes="form-label")
                yield Input(
                    str(self.settings.concurrent_fragments),
                    id="concurrent-fragments",
                    classes="form-control",
                    placeholder="1-32",
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
            with Horizontal(classes="form-row"):
                yield Label("Download archive", classes="form-label")
                yield Input(
                    str(self.settings.download_archive or ""),
                    id="download-archive",
                    classes="form-control",
                    placeholder="skip URLs already downloaded",
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
            yield Checkbox(
                "Write info JSON",
                value=self.settings.write_info_json,
                id="write-info-json",
            )
            yield Checkbox(
                "Write description",
                value=self.settings.write_description,
                id="write-description",
            )
            yield Checkbox(
                "Fetch comments",
                value=self.settings.write_comments,
                id="write-comments",
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
            yield Label("Advanced yt-dlp options", classes="subsection")
            yield Input(
                placeholder="Search the safe option guide",
                id="advanced-search",
                classes="form-control",
            )
            yield Static(
                self._advanced_options_help(),
                id="advanced-help",
                classes="muted",
            )
            yield Label("Arguments, one shell-style line", classes="form-label")
            yield TextArea(" ".join(self.settings.extra_args), id="extra-args")
            with Horizontal(classes="button-row"):
                yield Button("Validate advanced options", id="validate-extra-args")
            yield Static(
                "Advanced options have not been validated yet.",
                id="advanced-status",
                classes="status",
            )
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

        with Horizontal(classes="button-row", id="settings-actions"):
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
        history_table = self.query_one("#history-table", DataTable)
        history_table.add_columns("Time", "State", "Title", "URL", "Result")
        self._refresh_history()
        self._refresh_dependencies()
        if self.settings_only:
            self.call_after_refresh(self.query_one("#resume-partial-files", Checkbox).focus)
        else:
            self.call_after_refresh(self.query_one("#url-input", TextArea).focus)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "add-button":
            self._queue_from_input()
        elif button_id == "preview-button":
            self._preview_from_input()
        elif button_id == "formats-button":
            self._formats_from_input()
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
        elif button_id == "validate-extra-args":
            self._validate_extra_args()
        elif button_id == "clear-history":
            self.history_repository.clear()
            self.history_entries = []
            self._refresh_history()
            self.query_one("#history-status", Static).update("History cleared.")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "queue-filter":
            self.queue_filter = event.value.strip().lower()
            self._refresh_queue()
        elif event.input.id == "advanced-search":
            self.query_one("#advanced-help", Static).update(
                self._advanced_options_help(event.value)
            )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "profile-select":
            self._select_profile(str(event.value or ""))

    def _select_profile(self, name: str) -> None:
        try:
            settings = (
                self.repository.load_or_default()
                if not name
                else self.profile_repository.load(name)
            )
        except (ConfigError, OSError, ValueError) as exc:
            self.query_one("#home-status", Static).update(
                f"Profile error: {sanitize_message(str(exc))}"
            )
            return
        self.active_profile = name or None
        self.settings = settings
        self._sync_settings_form()
        self._refresh_dependencies()
        self.query_one("#home-summary", Label).update(self._home_summary())
        self.query_one("#home-status", Static).update(
            f"Loaded {name or 'default'} settings for the next job."
        )

    def _preview_from_input(self) -> None:
        try:
            urls = normalize_urls(parse_batch_text(self.query_one("#url-input", TextArea).text))
        except ValueError as exc:
            self.query_one("#preview-status", Static).update(
                f"Preview failed: {sanitize_message(str(exc))}"
            )
            return
        if not urls:
            self.query_one("#preview-status", Static).update("Paste a URL to preview.")
            return
        self.query_one("#preview-status", Static).update("Extracting metadata...")
        self._preview_url(urls[0], self.settings.clone())

    def _formats_from_input(self) -> None:
        try:
            urls = normalize_urls(parse_batch_text(self.query_one("#url-input", TextArea).text))
        except ValueError as exc:
            self.query_one("#formats-status", Static).update(
                f"Formats failed: {sanitize_message(str(exc))}"
            )
            return
        self.query_one("#formats-status", Static).update("Extracting available formats...")
        self._list_formats(urls[0], self.settings.clone())

    @work(thread=True, exclusive=True)
    def _preview_url(self, url: str, settings: Settings) -> None:
        try:
            info = DownloadEngine().extract_info(url, settings)
        except (RuntimeError, ValueError) as exc:
            self.call_from_thread(
                self._show_preview_error,
                sanitize_message(str(exc)),
            )
            return
        self.call_from_thread(self._show_preview, info)

    def _show_preview(self, info: MediaInfo) -> None:
        playlist = f" · {info.item_count or '?'} items" if info.is_playlist else ""
        self.query_one("#preview-status", Static).update(
            f"{info.title or 'Untitled'}\n"
            f"{info.uploader or info.channel or 'Unknown creator'} · "
            f"{_format_duration(info.duration_seconds)}{playlist}"
        )

    def _show_preview_error(self, message: str) -> None:
        self.query_one("#preview-status", Static).update(f"Preview failed: {message}")

    @work(thread=True, exclusive=True)
    def _list_formats(self, url: str, settings: Settings) -> None:
        try:
            formats = DownloadEngine().list_formats(url, settings)
        except (RuntimeError, ValueError) as exc:
            self.call_from_thread(self._show_formats_error, sanitize_message(str(exc)))
            return
        self.call_from_thread(self._show_formats, formats)

    def _show_formats(self, formats: list[FormatInfo]) -> None:
        if not formats:
            self.query_one("#formats-status", Static).update("No formats reported.")
            return
        lines = [
            f"{item.format_id}: {item.ext or '-'} {item.resolution or '-'} "
            f"{item.video_codec or '-'} / {item.audio_codec or '-'}"
            for item in formats[:16]
        ]
        suffix = f"\n…and {len(formats) - 16} more" if len(formats) > 16 else ""
        self.query_one("#formats-status", Static).update("\n".join(lines) + suffix)

    def _show_formats_error(self, message: str) -> None:
        self.query_one("#formats-status", Static).update(f"Formats failed: {message}")

    def _queue_from_input(self) -> None:
        text = self.query_one("#url-input", TextArea).text
        try:
            urls = normalize_urls(parse_batch_text(text))
        except ValueError as exc:
            self.query_one("#home-status", Static).update(sanitize_message(str(exc)))
            return
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
        for download_result in result.items:
            queue_item = self.items.get(download_result.job_id)
            if queue_item is not None:
                self.history_repository.record(
                    queue_item.request,
                    download_result,
                    profile=self.active_profile,
                    retry_count=queue_item.retry_count,
                )
        self._refresh_history()
        if any(item.state == JobState.CANCELED for item in result.items):
            message = "Canceled active job."
        elif result.failed_count:
            message = f"Finished with {result.failed_count} failed or blocked job(s)."
        else:
            message = f"Completed {len(result.items)} job(s)."
        self.query_one("#queue-status", Static).update(message)
        self.query_one("#home-status", Static).update(message)

    def _advanced_options_help(self, query: str = "") -> str:
        options = {
            "--format-sort res,fps": "prefer higher resolution/frame rate when formats tie",
            "--extractor-args NAME:key=value": "pass an extractor-specific option",
            "--geo-bypass": "try the extractor's geo-bypass behavior",
            "--live-from-start": "include the live stream from its start when supported",
            "--sleep-interval 2": "wait between requests",
            "--extractor-retries 3": "retry extractor requests",
            "--no-check-certificates": "allow invalid TLS certificates (use only when necessary)",
        }
        needle = query.strip().lower()
        rows = [
            f"{name} — {description}"
            for name, description in options.items()
            if not needle or needle in name.lower() or needle in description.lower()
        ]
        return "\n".join(rows) or (
            "No matching safe examples. Common app-owned options are controlled above."
        )

    def _validate_extra_args(self) -> None:
        try:
            args = split_extra_args(self.query_one("#extra-args", TextArea).text)
            candidate = self.settings.clone()
            candidate.extra_args = args
            compile_ydl_options(candidate, lambda _data: None, object())
        except (ValueError, RuntimeError) as exc:
            self.query_one("#advanced-status", Static).update(
                f"Advanced options error: {sanitize_message(str(exc))}"
            )
            return
        self.query_one("#advanced-status", Static).update(
            f"Validated {len(args)} advanced token(s)."
        )

    def _refresh_queue(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        selected_job_id: str | None = None
        rows = list(table.ordered_rows)
        if 0 <= table.cursor_row < len(rows):
            selected_job_id = _table_row_job_id(rows[table.cursor_row])
        table.clear()
        visible_job_ids: list[str] = []
        for item in self.items.values():
            event = item.progress
            title = sanitize_message(
                (event.title if event else None) or sanitize_url(item.request.url)
            )
            progress = format_percent(event.percent if event else None)
            speed = format_speed(event.speed_bytes) if event and event.speed_bytes else "-"
            eta = format_eta(event.eta_seconds) if event and event.eta_seconds is not None else "-"
            result = sanitize_message(item.error or item.output_path or "-")
            searchable = " ".join(
                (title, item.request.url, item.state.value, result)
            ).lower()
            if self.queue_filter and self.queue_filter not in searchable:
                continue
            table.add_row(
                title[:48],
                item.state.value,
                progress,
                speed,
                eta,
                result[:48],
                key=item.request.job_id,
            )
            visible_job_ids.append(item.request.job_id)
        if selected_job_id in visible_job_ids:
            table.move_cursor(row=visible_job_ids.index(selected_job_id), animate=False)

    def _refresh_history(self) -> None:
        self.history_entries = self.history_repository.load(limit=100)
        table = self.query_one("#history-table", DataTable)
        table.clear()
        for index, entry in enumerate(self.history_entries):
            table.add_row(
                entry.timestamp.replace("T", " ").replace("+00:00", "Z"),
                entry.state.value,
                (entry.title or "-")[:36],
                sanitize_url(entry.url)[:42],
                (entry.error or entry.output_path or "-")[:42],
                key=f"history-{index}",
            )

    def _save_settings(self) -> None:
        try:
            candidate = self.settings.clone()
            quality = str(self.query_one("#quality-select", Select).value)
            subtitles = str(self.query_one("#subtitles-select", Select).value)
            extra = split_extra_args(self.query_one("#extra-args", TextArea).text)
            candidate.quality_mode = quality
            candidate.subtitles = subtitles
            candidate.format_selector = self.query_one("#format-selector", Input).value.strip()
            if quality == "best":
                candidate.format_selector = "bestvideo*+bestaudio/best"
            elif not candidate.format_selector:
                raise ValueError("custom format selector cannot be empty")
            candidate.merge_output_format = (
                self.query_one("#merge-output-format", Input).value.strip() or "auto"
            )
            candidate.output_directory = Path(
                self.query_one("#output-directory", Input).value
            ).expanduser()
            archive = self.query_one("#download-archive", Input).value.strip()
            candidate.download_archive = Path(archive).expanduser() if archive else None
            filename_template = self.query_one("#filename-template", Input).value.strip()
            candidate.filename_template = validate_filename_template(filename_template)
            candidate.resume_partial_files = self.query_one(
                "#resume-partial-files", Checkbox
            ).value
            candidate.retries = max(0, int(self.query_one("#retries", Input).value.strip()))
            candidate.fragment_retries = max(
                0, int(self.query_one("#fragment-retries", Input).value.strip())
            )
            candidate.concurrent_fragments = int(
                self.query_one("#concurrent-fragments", Input).value.strip()
            )
            candidate.audio_only = self.query_one("#audio-only", Checkbox).value
            candidate.embed_metadata = self.query_one("#embed-metadata", Checkbox).value
            candidate.write_thumbnail = self.query_one("#write-thumbnail", Checkbox).value
            candidate.embed_thumbnail = self.query_one("#embed-thumbnail", Checkbox).value
            candidate.write_info_json = self.query_one("#write-info-json", Checkbox).value
            candidate.write_description = self.query_one("#write-description", Checkbox).value
            candidate.write_comments = self.query_one("#write-comments", Checkbox).value
            candidate.playlist_mode = str(self.query_one("#playlist-select", Select).value)
            candidate.overwrite = str(self.query_one("#overwrite-select", Select).value)
            candidate.subtitle_languages = [
                item.strip()
                for item in self.query_one("#subtitle-languages", Input).value.split(",")
                if item.strip()
            ]
            candidate.audio_format = (
                self.query_one("#audio-format", Input).value.strip() or "best"
            )
            candidate.audio_quality = (
                self.query_one("#audio-quality", Input).value.strip() or "5"
            )
            candidate.extra_args = extra
            browser = self.query_one("#cookies-browser", Input).value.strip()
            cookies = self.query_one("#cookies-file", Input).value.strip()
            proxy = self.query_one("#proxy", Input).value.strip()
            candidate.cookies_from_browser = browser or None
            candidate.cookies_file = Path(cookies).expanduser() if cookies else None
            candidate.proxy = proxy or None
            rate_limit = self.query_one("#rate-limit", Input).value.strip()
            socket_timeout = self.query_one("#socket-timeout", Input).value.strip()
            candidate.rate_limit = rate_limit or None
            candidate.socket_timeout = max(1, int(socket_timeout)) if socket_timeout else None
            external_downloader = self.query_one("#external-downloader", Select).value
            candidate.external_downloader = (
                str(external_downloader) if external_downloader else None
            )
            candidate.js_runtime = str(self.query_one("#js-runtime", Select).value)
            ffmpeg = self.query_one("#ffmpeg-path", Input).value.strip()
            ffprobe = self.query_one("#ffprobe-path", Input).value.strip()
            candidate.ffmpeg_path = Path(ffmpeg).expanduser() if ffmpeg else None
            candidate.ffprobe_path = Path(ffprobe).expanduser() if ffprobe else None
            candidate = Settings.from_mapping(candidate.to_mapping())
            self.repository.save(candidate)
            self.settings = candidate
            self.query_one("#settings-status", Static).update(f"Saved to {self.repository.path}")
            self.query_one("#home-summary", Label).update(self._home_summary())
            self._refresh_dependencies()
        except (ValueError, OSError) as exc:
            self.query_one("#settings-status", Static).update(f"Settings error: {exc}")

    def _reset_settings(self) -> None:
        self.settings = Settings(output_directory=default_output_directory())
        self._sync_settings_form()
        self.query_one("#settings-status", Static).update("Defaults restored. Save to keep them.")
        self._refresh_dependencies()

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
        self.query_one("#fragment-retries", Input).value = str(self.settings.fragment_retries)
        self.query_one("#concurrent-fragments", Input).value = str(
            self.settings.concurrent_fragments
        )
        self.query_one("#audio-only", Checkbox).value = self.settings.audio_only
        self.query_one("#embed-metadata", Checkbox).value = self.settings.embed_metadata
        self.query_one("#write-thumbnail", Checkbox).value = self.settings.write_thumbnail
        self.query_one("#embed-thumbnail", Checkbox).value = self.settings.embed_thumbnail
        self.query_one("#write-info-json", Checkbox).value = self.settings.write_info_json
        self.query_one("#write-description", Checkbox).value = self.settings.write_description
        self.query_one("#write-comments", Checkbox).value = self.settings.write_comments
        self.query_one("#audio-format", Input).value = self.settings.audio_format
        self.query_one("#audio-quality", Input).value = self.settings.audio_quality
        self.query_one("#extra-args", TextArea).text = " ".join(self.settings.extra_args)
        self.query_one("#advanced-search", Input).value = ""
        self.query_one("#advanced-help", Static).update(self._advanced_options_help())
        self.query_one("#advanced-status", Static).update(
            "Advanced options have not been validated yet."
        )
        self.query_one("#download-archive", Input).value = str(
            self.settings.download_archive or ""
        )
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
        self.call_after_refresh(self.query_one("#resume-partial-files", Checkbox).focus)

    def action_focus_history(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "history-tab"
        self.call_after_refresh(self.query_one("#history-table", DataTable).focus)

    def action_focus_download(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "home-tab"
        self.call_after_refresh(self.query_one("#url-input", TextArea).focus)

    def action_focus_filter(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "queue-tab"
        self.call_after_refresh(self.query_one("#queue-filter", Input).focus)


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "unknown duration"
    minutes, remaining = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining:02d}"
    return f"{minutes:02d}:{remaining:02d}"
