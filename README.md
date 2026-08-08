# DLP

[![CI](https://github.com/williamm0/dlp-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/williamm0/dlp-cli/actions/workflows/ci.yml)

DLP is a terminal UI and command-line wrapper around yt-dlp. It keeps download output readable, saves named settings profiles, records redacted history, and handles URL batches one item at a time.

## Install

### macOS arm64 package

Download `dlp-macos-arm64.pkg` from the [latest release](https://github.com/williamm0/dlp-cli/releases), open it, then start a new terminal:

```sh
dlp --version
dlp doctor
```

The unsigned package installs the bundled application under `/usr/local/libexec/dlp` and a `dlp` symlink under `/usr/local/bin`. macOS may require opening the package from Finder or approving it in Privacy & Security.

### Windows x64 installer

Run `dlp-windows-x64-setup.exe` from the [latest release](https://github.com/williamm0/dlp-cli/releases), reopen your terminal, then run:

```powershell
dlp --version
dlp doctor
```

The installer is per-user, does not require administrator privileges, and adds its directory to the current user's `PATH`. The first release artifacts are unsigned.

### Development install

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

# List available format IDs for a custom selector
dlp formats "https://example.com/video"

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

Running `dlp` opens the Download tab. `dlp settings` opens the same UI on Settings. The TUI accepts one URL per line, ignores blank lines and lines starting with `#`, and runs jobs serially. A failed or blocked item does not stop later items; canceling marks the remaining items canceled.

For automation, `download`, `batch`, `info`, `formats`, `history`, `settings`, `config`, and `doctor` expose machine-readable modes where implemented. `download --json` emits newline-delimited progress events and a final result. `--json` and `--no-prompt` never invoke dependency installation prompts. `--dry-run` validates settings and reports missing dependencies without downloading.

The default format is `bestvideo*+bestaudio/best`. DLP hides yt-dlp's normal output and renders the current phase, percentage, speed, and ETA instead. DLP saves media under `~/Downloads/dlp` on macOS and the equivalent user Downloads folder on Windows. An optional download archive skips URLs that have already completed.

The interactive queue supports serial batches, dependency consent, cancellation, retrying failed or blocked items, queue filtering, metadata preview, named profile selection, and local history. Settings cover output, formats, playlists, subtitles, metadata, cookies-by-reference, network limits, JavaScript runtime selection, external tool paths, and guarded advanced yt-dlp arguments.

`download`, `batch`, `info`, and `doctor` support newline-delimited JSON where it helps automation. `--no-prompt` keeps a missing dependency blocked and returns exit code `3`; failed downloads return `1`, invalid input returns `2`, and cancellation returns `130`.

## Dependencies

DLP bundles Python, Textual, yt-dlp, yt-dlp-ejs, and the runtime needed by the application in packaged builds. Non-YouTube requests do not require the YouTube EJS add-on at preflight time.

`ffmpeg` and `ffprobe` are required for jobs that merge separate streams, audio-only output, subtitles, embedded metadata or thumbnails, non-default merge formats, or custom selectors that combine formats. With the default settings, YouTube jobs also check for yt-dlp-ejs and Deno. Switching the JavaScript runtime to `auto` stops treating Deno as a hard requirement.

DLP asks before it invokes Homebrew on macOS or winget on Windows. If the package manager is unavailable, it prints a manual installation command and keeps the affected job blocked.

## Settings

Settings live at:

```text
macOS:  ~/Library/Application Support/dlp/config.toml
Windows: %APPDATA%\dlp\config.toml
```

The file stores paths and preferences. It does not store cookie contents, proxy credentials, or credentials from advanced arguments. Browser-cookie selections and cookie-file paths remain user-controlled. Named profiles live beside the main file, and history lives in the platform user-data directory as bounded redacted JSONL.

Useful maintenance commands:

```sh
dlp config path
dlp config validate
dlp config reset --yes
dlp settings --show
dlp history --clear --yes
dlp profile show NAME
dlp profile delete NAME --yes
```

History is capped at 500 entries. If the main TOML file is malformed, DLP moves it to `config.toml.invalid-<timestamp>-<pid>` and starts from defaults. Settings and history redact credentials, sensitive headers, query strings, and terminal control characters.

## Test and check

```sh
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src/dlp
```

Network smoke tests are opt-in. See `packaging/README.md` for macOS arm64 and Windows x64 builds, installer layout, upgrades, and uninstall behavior.

To run the network smoke suite against yt-dlp's public test video:

```sh
DLP_LIVE_TESTS=1 .venv/bin/python scripts/live_smoke.py
DLP_LIVE_TESTS=1 DLP_LIVE_CANCEL=1 .venv/bin/python scripts/live_smoke.py
```

The suite is never part of the default test run. It checks metadata, a dependency-only plan, a continue-on-error batch, and optionally cancellation.

The repository is released under the [MIT License](LICENSE). CI tests Ubuntu, macOS, and Windows, launches the frozen TUI on native builders, installs and exercises both installer artifacts, and publishes SHA-256 manifests for tagged releases.
