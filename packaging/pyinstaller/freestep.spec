# PyInstaller spec: one folder with FreeStepViewer.exe (GUI) and freestep.exe (CLI);
# on macOS additionally "FreeSTEP Viewer.app" (the CLI is Contents/MacOS/freestep).
#
# Build from the repository root (after rendering the icons):
#   python packaging/make_icons.py build/icons
#   pyinstaller --noconfirm --distpath dist --workpath build/pyinstaller packaging/pyinstaller/freestep.spec
# ruff: noqa
# mypy: ignore-errors
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent.parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
APP_ID = "cz.romanondracek.FreeStepViewer"
ICON = ROOT / "build" / "icons" / ("freestep-viewer.icns" if sys.platform == "darwin" else "freestep-viewer.ico")
icon = [str(ICON)] if ICON.exists() else None

# Translations (locale/*.po) and the application icon
datas = collect_data_files("freestep", includes=["gui/locale/*", "gui/icon.svg", "py.typed"])
# Only the Qt modules the viewer uses
excludes = ["tkinter", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore"]

gui = Analysis(
    [str(ROOT / "packaging" / "pyinstaller" / "freestep_viewer.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=["PySide6.QtSvg"],
    excludes=excludes,
)
cli = Analysis(
    [str(ROOT / "packaging" / "pyinstaller" / "freestep_cli.py")],
    pathex=[str(ROOT / "src")],
    excludes=[*excludes, "PySide6"],
)

gui_exe = EXE(
    PYZ(gui.pure),
    gui.scripts,
    [],
    exclude_binaries=True,
    name="FreeStepViewer",
    console=False,
    icon=icon,
)
cli_exe = EXE(
    PYZ(cli.pure),
    cli.scripts,
    [],
    exclude_binaries=True,
    name="freestep",
    console=True,
    icon=icon,
)
collected = COLLECT(
    gui_exe,
    gui.binaries,
    gui.datas,
    cli_exe,
    cli.binaries,
    cli.datas,
    name="FreeStepViewer",
)

if sys.platform == "darwin":
    BUNDLE(
        collected,
        name="FreeSTEP Viewer.app",
        icon=icon[0] if icon else None,
        bundle_identifier=APP_ID,
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "FreeSTEP exam",
                    "CFBundleTypeExtensions": ["fre"],
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Alternate",
                },
                {
                    "CFBundleTypeName": "FreeSTEP export",
                    "CFBundleTypeExtensions": ["zip"],
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Alternate",
                },
            ],
        },
    )
