"""Launch a frozen DLP binary in a terminal-like session and verify startup."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--startup-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if not args.binary.is_file():
        raise SystemExit(f"Frozen binary not found: {args.binary}")

    environment = os.environ.copy()
    environment.setdefault("TERM", "xterm-256color")
    if os.name == "nt":
        return _smoke_windows(args.binary, environment, args.startup_seconds)
    return _smoke_posix(args.binary, environment, args.startup_seconds)


def _smoke_posix(binary: Path, environment: dict[str, str], startup_seconds: float) -> int:
    import pty

    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            [str(binary)],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=environment,
            start_new_session=True,
        )
    finally:
        os.close(slave)

    try:
        time.sleep(startup_seconds)
        if process.poll() is not None:
            output = os.read(master, 64 * 1024).decode("utf-8", "replace")
            raise RuntimeError(
                f"Frozen TUI exited before startup (code {process.returncode}): {output[-2000:]}"
            )
        # Ctrl-Q is DLP's quit binding. The hard kill fallback keeps CI from
        # hanging if a terminal implementation ignores the key sequence.
        os.write(master, b"\x11")
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        return 0
    finally:
        os.close(master)


def _smoke_windows(binary: Path, environment: dict[str, str], startup_seconds: float) -> int:
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        [str(binary)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        creationflags=creation_flags,
    )
    try:
        time.sleep(startup_seconds)
        if process.poll() is not None:
            raise RuntimeError(
                f"Frozen TUI exited before startup (code {process.returncode})"
            )
        process.terminate()
        process.wait(timeout=5)
        return 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"Frozen smoke test failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
