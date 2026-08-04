from dlp.diagnostics import sanitize_message, sanitize_url


def test_sanitize_url_removes_query_and_fragment() -> None:
    assert sanitize_url("https://example.com/video?token=secret#chapter") == "https://example.com/video"
    assert sanitize_url("https://user:password@example.com/video") == "https://example.com/video"


def test_sanitize_message_redacts_sensitive_assignments() -> None:
    message = "authorization=secret https://example.com/video?token=secret"
    sanitized = sanitize_message(message)
    assert "secret" not in sanitized
    assert "[redacted]" in sanitized
