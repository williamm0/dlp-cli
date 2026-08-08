"""Opt-in network smoke tests for the public yt-dlp test video."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_URL = "https://www.youtube.com/watch?v=BaW_jenozKc"


def main() -> int:
    if os.environ.get("DLP_LIVE_TESTS") != "1":
        print("Live smoke tests are opt-in. Set DLP_LIVE_TESTS=1 to run them.")
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "src")

    with tempfile.TemporaryDirectory(prefix="dlp-live-") as temporary:
        output = Path(temporary)
        _run(
            root,
            ["info", "--json", args.url],
            environment,
            allowed_codes={0},
        )
        _run(
            root,
            ["download", "--dry-run", "--json", "--output", str(output), args.url],
            environment,
            allowed_codes={0, 3},
        )

        batch_file = output / "urls.txt"
        batch_file.write_text(
            f"{args.url}\nhttps://example.invalid/dlp-live-invalid\n",
            encoding="utf-8",
        )
        _run(
            root,
            ["batch", "--json", "--no-prompt", "--output", str(output), str(batch_file)],
            environment,
            allowed_codes={0, 1, 3},
        )

        if os.environ.get("DLP_LIVE_CANCEL") == "1":
            _run_cancellation(root, args.url, output, environment)
    return 0


def _run(
    root: Path,
    arguments: list[str],
    environment: dict[str, str],
    *,
    allowed_codes: set[int],
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "dlp", *arguments]
    result = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True)
    if result.returncode not in allowed_codes:
        raise SystemExit(
            f"Live smoke command failed ({result.returncode}): {' '.join(arguments)}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    if "--json" in arguments:
        for line in result.stdout.splitlines():
            json.loads(line)
    print(f"PASS {arguments[0]} ({result.returncode})")
    return result


def _run_cancellation(
    root: Path,
    url: str,
    output: Path,
    environment: dict[str, str],
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "dlp", "download", "--no-prompt", "--output", str(output), url],
        cwd=root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(2)
    process.send_signal(signal.SIGINT)
    try:
        exit_code = process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        raise SystemExit("Live cancellation smoke test timed out") from None
    if exit_code != 130:
        stdout, stderr = process.communicate()
        raise SystemExit(
            f"Live cancellation returned {exit_code}\n{stdout}\n{stderr}"
        )
    print("PASS cancellation (130)")


if __name__ == "__main__":
    raise SystemExit(main())
