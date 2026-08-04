"""Per-user TOML settings persistence."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import tomli_w
from platformdirs import PlatformDirs

from .models import Settings


class ConfigError(ValueError):
    """Raised when the settings file cannot be read or validated."""


def config_path() -> Path:
    """Return the platform-native settings file path."""

    return Path(PlatformDirs("dlp", appauthor=False).user_config_path) / "config.toml"


def default_output_directory() -> Path:
    """Return a predictable user-facing download directory."""

    return Path.home() / "Downloads" / "dlp"


class SettingsRepository:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_path()

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings(output_directory=default_output_directory())

        try:
            raw = self._read()
            return Settings.from_mapping(raw)
        except (OSError, TypeError, ValueError) as exc:
            raise ConfigError(f"Could not read settings from {self.path}: {exc}") from exc

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = settings.to_mapping()
        fd, temp_name = tempfile.mkstemp(prefix="config.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(tomli_w.dumps(payload))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
        finally:
            temp_path = Path(temp_name)
            if temp_path.exists():
                temp_path.unlink()

    def _read(self) -> dict[str, Any]:
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
            import tomli as tomllib

        with self.path.open("rb") as handle:
            raw = tomllib.load(handle)
        if not isinstance(raw, dict):
            raise TypeError("root value must be a table")
        return raw
