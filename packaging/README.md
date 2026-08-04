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

## Windows x64

```powershell
.\packaging\windows\build.ps1
```

The script creates the PyInstaller folder under `dist/dlp`. If Inno Setup is installed and `iscc` is on `PATH`, it also creates `dist/dlp-windows-x64-setup.exe`.

The Windows installer adds the application directory to the current user PATH. It does not require administrator privileges.

The Windows build expects an AMD64 Windows host, Python 3.10 or newer, and Inno Setup for the installer. If Inno Setup is absent, the script still produces the PyInstaller folder under `dist/dlp`.

Both builds bundle Python, Textual, yt-dlp, and yt-dlp-ejs. They continue to check for external `ffmpeg`, `ffprobe`, and Deno at runtime and ask before installing them.

`THIRD_PARTY_NOTICES.md` is copied into each PyInstaller bundle and therefore into both installer artifacts.
