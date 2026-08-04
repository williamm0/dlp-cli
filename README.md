# DLP

[![CI](https://github.com/williamm0/dlp-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/williamm0/dlp-cli/actions/workflows/ci.yml)

DLP is a terminal UI and command-line wrapper around yt-dlp. It keeps download output readable, saves settings, and handles URL batches one item at a time.

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

# Download one URL per line from a file
dlp batch urls.txt

# Open saved settings
dlp settings

# Check dependencies
dlp doctor
dlp doctor --install
```

The default format is `bestvideo*+bestaudio/best`. DLP hides yt-dlp's normal output and renders the current phase, percentage, speed, and ETA instead. DLP saves media under `~/Downloads/dlp` on macOS and the equivalent user Downloads folder on Windows.

The interactive queue supports serial batches, dependency consent, cancellation, and retrying failed or blocked items. Settings cover output, formats, playlists, subtitles, metadata, cookies-by-reference, network limits, JavaScript runtime selection, external tool paths, and guarded advanced yt-dlp arguments.

## Dependencies

The development environment installs Python, yt-dlp, yt-dlp-ejs, and the Textual UI. Best-quality downloads also need `ffmpeg` and `ffprobe` to merge separate video and audio streams. YouTube jobs may need Deno for yt-dlp's JavaScript runtime.

DLP asks before it invokes Homebrew on macOS or winget on Windows. If the package manager is unavailable, it prints a manual installation command and keeps the affected job blocked.

## Settings

Settings live at:

```text
macOS:  ~/Library/Application Support/dlp/config.toml
Windows: %APPDATA%\dlp\config.toml
```

The file stores paths and preferences. It does not store cookie contents or credentials. Browser-cookie selections and cookie-file paths remain user-controlled.

## Test and check

```sh
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src/dlp
```

Network smoke tests are opt-in. See `packaging/README.md` for macOS arm64 and Windows x64 builds.

The repository is released under the [MIT License](LICENSE). CI tests Ubuntu, macOS, and Windows, then builds the Windows x64 package on a native Windows runner.
