"""Command-line entrypoint for the DLP downloader."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .batch import normalize_urls, read_batch_file
from .config import ConfigError, SettingsRepository, config_path, default_output_directory
from .dependencies import DependencyManager, DependencyStatus
from .diagnostics import sanitize_message, sanitize_url
from .engine import DiagnosticLogger, DownloadEngine
from .formatting import event_summary
from .history import HistoryRepository, ProfileRepository
from .models import BatchResult, DownloadRequest, JobState, ProgressEvent, ProgressPhase, Settings
from .options import OptionValidationError, compile_ydl_options, split_extra_args
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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    download = subparsers.add_parser("download", help="download one or more URLs")
    _add_settings_arguments(download)
    download.add_argument("urls", nargs="+", help="URL supported by yt-dlp")

    batch = subparsers.add_parser("batch", help="download URLs from a text file")
    _add_settings_arguments(batch)
    batch.add_argument("file", type=Path)

    info = subparsers.add_parser("info", help="inspect URL metadata without downloading")
    _add_settings_arguments(info, include_download_controls=False)
    info.add_argument(
        "--flat-playlist", action="store_true", help="inspect playlist entries flatly"
    )
    info.add_argument("url", help="URL supported by yt-dlp")

    settings = subparsers.add_parser("settings", help="open or inspect saved settings")
    settings.add_argument(
        "--show", action="store_true", help="print settings instead of opening the TUI"
    )
    settings.add_argument("--json", action="store_true", help="print machine-readable settings")
    settings.add_argument("--path", action="store_true", help="print the config path")
    settings.add_argument("--reset", action="store_true", help="restore default settings")
    settings.add_argument("--yes", action="store_true", help="confirm a reset")

    config = subparsers.add_parser("config", help="inspect or validate configuration")
    config_subparsers = config.add_subparsers(dest="config_command")
    config_subparsers.add_parser("path", help="print the config path")
    config_show = config_subparsers.add_parser("show", help="print saved settings")
    config_show.add_argument("--json", action="store_true")
    config_validate = config_subparsers.add_parser("validate", help="validate saved settings")
    config_validate.add_argument("--json", action="store_true")
    config_reset = config_subparsers.add_parser("reset", help="restore default settings")
    config_reset.add_argument("--yes", action="store_true", help="confirm a reset")

    profile = subparsers.add_parser("profile", help="manage named settings profiles")
    profile_subparsers = profile.add_subparsers(dest="profile_command")
    profile_subparsers.add_parser("list", help="list saved profiles")
    profile_show = profile_subparsers.add_parser("show", help="show a profile")
    profile_show.add_argument("name")
    profile_show.add_argument("--json", action="store_true")
    profile_save = profile_subparsers.add_parser("save", help="save current settings as a profile")
    profile_save.add_argument("name")
    profile_delete = profile_subparsers.add_parser("delete", help="delete a profile")
    profile_delete.add_argument("name")
    profile_delete.add_argument("--yes", action="store_true", help="confirm deletion")

    history = subparsers.add_parser("history", help="list or clear recent jobs")
    history.add_argument("--limit", type=_positive_or_zero, default=25)
    history.add_argument("--json", action="store_true")
    history.add_argument("--clear", action="store_true", help="clear stored history")
    history.add_argument("--yes", action="store_true", help="confirm clearing history")

    doctor = subparsers.add_parser("doctor", help="check download dependencies")
    doctor.add_argument(
        "--install", action="store_true", help="ask before installing missing tools"
    )
    doctor.add_argument("--json", action="store_true", help="print machine-readable results")
    doctor.add_argument("--profile", help="check dependencies for a named profile")
    doctor.add_argument(
        "--ui-check",
        action="store_true",
        help="verify that the interactive UI imports, without starting it",
    )
    return parser


def _add_settings_arguments(
    parser: argparse.ArgumentParser, *, include_download_controls: bool = True
) -> None:
    parser.add_argument("--profile", help="load a named settings profile")
    parser.add_argument("--output", "-o", dest="output_directory", type=Path)
    parser.add_argument("--format", dest="format_selector", help="yt-dlp format selector")
    if include_download_controls:
        parser.add_argument("--audio-only", action="store_true")
        parser.add_argument("--audio-format", default=None)
        parser.add_argument("--audio-quality", default=None)
        parser.add_argument("--subtitles", choices=("off", "manual", "auto"))
        parser.add_argument("--subtitle-langs", default=None, help="comma-separated language codes")
        parser.add_argument("--playlist", choices=("all", "single"))
        parser.add_argument("--overwrite", choices=("skip", "overwrite"))
        parser.add_argument("--retries", type=_nonnegative_int)
        parser.add_argument("--rate-limit")
        parser.add_argument("--socket-timeout", type=_positive_int)
        parser.add_argument("--proxy")
        parser.add_argument("--cookies-from-browser")
        parser.add_argument("--cookie-file", type=Path)
        parser.add_argument("--ffmpeg-path", type=Path)
        parser.add_argument("--ffprobe-path", type=Path)
        parser.add_argument("--js-runtime", choices=("auto", "deno"))
        parser.add_argument("--extra-arg", action="append", default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--no-prompt", action="store_true")
    parser.add_argument("--json", action="store_true", help="print newline-delimited JSON")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            return _run_tui()
        if args.command == "download":
            return _run_urls(args)
        if args.command == "batch":
            return _run_batch(args)
        if args.command == "info":
            return _run_info(args)
        if args.command == "settings":
            return _run_settings(args)
        if args.command == "config":
            return _run_config(args)
        if args.command == "profile":
            return _run_profile(args)
        if args.command == "history":
            return _run_history(args)
        if args.command == "doctor":
            return _run_doctor(args)
    except (ConfigError, OSError, OptionValidationError, ValueError, RuntimeError) as exc:
        print(f"Error: {sanitize_message(str(exc))}", file=sys.stderr)
        return EXIT_INVALID
    return EXIT_INVALID


def _run_urls(args: argparse.Namespace) -> int:
    return _run_requests(normalize_urls(args.urls), args)


def _run_batch(args: argparse.Namespace) -> int:
    urls = read_batch_file(args.file)
    if not urls:
        raise ValueError("batch file contains no URLs")
    return _run_requests(urls, args)


def _run_requests(urls: list[str], args: argparse.Namespace) -> int:
    settings, profile_name = _settings_for_args(args)
    requests = [
        DownloadRequest(job_id=uuid.uuid4().hex[:8], url=url, settings=settings.clone())
        for url in urls
    ]
    if getattr(args, "dry_run", False):
        return _run_dry_run(requests, json_mode=getattr(args, "json", False))

    renderer = ConsoleRenderer(json_mode=getattr(args, "json", False))
    cancel_event = threading.Event()
    history = HistoryRepository()
    runner = QueueRunner(
        result_sink=lambda request, result: history.record(
            request, result, profile=profile_name
        )
    )
    try:
        result = runner.run(
            requests,
            renderer.emit,
            cancel_event=cancel_event,
            dependency_prompt=(
                None
                if getattr(args, "no_prompt", False) or getattr(args, "json", False)
                else _prompt_for_dependencies
            ),
        )
    except KeyboardInterrupt:
        cancel_event.set()
        print("\nCanceled", file=sys.stderr)
        return EXIT_CANCELED
    if getattr(args, "json", False):
        print(json.dumps({"type": "batch_result", **_batch_mapping(result)}, sort_keys=True))
    else:
        print(_human_batch_summary(result))
    return _exit_code(result)


def _run_info(args: argparse.Namespace) -> int:
    settings, _profile_name = _settings_for_args(args)
    info = DownloadEngine().extract_info(
        args.url,
        settings,
        flat_playlist=args.flat_playlist,
    )
    if args.json:
        print(json.dumps(info.to_mapping(), sort_keys=True))
    else:
        print(f"Title: {info.title or '-'}")
        print(f"Uploader: {info.uploader or info.channel or '-'}")
        print(f"Duration: {_format_duration(info.duration_seconds)}")
        print(f"Extractor: {info.extractor or '-'}")
        print(f"URL: {info.url}")
        if info.is_playlist:
            print(f"Playlist items: {info.item_count if info.item_count is not None else '-'}")
    return EXIT_OK


def _run_dry_run(requests: list[DownloadRequest], *, json_mode: bool) -> int:
    manager = DependencyManager()
    plans: list[dict[str, Any]] = []
    has_missing = False
    for request in requests:
        statuses = manager.check_for_request(request.url, request.settings)
        missing = [status.name.value for status in statuses if not status.available]
        has_missing = has_missing or bool(missing)
        logger = DiagnosticLogger()
        compile_ydl_options(request.settings, lambda _data: None, logger)
        plans.append(
            {
                "job_id": request.job_id,
                "url": sanitize_url(request.url),
                "format": request.settings.format_selector,
                "output_directory": str(request.settings.output_directory),
                "missing_dependencies": missing,
            }
        )
    if json_mode:
        for plan in plans:
            print(json.dumps({"type": "dry_run", **plan}, sort_keys=True))
    else:
        for plan in plans:
            missing_text = ", ".join(plan["missing_dependencies"]) or "none"
            print(
                f"{plan['url']}: format {plan['format']}; "
                f"output {plan['output_directory']}; missing dependencies: {missing_text}"
            )
    return EXIT_DEPENDENCY if has_missing else EXIT_OK


def _settings_for_args(args: argparse.Namespace) -> tuple[Settings, str | None]:
    profile_name = getattr(args, "profile", None)
    if profile_name:
        settings = ProfileRepository().load(profile_name)
    else:
        settings = SettingsRepository().load_or_default()
    settings = settings.clone()
    overrides: dict[str, Any] = {}
    for arg_name, setting_name in (
        ("output_directory", "output_directory"),
        ("format_selector", "format_selector"),
        ("audio_format", "audio_format"),
        ("audio_quality", "audio_quality"),
        ("subtitles", "subtitles"),
        ("playlist", "playlist_mode"),
        ("overwrite", "overwrite"),
        ("retries", "retries"),
        ("rate_limit", "rate_limit"),
        ("socket_timeout", "socket_timeout"),
        ("proxy", "proxy"),
        ("cookies_from_browser", "cookies_from_browser"),
        ("cookie_file", "cookies_file"),
        ("ffmpeg_path", "ffmpeg_path"),
        ("ffprobe_path", "ffprobe_path"),
        ("js_runtime", "js_runtime"),
    ):
        value = getattr(args, arg_name, None)
        if value is not None:
            overrides[setting_name] = value
    if getattr(args, "format_selector", None) is not None:
        overrides["quality_mode"] = "custom"
    if getattr(args, "audio_only", False):
        overrides["audio_only"] = True
    if getattr(args, "subtitle_langs", None) is not None:
        overrides["subtitle_languages"] = [
            item.strip() for item in args.subtitle_langs.split(",") if item.strip()
        ]
    if getattr(args, "extra_arg", None) is not None:
        overrides["extra_args"] = [
            token for value in args.extra_arg for token in split_extra_args(value)
        ]
    if overrides:
        raw = settings.to_mapping()
        raw.update(
            {
                key: str(value) if isinstance(value, Path) else value
                for key, value in overrides.items()
            }
        )
        settings = Settings.from_mapping(raw)
    return settings, profile_name


def _run_settings(args: argparse.Namespace) -> int:
    if args.path:
        if args.json:
            print(json.dumps({"path": str(config_path())}, sort_keys=True))
        else:
            print(config_path())
        return EXIT_OK
    repository = SettingsRepository()
    if args.reset:
        if not args.yes:
            raise ValueError("reset requires --yes")
        repository.save(Settings(output_directory=default_output_directory()))
        if args.json:
            print(json.dumps({"reset": True, "path": str(repository.path)}, sort_keys=True))
        else:
            print(f"Restored defaults at {repository.path}")
        return EXIT_OK
    if args.show:
        return _print_settings(repository.load_or_default(), args.json)
    return _run_tui(settings_only=True)


def _run_config(args: argparse.Namespace) -> int:
    command = args.config_command or "show"
    repository = SettingsRepository()
    if command == "path":
        print(repository.path)
        return EXIT_OK
    if command == "validate":
        repository.load()
        if args.json:
            print(json.dumps({"valid": True, "path": str(repository.path)}))
        else:
            print(f"Valid settings: {repository.path}")
        return EXIT_OK
    if command == "show":
        return _print_settings(repository.load(), args.json)
    if command == "reset":
        if not args.yes:
            raise ValueError("config reset requires --yes")
        repository.save(Settings(output_directory=default_output_directory()))
        print(f"Restored defaults at {repository.path}")
        return EXIT_OK
    return EXIT_INVALID


def _run_profile(args: argparse.Namespace) -> int:
    repository = ProfileRepository()
    command = args.profile_command or "list"
    if command == "list":
        names = repository.names()
        if args.profile_command is None or not names:
            print("\n".join(names) or "No saved profiles")
        else:
            print("\n".join(names))
        return EXIT_OK
    if command == "show":
        return _print_settings(repository.load(args.name), args.json)
    if command == "save":
        repository.save(args.name, SettingsRepository().load_or_default())
        print(f"Saved profile '{args.name}'")
        return EXIT_OK
    if command == "delete":
        if not args.yes:
            raise ValueError("profile delete requires --yes")
        repository.delete(args.name)
        print(f"Deleted profile '{args.name}'")
        return EXIT_OK
    return EXIT_INVALID


def _run_history(args: argparse.Namespace) -> int:
    repository = HistoryRepository()
    if args.clear:
        if not args.yes:
            raise ValueError("history --clear requires --yes")
        repository.clear()
        if args.json:
            print(json.dumps({"cleared": True}, sort_keys=True))
        else:
            print("History cleared")
        return EXIT_OK
    entries = repository.load(limit=args.limit)
    if args.json:
        for entry in entries:
            print(json.dumps(entry.to_mapping(), sort_keys=True))
    else:
        if not entries:
            print("No download history")
        for entry in entries:
            title = entry.title or entry.url
            detail = entry.error or entry.output_path or "-"
            print(f"{entry.timestamp}  {entry.state.value:11}  {title[:50]}  {detail[:50]}")
    return EXIT_OK


def _run_doctor(args: argparse.Namespace) -> int:
    if args.ui_check:
        from .ui import DownloaderApp

        _ = DownloaderApp
        if args.json:
            print(json.dumps({"ready": True, "type": "ui_check"}, sort_keys=True))
        else:
            print("Interactive UI import: ready")
        return EXIT_OK
    if args.profile:
        settings = ProfileRepository().load(args.profile)
    else:
        settings = SettingsRepository().load_or_default()
    manager = DependencyManager()
    requirements = {
        status.name
        for status in manager.check_for_request("https://www.youtube.com/", settings)
    }
    statuses = sorted(
        manager.check(requirements, settings), key=lambda item: item.name.value
    )
    if args.json:
        print(
            json.dumps(
                {
                    "dependencies": [_dependency_mapping(status) for status in statuses],
                    "install_requested": bool(args.install),
                    "install_performed": False,
                },
                sort_keys=True,
            )
        )
    else:
        for status in sorted(statuses, key=lambda item: item.name.value):
            state = "ready" if status.available else "missing"
            version = f" ({status.version})" if status.version else ""
            print(f"{status.name.value:12} {state:7}{version}  {status.reason}")
            if not status.available and args.install and _prompt_for_status(status):
                result = manager.install(status.name, consent=True)
                print(result.message)
    if args.install:
        statuses = sorted(
            manager.check(requirements, settings), key=lambda item: item.name.value
        )
    return EXIT_OK if all(status.available for status in statuses) else EXIT_DEPENDENCY


class ConsoleRenderer:
    """Render compact progress without exposing yt-dlp's command stream."""

    def __init__(self, *, json_mode: bool = False) -> None:
        self._last_line_length = 0
        self.json_mode = json_mode

    def emit(self, event: ProgressEvent) -> None:
        if self.json_mode:
            print(json.dumps(_event_mapping(event), sort_keys=True), flush=True)
            return
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
    if any(item.state == JobState.BLOCKED for item in result.items):
        return EXIT_DEPENDENCY
    return EXIT_OK if result.failed_count == 0 else EXIT_FAILED


