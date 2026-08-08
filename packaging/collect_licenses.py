"""Collect license metadata for the distributions included in a frozen build."""

from __future__ import annotations

import argparse
import importlib.metadata
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from packaging.markers import default_environment
from packaging.requirements import Requirement

_ROOT_DISTRIBUTIONS = {
    "platformdirs",
    "textual",
    "tomli-w",
    "yt-dlp",
    "yt-dlp-ejs",
}
_LICENSE_NAME = re.compile(r"(?i)(^|[-_.])(license|licence|copying|notice)([-_.]|$)")
_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    collect(args.output)
    return 0


def collect(output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    selected = _dependency_closure()
    manifest: list[str] = ["Distribution | Version | License metadata"]
    for name in sorted(selected):
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        metadata = distribution.metadata.json
        display_name = str(metadata.get("name") or name)
        license_text = str(metadata.get("license") or "See copied license files")
        home_page = str(metadata.get("home_page") or "")
        folder = output / f"{display_name}-{distribution.version}"
        folder.mkdir(parents=True, exist_ok=True)
        license_files = _copy_license_files(distribution, folder)
        (folder / "METADATA.txt").write_text(
            f"Name: {display_name}\n"
            f"Version: {distribution.version}\n"
            f"License: {license_text}\n"
            f"Home-page: {home_page}\n"
            f"Files: {', '.join(license_files) or 'none'}\n",
            encoding="utf-8",
        )
        manifest.append(
            f"{display_name} | {distribution.version} | {license_text}"
        )
    (output / "MANIFEST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")


def _dependency_closure() -> set[str]:
    selected: set[str] = set()
    pending = list(_ROOT_DISTRIBUTIONS)
    while pending:
        name = pending.pop()
        normalized = name.lower().replace("_", "-")
        if normalized in selected:
            continue
        selected.add(normalized)
        try:
            requirements = importlib.metadata.requires(name) or []
        except importlib.metadata.PackageNotFoundError:
            continue
        for requirement in requirements:
            try:
                parsed = Requirement(requirement)
            except (ValueError, TypeError):
                match = _REQUIREMENT_NAME.match(requirement)
                if match:
                    pending.append(match.group(1))
                continue
            if parsed.marker is not None and not parsed.marker.evaluate(
                cast(Mapping[str, str], default_environment())
            ):
                continue
            pending.append(parsed.name)
    return selected


def _copy_license_files(
    distribution: importlib.metadata.Distribution, folder: Path
) -> list[str]:
    copied: list[str] = []
    for file in distribution.files or ():
        if not _LICENSE_NAME.search(Path(file).name):
            continue
        source = Path(str(distribution.locate_file(file)))
        if not source.is_file():
            continue
        destination = folder / Path(file).name
        if destination.exists():
            continue
        shutil.copyfile(source, destination)
        copied.append(destination.name)
    return copied


if __name__ == "__main__":
    raise SystemExit(main())
