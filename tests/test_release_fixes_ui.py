"""Release fixes for the window, settings files, FTP console and Store build."""
import json
import os
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from smftp.core.clients import FTPClient
from smftp.models.entries import Connection
from test_audit_known_issues import close_window


def new_window():
    from smftp.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    return MainWindow()


# ---------- remote files that Windows would run are never launched ----------

def test_executable_types_are_detected():
    from smftp.utils.paths import runs_when_opened
    for name in ("deploy.cmd", "setup.EXE", "raccourci.lnk", "macro.vbs", "page.hta", "x.ps1", "lien.url"):
        assert runs_when_opened(name), name
    for name in ("index.php", "style.css", "photo.jpg", "notes.txt", "README", ".htaccess"):
        assert not runs_when_opened(name), name


@pytest.mark.skipif(os.name != "nt", reason="Bloc-notes et Mark of the Web : Windows uniquement")
def test_external_open_of_an_executable_uses_notepad(tmp_path, monkeypatch):
    from PySide6.QtCore import QProcess
    from PySide6.QtGui import QDesktopServices
    launched, opened = [], []
    monkeypatch.setattr(QProcess, "startDetached", lambda program, args: launched.append((program, args)) or (True, 1))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    window = new_window()
    try:
        script = tmp_path / "deploy.bat"
        script.write_text("echo test")
        window.open_downloaded(dict(local=str(script), remote="/deploy.bat", external=True))
        page = tmp_path / "page.html"
        page.write_text("<p>ok</p>")
        window.open_downloaded(dict(local=str(page), remote="/page.html", external=True))
    finally:
        close_window(window)
    assert [Path(program).name.lower() for program, _ in launched] == ["notepad.exe"]
    assert launched[0][1] == [str(script)]
    assert [Path(url.toLocalFile()) for url in opened] == [page]
    with open(f"{page}:Zone.Identifier", encoding="ascii") as stream:
        assert "ZoneId=3" in stream.read()


