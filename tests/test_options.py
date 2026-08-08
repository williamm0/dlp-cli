from pathlib import Path

import pytest

from dlp.models import Settings
from dlp.options import (
    OptionValidationError,
    compile_ydl_options,
    split_extra_args,
    validate_filename_template,
)


def test_compile_options_preserves_clean_progress_boundary(tmp_path: Path) -> None:
    settings = Settings(output_directory=tmp_path, extra_args=["--format-sort", "res,fps"])
    marker = object()
    options = compile_ydl_options(settings, marker, marker)

    assert options["format_sort"] == ["res", "fps"]
    assert options["outtmpl"]["default"].startswith(str(tmp_path))
    assert options["quiet"] is True
    assert options["progress_hooks"] == [marker]
    assert options["logger"] is marker


@pytest.mark.parametrize("args", [["-o", "other.mp4"], ["--exec", "echo hi"], ["--quiet"]])
def test_compile_options_rejects_app_owned_flags(args: list[str]) -> None:
    with pytest.raises(OptionValidationError, match="controlled by dlp"):
        compile_ydl_options(Settings(extra_args=args), lambda _: None, object())


def test_compile_options_rejects_positional_extra_values() -> None:
    with pytest.raises(OptionValidationError, match="cannot contain URLs"):
        compile_ydl_options(Settings(extra_args=["https://example.com"]), lambda _: None, object())


def test_split_extra_args_reports_unbalanced_quotes() -> None:
    with pytest.raises(OptionValidationError, match="invalid extra arguments"):
        split_extra_args('--format "best')


def test_filename_template_cannot_escape_output_directory() -> None:
    with pytest.raises(OptionValidationError, match="path separators"):
        validate_filename_template("../outside/%(title)s.mp4")


def test_compile_options_selects_configured_javascript_runtime() -> None:
    options = compile_ydl_options(Settings(js_runtime="deno"), lambda _: None, object())

    assert options["js_runtimes"] == {"deno": {"path": None}}


def test_compile_options_supports_archive_metadata_and_fragment_controls(tmp_path: Path) -> None:
    settings = Settings(
        output_directory=tmp_path,
        download_archive=tmp_path / "archive.txt",
        write_info_json=True,
        write_description=True,
        write_comments=True,
        fragment_retries=7,
        concurrent_fragments=4,
    )
    options = compile_ydl_options(settings, lambda _: None, object())

    assert options["download_archive"] == str(tmp_path / "archive.txt")
    assert options["writeinfojson"] is True
    assert options["writedescription"] is True
    assert options["getcomments"] is True
    assert options["fragment_retries"] == 7
    assert options["concurrent_fragment_downloads"] == 4


@pytest.mark.parametrize(
    "args",
    [["--format", "best"], ["--cookies", "cookies.txt"], ["--download-archive", "a.txt"]],
)
def test_compile_options_rejects_common_app_owned_flags(args: list[str]) -> None:
    with pytest.raises(OptionValidationError, match="controlled by dlp"):
        compile_ydl_options(Settings(extra_args=args), lambda _: None, object())


def test_settings_do_not_persist_proxy_credentials_or_sensitive_headers() -> None:
    with pytest.raises(ValueError, match="proxy credentials"):
        Settings(proxy="https://user:password@example.com:8080").to_mapping()
    with pytest.raises(ValueError, match="credentials"):
        Settings(extra_args=["--add-header", "Authorization: Bearer secret"]).to_mapping()