def _prompt_for_dependencies(request: DownloadRequest, statuses: list[DependencyStatus]) -> bool:
    names = ", ".join(status.name.value for status in statuses)
    print(f"Missing for {sanitize_url(request.url)}: {names}")
    for status in statuses:
        command = (
            " ".join(status.install_command)
            if status.install_command
            else status.manual_command
        )
        print(f"  {status.name.value}: {status.reason}. Install with: {command}")
    return all(_prompt_for_status(status) for status in statuses)


def _prompt_for_status(status: DependencyStatus) -> bool:
    try:
        answer = input(f"Install {status.name.value}? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in {"y", "yes"}


def _print_settings(settings: Settings, json_mode: bool) -> int:
    payload = settings.to_mapping()
    if json_mode:
        print(json.dumps(payload, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key} = {value}")
    return EXIT_OK


def _event_mapping(event: ProgressEvent) -> dict[str, Any]:
    return {
        "type": "progress",
        "job_id": event.job_id,
        "phase": event.phase.value,
        "state": event.state.value if event.state else None,
        "percent": event.percent,
        "downloaded_bytes": event.downloaded_bytes,
        "total_bytes": event.total_bytes,
        "speed_bytes": event.speed_bytes,
        "eta_seconds": event.eta_seconds,
        "title": sanitize_message(event.title or "") or None,
        "filename": sanitize_message(event.filename or "") or None,
        "message": sanitize_message(event.message),
    }


def _batch_mapping(result: BatchResult) -> dict[str, Any]:
    return {
        "succeeded": result.succeeded,
        "failed_count": result.failed_count,
        "items": [
            {
                "job_id": item.job_id,
                "state": item.state.value,
                "output_path": item.output_path,
                "error": sanitize_message(item.error or "") or None,
            }
            for item in result.items
        ],
    }


def _dependency_mapping(status: DependencyStatus) -> dict[str, Any]:
    return {
        "name": status.name.value,
        "available": status.available,
        "required": status.required,
        "path": status.path,
        "version": status.version,
        "reason": status.reason,
        "bundled": status.bundled,
        "manual_command": status.manual_command,
    }


def _human_batch_summary(result: BatchResult) -> str:
    if any(item.state == JobState.CANCELED for item in result.items):
        return f"Canceled after {len(result.items)} job(s)."
    if result.failed_count:
        return f"Finished with {result.failed_count} failed or blocked job(s)."
    return f"Completed {len(result.items)} job(s)."


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    minutes, remaining = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return (
        f"{hours:02d}:{minutes:02d}:{remaining:02d}"
        if hours
        else f"{minutes:02d}:{remaining:02d}"
    )


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _positive_or_zero(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


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
