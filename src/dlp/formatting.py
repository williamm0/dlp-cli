"""Human-readable progress formatting used by CLI and TUI."""

from __future__ import annotations

from typing import Any


def format_bytes(value: int | float | None) -> str:
    if value is None:
        return "-"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(amount) < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return "-"


def format_speed(value: float | None) -> str:
    return f"{format_bytes(value)}/s" if value is not None else "-"


def format_eta(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    seconds = max(0, int(seconds))
    minutes, remaining = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining:02d}"
    return f"{minutes:02d}:{remaining:02d}"


def format_percent(value: float | None) -> str:
    if value is None:
        return "  --.-%"
    return f"{max(0.0, min(100.0, value)):5.1f}%"


def event_summary(event: Any) -> str:
    pieces = [event.message or event.phase.value]
    if event.percent is not None:
        pieces.append(format_percent(event.percent))
    if event.speed_bytes is not None:
        pieces.append(format_speed(event.speed_bytes))
    if event.eta_seconds is not None:
        pieces.append(f"ETA {format_eta(event.eta_seconds)}")
    return " | ".join(pieces)
