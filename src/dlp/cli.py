"""Command-line entrypoint for the dlp downloader."""

from __future__ import annotations

import argparse
import sys
import threading
import uuid
from collections.abc import Sequence
from pathlib import Path

from .batch import normalize_urls, read_batch_file
from .config import ConfigError, SettingsRepository
from .dependencies import DependencyManager, DependencyStatus
from .diagnostics import sanitize_message, sanitize_url
from .formatting import event_summary
from .models import BatchResult, DownloadRequest, JobState, ProgressEvent, ProgressPhase
from .queue import QueueRunner

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INVALID = 2
EXIT_DEPENDENCY = 3
EXIT_CANCELED = 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dlp",
        description="Clean yt-dlp downloads from your terminal",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    subparsers = parser.add_subparsers(dest="command")

    download = subparsers.add_parser("download", help="download one or more URLs")
    download.add_argument("urls", nargs="+", help="URL supported by yt-dlp")

    batch = subparsers.add_parser("batch", help="download URLs from a text file")
    batch.add_argument("file", type=Path)

    subparsers.add_parser("settings", help="open the saved settings screen")
    doctor = subparsers.add_parser("doctor", help="check download dependencies")
    doctor.add_argument(
        "--install",
        action="store_true",
        help="ask before installing missing tools",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            return _run_tui()
        if args.command == "download":
            return _run_urls(args.urls)
        if args.command == "batch":
            return _run_batch(args.file)
        if args.command == "settings":
            return _run_tui(settings_only=True)
        if args.command == "doctor":
            return _run_doctor(args.install)
    except (ConfigError, OSError, ValueError) as exc:
        print(f"Error: {sanitize_message(str(exc))}", file=sys.stderr)
        return EXIT_INVALID
    return EXIT_INVALID


def _run_urls(values: list[str]) -> int:
    return _run_requests(normalize_urls(values))


def _run_batch(path: Path) -> int:
    urls = read_batch_file(path)
    if not urls:
        raise ValueError("batch file contains no URLs")
    return _run_requests(urls)


def _run_requests(urls: list[str]) -> int:
    settings = SettingsRepository().load()
    requests = [
        DownloadRequest(job_id=uuid.uuid4().hex[:8], url=url, settings=settings.clone())
        for url in urls
    ]
    renderer = ConsoleRenderer()
    cancel_event = threading.Event()

    try:
        result = QueueRunner().run(
            requests,
            renderer.emit,
            cancel_event=cancel_event,
            dependency_prompt=_prompt_for_dependencies,
        )
    except KeyboardInterrupt:
        cancel_event.set()
        print("\nCanceled", file=sys.stderr)
        return EXIT_CANCELED
    return _exit_code(result)


def _run_doctor(allow_install: bool) -> int:
    settings = SettingsRepository().load()
    manager = DependencyManager()
    requirements = {
        status.name
        for status in manager.check_for_request("https://www.youtube.com/", settings)
    }
    statuses = manager.check(requirements, settings)
    for status in sorted(statuses, key=lambda item: item.name.value):
        state = "ready" if status.available else "missing"
        version = f" ({status.version})" if status.version else ""
        print(f"{status.name.value:12} {state:7}{version}  {status.reason}")
        if not status.available and allow_install:
            if _prompt_for_status(status):
                result = manager.install(status.name, consent=True)
                print(result.message)
    if allow_install:
        statuses = manager.check(requirements, settings)
    return EXIT_OK if all(status.available for status in statuses) else EXIT_DEPENDENCY


def _prompt_for_dependencies(request: DownloadRequest, statuses: list[DependencyStatus]) -> bool:
    names = ", ".join(status.name.value for status in statuses)
    print(f"Missing for {sanitize_url(request.url)}: {names}")
    for status in statuses:
        if status.install_command:
            command = " ".join(status.install_command)
        else:
            command = status.manual_command
        print(f"  {status.name.value}: {status.reason}. Install with: {command}")
    return all(_prompt_for_status(status) for status in statuses)


def _prompt_for_status(status: DependencyStatus) -> bool:
    try:
        answer = input(f"Install {status.name.value}? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in {"y", "yes"}


class ConsoleRenderer:
    """Render compact progress summaries without exposing yt-dlp output."""

    def __init__(self) -> None:
        self._last_line_length = 0

    def emit(self, event: ProgressEvent) -> None:
        label = sanitize_message(event.title or event.job_id)
        summary = event_summary(event)
        line = f"{label}: {summary}"
        if event.phase in {
            ProgressPhase.COMPLETED,
            ProgressPhase.FAILED,
            ProgressPhase.CANCELED,
            ProgressPhase.BLOCKED,
        }:
            print("\r" + line + " " * max(0, self._last_line_length - len(line)))
            self._last_line_length = 0
        else:
            padding = " " * max(0, self._last_line_length - len(line))
            print("\r" + line + padding, end="", flush=True)
            self._last_line_length = len(line)


def _exit_code(result: BatchResult) -> int:
    if any(item.state == JobState.CANCELED for item in result.items):
        return EXIT_CANCELED
    return EXIT_OK if result.failed_count == 0 else EXIT_FAILED


def _run_tui(settings_only: bool = False) -> int:
    try:
        from .ui import DownloaderApp
    except ImportError as exc:
        print(f"The interactive UI is unavailable: {exc}", file=sys.stderr)
        return EXIT_DEPENDENCY
    app = DownloaderApp(settings_only=settings_only)
    app.run()
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
