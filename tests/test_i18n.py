"""English interface: complete catalog, consistent placeholders, no French left on screen."""
import json
import re
import subprocess
import sys
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "resources" / "translations" / "smftp_en.json"
PLACEHOLDER = re.compile(r"\{[a-z_]+\}|%[0-9n]|%s")
FRENCH = re.compile(r"[éèêàùçôîâœ’«»]|\b(le|la|les|des|du|une|est|pas|vous|votre|dans|pour|avec|aucun|fichier|"
                    r"dossier|serveur|connexion|Annuler|Fermer|Ouvrir|Enregistrer|Supprimer|Renommer|Nouvelle?)\b", re.I)


def catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def test_every_text_of_the_code_has_an_english_translation():
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "i18n_extract.py"), "--check"],
                            capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert result.returncode == 0, result.stdout[-3000:]


def test_translations_keep_placeholders_line_breaks_and_prefixes():
    problems = []
    for french, english in catalog().items():
        if "%d/%m" in french:
            continue
        if sorted(PLACEHOLDER.findall(french)) != sorted(PLACEHOLDER.findall(english)):
            problems.append((french, english))
        if french.count("\n") != english.count("\n") or french.count("\t") != english.count("\t"):
            problems.append((french, english))
        if french.startswith("Statut") and not english.startswith("Status"):
            problems.append((french, english))
    assert not problems, problems[:10]


def test_translated_date_formats_are_valid():
    from datetime import datetime
    for french, english in catalog().items():
        if "%d/%m" in french:
            datetime(2026, 11, 26, 9, 5).strftime(english)
            assert "%" in english


@pytest.fixture
def english():
    """English for one test only: the translator is removed afterwards."""
    from smftp import i18n
    app = QApplication.instance() or QApplication([])
    previous = i18n._language
    installed = i18n.install(app, "en")
    translator = installed[0]
    type(translator).missing.clear()
    yield translator
    for item in installed:
        app.removeTranslator(item)
    i18n.install(app, previous) if previous == "en" else None
    i18n._language = previous
    from PySide6.QtCore import QLocale
    QLocale.setDefault(QLocale(QLocale.Language.French, QLocale.Country.France))


def french_left(translator):
    """Texts the application asked to translate and the catalog does not have."""
    return sorted(text for text in type(translator).missing if any(c.isalpha() for c in text))


def test_main_window_menus_and_dialogs_are_in_english(english, monkeypatch):
    from PySide6.QtWidgets import QDialog, QMessageBox
    from smftp.ui.main_window import MainWindow
    from smftp.ui.connection_dialog import ConnectionDialog
    from smftp.ui.settings import SettingsDialog
    from test_audit_known_issues import close_window
    monkeypatch.setattr(QDialog, "exec", lambda dialog: 0)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)
    window = MainWindow()
    try:
        titles = [action.text() for action in window.menuBar().actions()]
        assert titles[0] == "&File" and "&Help" in titles
        for top in window.menuBar().actions():
            if top.menu():
                for action in top.menu().actions():
                    assert not FRENCH.search(action.text().replace("&", "")), action.text()
        assert window.remote.count.text() == "Not connected"
        ConnectionDialog(window).close()
        SettingsDialog(window.settings, window).close()
        window.show_help()
        window.manage_sites()
        window.show_schedule()
        window.build_context_menu(window.local)
        window.build_context_menu(window.remote)
        window.show_about()
    finally:
        close_window(window)
    assert not french_left(english), french_left(english)


def test_error_messages_and_sizes_are_in_english(english):
    import socket
    from smftp.core.errors import describe_error
    from smftp.ui.explorer import size_label
    from smftp.models.entries import Connection
    info = describe_error(socket.gaierror(11001, "getaddrinfo failed"), Connection("h.test", "u", "SFTP"))
    assert not FRENCH.search(info.summary), info.summary
    assert size_label(2048) == "2.0 KB" and size_label(10) == "10 B"
    assert not french_left(english), french_left(english)


def test_assistant_answers_in_the_interface_language(english):
    from smftp.core import assistant
    assert "English" in assistant.system_prompt()


def test_automatic_language_follows_windows(monkeypatch, tmp_path):
    from PySide6.QtCore import QLocale, QSettings
    from smftp import i18n
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(QLocale, "system", staticmethod(lambda: QLocale(QLocale.Language.German)))
    assert i18n.configured_language(settings) == "en"
    monkeypatch.setattr(QLocale, "system", staticmethod(lambda: QLocale(QLocale.Language.French)))
    assert i18n.configured_language(settings) == "fr"
    settings.setValue("language", "en")
    assert i18n.configured_language(settings) == "en"
