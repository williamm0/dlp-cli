# DLP

[![CI](https://github.com/williamm0/dlp-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/williamm0/dlp-cli/actions/workflows/ci.yml)

DLP is a terminal UI and command-line wrapper around yt-dlp. It keeps download output readable, saves named settings profiles, records redacted history, and handles URL batches one item at a time.

## Install for development

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

On Windows, use `.venv\Scripts\python.exe` and `.venv\Scripts\dlp.exe`.

## Use it

```sh
# Open the interactive downloader
dlp

# Download one or more URLs
dlp download "https://example.com/video"

# Script-friendly progress and a dependency-only dry run
dlp download --json --dry-run "https://example.com/video"

# Inspect metadata without downloading
dlp info --json "https://example.com/video"

# Download one URL per line from a file
dlp batch urls.txt

# Open saved settings
dlp settings

# Check dependencies
dlp doctor
dlp doctor --install

# Inspect config, profiles, and recent jobs
dlp config show
dlp profile save podcasts
dlp profile list
dlp history --limit 20
```

The default format is `bestvideo*+bestaudio/best`. DLP hides yt-dlp's normal output and renders the current phase, percentage, speed, and ETA instead. DLP saves media under `~/Downloads/dlp` on macOS and the equivalent user Downloads folder on Windows.

The interactive queue supports serial batches, dependency consent, cancellation, retrying failed or blocked items, queue filtering, metadata preview, named profile selection, and local history. Settings cover output, formats, playlists, subtitles, metadata, cookies-by-reference, network limits, JavaScript runtime selection, external tool paths, and guarded advanced yt-dlp arguments.

`download`, `batch`, `info`, and `doctor` support newline-delimited JSON where it helps automation. `--no-prompt` keeps a missing dependency blocked and returns exit code `3`; failed downloads return `1`, invalid input returns `2`, and cancellation returns `130`.

## Dependencies

The development environment installs Python, yt-dlp, yt-dlp-ejs, and the Textual UI. Best-quality downloads also need `ffmpeg` and `ffprobe` to merge separate video and audio streams. YouTube jobs may need Deno for yt-dlp's JavaScript runtime.

DLP asks before it invokes Homebrew on macOS or winget on Windows. If the package manager is unavailable, it prints a manual installation command and keeps the affected job blocked.

## Settings

Settings live at:

```text
macOS:  ~/Library/Application Support/dlp/config.toml
Windows: %APPDATA%\dlp\config.toml
```

The file stores paths and preferences. It does not store cookie contents, proxy credentials, or credentials from advanced arguments. Browser-cookie selections and cookie-file paths remain user-controlled. Named profiles live beside the main file, and history lives in the platform user-data directory as bounded redacted JSONL.

## Test and check

```sh
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src/dlp
```

Network smoke tests are opt-in. See `packaging/README.md` for macOS arm64 and Windows x64 builds.

The repository is released under the [MIT License](LICENSE). CI tests Ubuntu, macOS, and Windows, builds native macOS arm64 and Windows x64 artifacts, exercises the frozen UI import path with `dlp doctor --ui-check`, and publishes SHA-256 manifests for tagged releases.
