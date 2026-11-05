"""Render the application icon (``src/freestep/gui/icon.svg``) for the packages.

Usage::

    python packaging/make_icons.py OUTPUT_DIR

Writes ``freestep-viewer-<size>.png`` for the usual icon sizes, a multi-resolution
``freestep-viewer.ico`` for Windows when Pillow is installed and ``freestep-viewer.icns``
for macOS when ``iconutil`` is available (macOS only).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ICON = Path(__file__).resolve().parent.parent / "src" / "freestep" / "gui" / "icon.svg"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)


def main(argv: list[str]) -> int:
    """Render the icons into the directory given as the only argument."""
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt  # noqa: PLC0415 — needs QT_QPA_PLATFORM set first
    from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: PLC0415
    from PySide6.QtSvg import QSvgRenderer  # noqa: PLC0415

    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication([])
    renderer = QSvgRenderer(str(ICON))
    if not renderer.isValid():
        print(f"Invalid SVG: {ICON}", file=sys.stderr)
        return 1
    pngs = []
    for size in SIZES:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        path = out / f"freestep-viewer-{size}.png"
        image.save(str(path))
        pngs.append(path)
    del app
    try:
        from PIL import Image  # noqa: PLC0415 — optional dependency
    except ImportError:
        print("Pillow not installed, skipping the .ico file")
    else:
        largest = Image.open(pngs[-1])
        largest.save(out / "freestep-viewer.ico", sizes=[(s, s) for s in SIZES if s <= 256])
    if shutil.which("iconutil"):
        _make_icns(out)
    print(f"Icons written to {out}")
    return 0


def _make_icns(out: Path) -> None:
    """Build ``freestep-viewer.icns`` from the rendered PNGs with macOS ``iconutil``."""
    iconset = out / "freestep-viewer.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        shutil.copy(out / f"freestep-viewer-{size}.png", iconset / f"icon_{size}x{size}.png")
        shutil.copy(out / f"freestep-viewer-{size * 2}.png", iconset / f"icon_{size}x{size}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out / "freestep-viewer.icns")], check=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
