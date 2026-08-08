"""Dependency discovery and consent-based package-manager installation."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .models import InstallState, Settings


class DependencyName(str, Enum):
    PYTHON = "python"
    YTDLP = "yt-dlp"
    YTDLP_EJS = "yt-dlp-ejs"
    FFMPEG = "ffmpeg"
    FFPROBE = "ffprobe"
    DENO = "deno"
    ARIA2C = "aria2c"


@dataclass(frozen=True)
class DependencyStatus:
    name: DependencyName
    available: bool
    required: bool
    path: str | None = None
    version: str | None = None
    reason: str = ""
    install_command: tuple[str, ...] = ()
    manual_command: str = ""
    bundled: bool = False


@dataclass(frozen=True)
class InstallResult:
    name: DependencyName
    state: InstallState
    message: str
    command: tuple[str, ...] = ()


def is_youtube_url(url: str) -> bool:
    try:
        hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in ("youtube.com", "youtu.be", "youtube-nocookie.com")
    )


def required_dependencies(request_url: str, settings: Settings) -> set[DependencyName]:
    requirements = {
        DependencyName.PYTHON,
        DependencyName.YTDLP,
        DependencyName.YTDLP_EJS,
    }
    if _needs_media_tools(settings):
        requirements.update({DependencyName.FFMPEG, DependencyName.FFPROBE})
    if is_youtube_url(request_url) and settings.js_runtime != "auto":
        requirements.add(DependencyName.DENO)
    if settings.external_downloader == "aria2c":
        requirements.add(DependencyName.ARIA2C)
    return requirements


def _needs_media_tools(settings: Settings) -> bool:
    """Determine whether the selected job can require ffmpeg/ffprobe."""

    return any(
        (
            settings.quality_mode == "best",
            settings.audio_only,
            settings.subtitles != "off",
            settings.merge_output_format != "auto",
            "+" in settings.format_selector,
            settings.embed_metadata,
            settings.embed_thumbnail,
        )
    )


class DependencyManager:
    def __init__(
        self,
        *,
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self._runner = runner

    def check(
        self,
        requirements: Iterable[DependencyName],
        settings: Settings | None = None,
    ) -> list[DependencyStatus]:
        return [self._status(name, settings) for name in requirements]

    def check_for_request(self, url: str, settings: Settings) -> list[DependencyStatus]:
        return self.check(required_dependencies(url, settings), settings)

    def install(self, name: DependencyName, consent: bool) -> InstallResult:
        command = self._install_command(name)
        if not consent:
            return InstallResult(
                name,
                InstallState.DECLINED,
                f"Installation declined for {name.value}",
                command,
            )
        if not command:
            return InstallResult(
                name,
                InstallState.UNAVAILABLE,
                f"No supported package manager was found for {name.value}",
            )

        completed = self._runner(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "installation failed").strip()
            return InstallResult(name, InstallState.FAILED, detail, command)
        return InstallResult(name, InstallState.INSTALLED, f"Installed {name.value}", command)

    def _status(self, name: DependencyName, settings: Settings | None) -> DependencyStatus:
        path: str | None = None
        version: str | None = None
        bundled = False
        reason = ""

        if name == DependencyName.PYTHON:
            available = sys.version_info >= (3, 10)
            path = sys.executable
            version = platform.python_version()
            bundled = getattr(sys, "frozen", False)
            reason = "Python 3.10 or newer is required"
        elif name == DependencyName.YTDLP:
            spec = importlib.util.find_spec("yt_dlp")
            available = spec is not None
            if spec is not None:
                path = spec.origin
                version = _package_version("yt-dlp")
            bundled = getattr(sys, "frozen", False) and available
            reason = "yt-dlp provides the download engine"
        elif name == DependencyName.YTDLP_EJS:
            version = _package_version("yt-dlp-ejs")
            available = version is not None or importlib.util.find_spec("yt_dlp_ejs") is not None
            bundled = getattr(sys, "frozen", False) and available
            reason = "yt-dlp-ejs enables current YouTube challenge solving"
        else:
            configured = _configured_path(name, settings)
            if configured:
                path = str(configured)
                available = configured.is_file()
            else:
                path = shutil.which(name.value)
                available = path is not None
            reason = _external_reason(name)
            version = _executable_version(path) if path else None

        install_command, manual_command = self._commands_for(name)
        return DependencyStatus(
            name=name,
            available=available,
            required=True,
            path=path,
            version=version,
            reason=reason,
            install_command=install_command,
            manual_command=manual_command,
            bundled=bundled,
        )

    def _install_command(self, name: DependencyName) -> tuple[str, ...]:
        commands, _ = self._commands_for(name)
        return commands

    def _commands_for(self, name: DependencyName) -> tuple[tuple[str, ...], str]:
        system = platform.system()
        if system == "Darwin" and shutil.which("brew"):
            package = {
                DependencyName.FFMPEG: "ffmpeg",
                DependencyName.FFPROBE: "ffmpeg",
                DependencyName.DENO: "deno",
                DependencyName.ARIA2C: "aria2",
            }.get(name)
            if package:
                command: tuple[str, ...] = ("brew", "install", package)
                return command, "brew install " + package
            if name in {DependencyName.YTDLP, DependencyName.YTDLP_EJS}:
                command = (
                    *_pip_executable(),
                    "-m",
                    "pip",
                    "install",
                    "--user",
                    "yt-dlp[default]",
                    "yt-dlp-ejs",
                )
                return command, "python3 -m pip install --user 'yt-dlp[default]' yt-dlp-ejs"
        if system == "Windows" and shutil.which("winget"):
            package = {
                DependencyName.FFMPEG: "Gyan.FFmpeg.Shared",
                DependencyName.FFPROBE: "Gyan.FFmpeg.Shared",
                DependencyName.DENO: "DenoLand.Deno",
                DependencyName.ARIA2C: "aria2.aria2",
            }.get(name)
            if package:
                command = ("winget", "install", "--id", package, "--exact", "--silent")
                return command, f"winget install --id {package} --exact"
            if name in {DependencyName.YTDLP, DependencyName.YTDLP_EJS}:
                command = (
                    *_pip_executable(),
                    "-m",
                    "pip",
                    "install",
                    "--user",
                    "yt-dlp[default]",
                    "yt-dlp-ejs",
                )
                return command, "py -m pip install --user yt-dlp[default] yt-dlp-ejs"

        if name in {DependencyName.YTDLP, DependencyName.YTDLP_EJS}:
            return (), "python3 -m pip install --user 'yt-dlp[default]' yt-dlp-ejs"
        if name == DependencyName.FFMPEG or name == DependencyName.FFPROBE:
            return (), "Install ffmpeg from https://ffmpeg.org/download.html"
        if name == DependencyName.DENO:
            return (), "Install Deno from https://deno.com/runtime"
        if name == DependencyName.ARIA2C:
            return (), "Install aria2 from https://aria2.github.io/"
        return (), ""


def _configured_path(name: DependencyName, settings: Settings | None) -> Path | None:
    if settings is None:
        return None
    if name == DependencyName.FFMPEG:
        return settings.ffmpeg_path
    if name == DependencyName.FFPROBE:
        return settings.ffprobe_path
    return None


def _pip_executable() -> tuple[str, ...]:
    """Use a real system Python when the frozen app cannot run ``-m pip``."""

    if getattr(sys, "frozen", False):
        return ("py",) if platform.system() == "Windows" else ("python3",)
    return (sys.executable,)


def _package_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _executable_version(path: str | None) -> str | None:
    if not path:
        return None
    try:
        result = subprocess.run((path, "--version"), capture_output=True, text=True, check=False)
    except OSError:
        return None
    line = (result.stdout or result.stderr).splitlines()
    return line[0].strip() if line else None


def _external_reason(name: DependencyName) -> str:
    return {
        DependencyName.FFMPEG: "Required to merge separate video and audio streams",
        DependencyName.FFPROBE: "Required for media inspection and post-processing",
        DependencyName.DENO: "Required by yt-dlp for full YouTube support",
        DependencyName.ARIA2C: "Optional external downloader",
    }.get(name, "External tool")
