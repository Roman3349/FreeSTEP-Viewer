"""Localisation of the GUI (gettext catalogues, editable in Weblate).

Source strings are English. Translations live in ``locale/<language>.po``
(standard gettext PO files, template ``locale/freestep.pot``) and are looked up
at call time, so the language can be switched while the application runs.
Texts use ``str.format`` placeholders::

    tr("Exam {id}", id=158)
    ntr("{n} frame", "{n} frames", count)  # plural forms from the PO header

After changing texts in the sources run ``python -m freestep.gui.po update``
to regenerate the template and merge it into the translations. Adding a
language means adding ``locale/<code>.po`` (Weblate does that itself).

Standard Qt texts (dialog buttons, file dialogs) are translated by Qt itself
via :func:`install_qt_translator`, provided the Qt installation ships them.
"""

from __future__ import annotations

import gettext

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

from .po import LOCALE_DIR, load_translations

#: Language of the source strings.
DEFAULT_LANGUAGE = "en"


def _available_languages() -> dict[str, str]:
    """Return ``{code: native name}`` of the source language and every ``.po`` file."""
    codes = [DEFAULT_LANGUAGE, *sorted(p.stem for p in LOCALE_DIR.glob("*.po"))]
    names = {}
    for code in codes:
        name = QLocale(code).nativeLanguageName() or code
        names[code] = name[:1].upper() + name[1:]
    return names


#: Supported languages: code → name shown in the language menu (in that language).
LANGUAGES: dict[str, str] = _available_languages()

_language = DEFAULT_LANGUAGE
_translations: gettext.NullTranslations = gettext.NullTranslations()
_qt_translator: QTranslator | None = None


def tr(text: str, /, **values: object) -> str:
    """Translate a text into the current language.

    Args:
        text: English source text; must be a literal so that extraction finds it.
        **values: Values for the ``str.format`` placeholders in ``text``.

    Returns:
        The translation (the source text when there is none) with the values filled in.
    """
    translated = _translations.gettext(text)
    return translated.format(**values) if values else translated


def ntr(singular: str, plural: str, n: int, /, **values: object) -> str:
    """Translate a text whose form depends on a number.

    Args:
        singular: English text for ``n == 1``; literal, may use ``{n}``.
        plural: English text for other ``n``; literal, may use ``{n}``.
        n: The number selecting the plural form (Czech has three).
        **values: Values for further placeholders.

    Returns:
        The translated form with ``n`` and the values filled in.
    """
    return _translations.ngettext(singular, plural, n).format(n=n, **values)


def language() -> str:
    """Return the code of the current language (a key of :data:`LANGUAGES`)."""
    return _language


def set_language(code: str) -> None:
    """Switch the language used by :func:`tr` and :func:`ntr`.

    Widgets showing translated texts must be rebuilt afterwards.

    Args:
        code: Language code; unsupported codes fall back to :data:`DEFAULT_LANGUAGE`.
    """
    global _language, _translations  # noqa: PLW0603 — module-wide setting
    _language = code if code in LANGUAGES else DEFAULT_LANGUAGE
    if _language == DEFAULT_LANGUAGE:
        _translations = gettext.NullTranslations()
    else:
        _translations = load_translations(LOCALE_DIR / f"{_language}.po")


def system_language() -> str:
    """Return the supported language matching the system locale, else :data:`DEFAULT_LANGUAGE`."""
    code = QLocale.system().name().split("_")[0]
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


#: Stored/menu value meaning "follow the operating system language".
AUTO_LANGUAGE = "auto"


def resolve_language(preference: str) -> str:
    """Resolve a stored language preference to a concrete language code.

    Args:
        preference: A supported language code, or :data:`AUTO_LANGUAGE` (or any
            unsupported value) to follow the operating system.

    Returns:
        ``preference`` when it names a supported language, else the language from
        :func:`system_language` (Czech when the OS is Czech, otherwise English).
    """
    return preference if preference in LANGUAGES else system_language()


def install_qt_translator(app: QCoreApplication) -> bool:
    """Load Qt's own translations (``qtbase_<lang>.qm``) for the current language.

    Replaces a previously installed translator.

    Args:
        app: The application.

    Returns:
        ``True`` when a translation was found and installed; always ``True`` for English.
    """
    global _qt_translator  # noqa: PLW0603 — one translator per application
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator = None
    if _language == DEFAULT_LANGUAGE:
        return True
    translator = QTranslator(app)
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if not translator.load(f"qtbase_{_language}", path):
        return False
    app.installTranslator(translator)
    _qt_translator = translator
    return True
