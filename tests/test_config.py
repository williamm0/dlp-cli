from pathlib import Path

import pytest

from dlp.config import ConfigError, SettingsRepository
from dlp.models import Settings


def test_settings_round_trip_uses_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    settings = Settings(
        output_directory=tmp_path / "downloads",
        cookies_file=tmp_path / "cookies.txt",
        extra_args=["--format-sort", "res,fps"],
        subtitles="manual",
        subtitle_languages=["en", "no"],
    )

    repository = SettingsRepository(path)
    repository.save(settings)
    loaded = repository.load()

    assert loaded.output_directory == tmp_path / "downloads"
    assert loaded.cookies_file == tmp_path / "cookies.txt"
    assert loaded.extra_args == ["--format-sort", "res,fps"]
    assert loaded.subtitle_languages == ["en", "no"]
    assert path.read_text(encoding="utf-8").startswith("quality_mode")


def test_missing_config_returns_platform_default(tmp_path: Path) -> None:
    settings = SettingsRepository(tmp_path / "missing.toml").load()
    assert settings.output_directory.name == "dlp"


def test_invalid_config_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("quality_mode = \"unsupported\"\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="quality_mode"):
        SettingsRepository(path).load()


def test_invalid_config_can_be_recovered_without_data_loss(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("quality_mode = \"unsupported\"\n", encoding="utf-8")

    settings = SettingsRepository(path).load_or_default()

    assert settings.quality_mode == "best"
    assert not path.exists()
    assert list(tmp_path.glob("config.toml.invalid-*"))
