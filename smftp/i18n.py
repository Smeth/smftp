"""Interface language: French (source language of the code) or English.

The code is written in French. Every text shown to the user goes through Qt's
translation: ``self.tr("…")`` in Qt classes, ``tr("…")`` from this module
elsewhere (workers, error messages). For English, ``CatalogTranslator`` looks
each French text up in ``resources/translations/smftp_en.json``; a text missing
from the catalog is shown in French, and the test suite reports it.

Conventions for translatable texts:
- a literal string, or a variable that holds one (menu titles in a list);
- variable parts as named placeholders: ``tr("Copie de {name} terminée").format(name=name)``,
  never an f-string inside tr(); Qt's "%1", "%s" and "%n" placeholders work too;
- plurals: ``self.tr("%n fichier(s)", "", count)`` or ``tr("%n fichier(s)", n=count)``.
"""
import json
from pathlib import Path

LANGUAGES = {"fr": "Français", "en": "English"}
CONTEXT = "SMFTP"
CATALOG_DIR = Path(__file__).resolve().parents[1] / "resources" / "translations"

_language = "fr"


def N_(text: str) -> str:
    """Mark a text for translation where it is defined, translated later where shown
    (tools/i18n_extract.py collects it). Returns the text unchanged."""
    return text


def tr(text: str, disambiguation: str | None = None, n: int = -1) -> str:
    """Translate a French text outside Qt classes (thread-safe)."""
    from PySide6.QtCore import QCoreApplication
    if QCoreApplication.instance() is None:
        return text.replace("%n", str(n)) if n >= 0 else text
    return QCoreApplication.translate(CONTEXT, text, disambiguation, n)


def language() -> str:
    """Language of this session: "fr" or "en"."""
    return _language


def configured_language(settings=None) -> str:
    """The saved choice, "auto" meaning the Windows display language."""
    from PySide6.QtCore import QLocale, QSettings
    choice = (settings or QSettings()).value("language", "auto")
    if choice in LANGUAGES:
        return choice
    return "fr" if QLocale.system().language() == QLocale.Language.French else "en"


def load_catalog(code: str) -> dict:
    path = CATALOG_DIR / f"smftp_{code}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: value for key, value in data.items() if isinstance(key, str) and isinstance(value, str) and value}


def _make_translator(catalog: dict):
    from PySide6.QtCore import QTranslator

    class CatalogTranslator(QTranslator):
        """French source text -> translation, whatever the Qt context."""
        missing = set()

        def translate(self, context, source, disambiguation=None, n=-1):
            translated = catalog.get(source)
            if translated is None and source:
                CatalogTranslator.missing.add(source)
            return translated

        def isEmpty(self):
            return False

    return CatalogTranslator()


def install(app, code: str | None = None):
    """Install the translators of the chosen language on the application.

    Returns the list of installed translators (kept alive by the caller).
    """
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    global _language
    _language = code or configured_language()
    installed = []
    if _language == "en":
        QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        translator = _make_translator(load_catalog("en"))
        app.installTranslator(translator)
        installed.append(translator)
        app.catalog_translator = translator
        return installed
    QLocale.setDefault(QLocale(QLocale.Language.French, QLocale.Country.France))
    # Qt's own dialogs and buttons (Cancel, Yes…) in French.
    qt = QTranslator(app)
    if (qt.load("qtbase_fr", str(CATALOG_DIR)) or
            qt.load("qtbase_fr", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))):
        app.installTranslator(qt)
        installed.append(qt)
    return installed
