# Contributing

## Development setup

Create a virtual environment and install the project with its development tools:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

On Windows, use `.venv\Scripts\python.exe` and `.venv\Scripts\pip.exe`.

## Checks

Run the same checks used by CI before opening a pull request:

```sh
python -m pytest -q
python -m ruff check .
python -m mypy src/dlp --show-error-codes
```

Network downloads are opt-in. Unit and TUI tests use fakes so a test run does not contact a media site.

## Packaging

Build each installer on its native platform. See [packaging/README.md](packaging/README.md) for the macOS arm64 and Windows x64 commands.

Keep installer changes paired with a smoke check for `dlp --version`, `dlp --help`, and `dlp doctor`.
