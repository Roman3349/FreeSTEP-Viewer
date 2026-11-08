"""Name, ID and icon of the application."""

from pathlib import Path

#: Application name (window title, About dialog, settings).
APP_NAME = "FreeSTEP Viewer"
#: Project homepage (About dialog).
APP_URL = "https://github.com/Roman3349/FreeSTEP-Viewer"
#: Reverse-DNS application ID (desktop file, Flatpak, AppStream).
APP_ID = "cz.romanondracek.freestepviewer"
#: Application icon (also used by the packaging scripts).
ICON_PATH = Path(__file__).with_name("icon.svg")
