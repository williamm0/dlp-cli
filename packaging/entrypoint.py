"""PyInstaller entrypoint with an absolute package import."""

from dlp.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
