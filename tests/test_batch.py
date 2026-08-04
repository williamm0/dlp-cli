from pathlib import Path

import pytest

from dlp.batch import normalize_urls, parse_batch_text, read_batch_file


def test_parse_batch_text_ignores_blank_and_comment_lines() -> None:
    text = "\n# archive\n https://example.com/a \n\nhttps://example.com/b\n"
    assert parse_batch_text(text) == ["https://example.com/a", "https://example.com/b"]


def test_read_batch_file_uses_utf8(tmp_path: Path) -> None:
    path = tmp_path / "urls.txt"
    path.write_text("https://example.com/video\n", encoding="utf-8")
    assert read_batch_file(path) == ["https://example.com/video"]


def test_normalize_urls_requires_a_value() -> None:
    with pytest.raises(ValueError, match="at least one URL"):
        normalize_urls(["", "  "])
