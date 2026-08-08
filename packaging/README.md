# Packaging

The first release produces unsigned artifacts. Build each platform on its native host so PyInstaller uses the correct interpreter and native libraries.

## macOS arm64

```sh
./packaging/macos/build.sh
```

The script creates `dist/dlp-macos-arm64.pkg` and installs the `dlp` command under `/usr/local/bin`.

The build expects:

- Apple Silicon macOS
- the project virtual environment at `.venv`
- `pkgbuild` from Xcode Command Line Tools

The script sets `PYTHONPATH` to `src`, so the version probe works from a clean checkout. Install the project dependencies first if the selected Python does not already have PyInstaller and the application dependencies.

## Windows x64

```powershell
.\packaging\windows\build.ps1
```

The script creates the PyInstaller folder under `dist/dlp`. If Inno Setup is installed and `iscc` is on `PATH`, it also creates `dist/dlp-windows-x64-setup.exe`.

The Windows installer adds the application directory to the current user PATH. It does not require administrator privileges.

The Windows build expects an AMD64 Windows host, Python 3.10 or newer, and Inno Setup for the installer. If Inno Setup is absent, the script still produces the PyInstaller folder under `dist/dlp`.

Both builds bundle Python, Textual, yt-dlp, and yt-dlp-ejs. They continue to check for external `ffmpeg`, `ffprobe`, and Deno at runtime and ask before installing them.

The build also generates a `licenses/` directory from the installed runtime distributions. It is copied into the frozen bundle alongside `THIRD_PARTY_NOTICES.md` and contains `MANIFEST.txt` plus the applicable license files.

Before release, run `dlp --version`, `dlp doctor --ui-check`, and the terminal-session smoke test:

```sh
python packaging/smoke_frozen.py dist/dlp/dlp
```

The smoke test launches the actual frozen TUI in a pseudo-terminal and catches missing widget modules such as the v0.1 `_tab_pane` failure. Native CI also installs the generated package and runs the installed binary.

## Installed layout and upgrades

macOS:

- The package installs the bundled app under `/usr/local/libexec/dlp`.
- It creates `/usr/local/bin/dlp` as a symlink to the bundled executable.
- Upgrade by installing a newer `.pkg` over the existing install.

Windows:

- The installer defaults to `Program Files\DLP` for the current user.
- It adds that directory once to the current user's `PATH`.
- Upgrade by running a newer `dlp-windows-x64-setup.exe`; the installer removes its old PATH segment if the install directory changes.

Neither installer removes your saved settings, profiles, download archive, or history from the user config/data directories.

## Uninstall

macOS has no system package manager entry in the unsigned first release. Remove `/usr/local/bin/dlp` and `/usr/local/libexec/dlp` when no other DLP package is installed.

Windows users can uninstall DLP from Apps & Features or run the generated uninstaller. It removes the installed binaries and its own PATH segment, not per-user config/history files.

## CI artifacts

The CI workflow uploads:

- macOS: `dist/dlp-macos-arm64.pkg` and `dist/SHA256SUMS.txt`
- Windows: `dist/dlp-windows-x64-setup.exe`, the portable `dist/dlp` folder, and `dist/SHA256SUMS.txt`

Release tags publish the two installers and a checksum manifest to GitHub Releases. The first release remains unsigned; signing and notarization are intentionally deferred.

`THIRD_PARTY_NOTICES.md` is copied into each PyInstaller bundle and therefore into both installer artifacts.
