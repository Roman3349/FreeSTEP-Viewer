"""Generate the Flatpak module with the Python dependencies from ``uv.lock``.

Flatpak builds run without network access, so every dependency must be listed
as a source with its checksum. This script takes the exact versions and hashes
locked in ``uv.lock`` (PySide6 comes from the ``io.qt.PySide.BaseApp`` base and is
skipped) and writes ``python3-dependencies.json`` next to it.

Usage (after changing dependencies and running ``uv lock``)::

    python packaging/flatpak/generate_sources.py          # write the module
    python packaging/flatpak/generate_sources.py --check  # exit code 1 when outdated
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "uv.lock"
OUTPUT = Path(__file__).with_name("python3-dependencies.json")
PROJECT = "freestep"
#: Provided by the Flatpak base application.
PROVIDED = {"pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6"}
#: CPython versions of the KDE runtimes the manifest may use.
PYTHON_TAGS = ("cp312", "cp313", "cp314")
_ARCH_RE = {"x86_64": re.compile(r"manylinux[^-]*x86_64"), "aarch64": re.compile(r"manylinux[^-]*aarch64")}


def _runtime_packages(lock: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the locked packages the project needs at runtime (without dev groups and provided ones)."""
    packages = {p["name"]: p for p in lock["package"]}
    needed: list[str] = []
    stack = [d["name"] for d in packages[PROJECT].get("dependencies", [])]
    while stack:
        name = stack.pop()
        if name in needed or name in PROVIDED:
            continue
        needed.append(name)
        stack.extend(d["name"] for d in packages[name].get("dependencies", []))
    return [packages[name] for name in sorted(needed)]


def _sources(package: dict[str, Any]) -> list[dict[str, Any]]:
    """Pick the files of one package: a pure wheel, else binary wheels per architecture, else the sdist."""
    wheels = package.get("wheels", [])
    pure = [w for w in wheels if w["url"].endswith("-none-any.whl")]
    if pure:
        return [_file(pure[0])]
    binary = []
    for arch, pattern in _ARCH_RE.items():
        for tag in PYTHON_TAGS:
            match = next((w for w in wheels if f"-{tag}-{tag}-" in w["url"] and pattern.search(w["url"])), None)
            if match is not None:
                binary.append(_file(match, arch))
    if binary:
        return binary
    if "sdist" in package:
        return [_file(package["sdist"])]
    raise SystemExit(f"No usable distribution for {package['name']}")


def _file(dist: dict[str, Any], arch: str | None = None) -> dict[str, Any]:
    """Build a Flatpak ``file`` source from a uv.lock distribution entry."""
    algorithm, digest = dist["hash"].split(":", 1)
    if algorithm != "sha256":
        raise SystemExit(f"Unsupported hash {algorithm} for {dist['url']}")
    source: dict[str, Any] = {"type": "file", "url": dist["url"], "sha256": digest}
    if arch is not None:
        source["only-arches"] = [arch]
    return source


def build_module() -> dict[str, Any]:
    """Build the Flatpak module description."""
    lock = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    packages = _runtime_packages(lock)
    names = " ".join(p["name"] for p in packages)
    return {
        "name": "python3-dependencies",
        "buildsystem": "simple",
        "build-commands": [
            'pip3 install --verbose --exists-action=i --no-index --find-links="file://${PWD}" '
            f"--prefix=${{FLATPAK_DEST}} --no-build-isolation {names}"
        ],
        "sources": [source for package in packages for source in _sources(package)],
    }


def main(argv: list[str]) -> int:
    """Write the module, or with ``--check`` verify that it is up to date."""
    content = json.dumps(build_module(), indent=4) + "\n"
    if "--check" in argv:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != content:
            print(f"{OUTPUT.name} is outdated, run: python {Path(__file__).relative_to(ROOT)}")
            return 1
        return 0
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Written {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