def test_external_open_of_an_executable_is_cancelled_by_default(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    window = new_window()
    try:
        window.online = True
        window.connection = Connection("h.test", "u", "SFTP")
        window.remote.selected = lambda: [("/www/setup.exe", False)]
        added = []
        window.transfers.add = lambda *args, **kwargs: added.append(args)
        window.edit_remote(True)
        assert added == []
    finally:
        close_window(window)


# ---------- FTP command console ----------

def test_ftp_command_console_sends_the_command():
    from smftp.workers.session import Session

    class Recorder:
        def __init__(self):
            self.sent = []

        def sendcmd(self, command):
            self.sent.append(command)
            return "200 OK"

    session = Session()
    client = FTPClient()
    client.ftp = Recorder()
    session.client = client
    errors = []
    session.failed.connect(errors.append)
    session.command("SYST")
    assert client.ftp.sent == ["SYST"] and errors == []


# ---------- damaged settings files ----------

def test_corrupt_sites_file_is_kept_aside_and_reported():
    from smftp.utils.storage import data_dir, load_sites, save_sites
    path = data_dir() / "sites.json"
    path.write_text('[{"host": "a.test", "user": "u", "protocol": "SFTP", "port": 22}, ', encoding="utf-8")
    problems = []
    assert load_sites(problems) == []
    assert problems and "illisible" in problems[0]
    backups = list(data_dir().glob("sites.illisible-*.json"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8").startswith('[{"host"')
    save_sites([])
    assert backups[0].exists()


def test_one_bad_profile_is_reported_and_the_others_load():
    from smftp.utils.storage import data_dir, load_sites
    good = {"name": "Prod", "host": "a.test", "user": "u", "protocol": "SFTP", "port": 22}
    (data_dir() / "sites.json").write_text(json.dumps([good, {"host": 5}, "texte"]), encoding="utf-8")
    problems = []
    assert [site["host"] for site in load_sites(problems)] == ["a.test"]
    assert problems and problems[0].startswith("2 profil(s)")


def test_valid_sites_file_reports_nothing():
    from smftp.utils.storage import data_dir, load_sites
    (data_dir() / "sites.json").write_text(
        json.dumps([{"host": "a.test", "user": "u", "protocol": "SFTP", "port": 22}]), encoding="utf-8")
    problems = []
    assert len(load_sites(problems)) == 1 and problems == []
    assert not list(data_dir().glob("sites.illisible-*.json"))


def test_window_reports_a_corrupt_sites_file():
    from smftp.utils.storage import data_dir
    (data_dir() / "sites.json").write_text("{pas du json", encoding="utf-8")
    window = new_window()
    try:
        assert window.sites == [] and window.site_problems
    finally:
        close_window(window)


DAILY_WITH_NULL_DAYS = {"tasks": [{"name": "t", "kind": "sync", "profile": "p", "local": "C:\\x", "remote": "/",
                                   "recurrence": {"type": "daily", "time": "10:00", "days": None}}]}


@pytest.mark.parametrize("content", [
    {"tasks": 5}, {"history": 3}, [1, 2], "texte", DAILY_WITH_NULL_DAYS,
    {"tasks": [{"name": ["liste"], "kind": "sync"}]},
    {"history": [{"status": ["x"]}, {"status": "Réussi"}]},
])
def test_unexpected_schedule_file_never_prevents_startup(content):
    from smftp.core.schedule import ScheduleStore
    from smftp.utils.storage import data_dir
    path = data_dir() / "schedule.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    store = ScheduleStore(path)
    store.load()
    assert store.tasks == []
    assert all(isinstance(value, (str, int, float, bool, type(None))) for row in store.history for value in row.values())


def test_main_window_starts_with_an_unexpected_schedule_file():
    from smftp.utils.storage import data_dir
    (data_dir() / "schedule.json").write_text('{"tasks": 5, "history": 3}', encoding="utf-8")
    close_window(new_window())


# ---------- Delete key only acts on the focused file panel ----------

def test_delete_key_ignores_a_selection_left_in_another_panel(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kwargs: asked.append(args[2]) or QMessageBox.StandardButton.No)
    window = new_window()
    try:
        window.local.selected = lambda: [(str(tmp_path / "important.docx"), False)]
        window.last_panel = window.local
        monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: window.logs))
        window.focused_file_action("remove")
        assert asked == []
        monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: window.local.view))
        window.focused_file_action("remove")
        assert len(asked) == 1
        assert "important.docx" in asked[0] and "sur ce PC" in asked[0]
    finally:
        close_window(window)


# ---------- connection form fits a 1080p screen at 150 % ----------

def test_connection_form_scrolls_and_keeps_its_buttons_visible():
    from PySide6.QtWidgets import QScrollArea
    from smftp.ui.connection_dialog import ConnectionDialog
    QApplication.instance() or QApplication([])
    form = ConnectionDialog()
    assert isinstance(form.scroll, QScrollArea)
    assert not form.scroll.widget().isAncestorOf(form.ok_button)
    form.show()
    assert form.frameGeometry().height() <= form.screen().availableGeometry().height()
    form.reject()


# ---------- the Store build offers no second update channel ----------

def test_store_build_hides_the_github_update_check(monkeypatch):
    from smftp.workers import updates
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "store")
    assert updates.distribution() == "store" and not updates.checks_for_updates()
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "uptodown")
    assert updates.checks_for_updates()
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "nimporte")
    assert updates.distribution() == "github"


def menu_texts(window):
    return [action.text() for top in window.menuBar().actions() if top.menu() for action in top.menu().actions()]


def test_store_build_has_no_update_menu(monkeypatch):
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "store")
    window = new_window()
    try:
        assert not any("mises à jour" in text for text in menu_texts(window))
    finally:
        close_window(window)
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "github")
    window = new_window()
    try:
        assert any("mises à jour" in text for text in menu_texts(window))
    finally:
        close_window(window)


def test_release_page_is_limited_to_the_project():
    from smftp.workers.updates import safe_release_url, RELEASES_PAGE
    assert safe_release_url("https://github.com/Smeth/smftp/releases/tag/v1.2.0").endswith("v1.2.0")
    assert safe_release_url("file:///C:/Windows/System32/calc.exe") == RELEASES_PAGE
    assert safe_release_url("https://github.com/Smeth/smftp.evil.example/x") == RELEASES_PAGE
    assert safe_release_url(None) == RELEASES_PAGE
