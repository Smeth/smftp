"""Connection cancel, menus, site manager, panels, permissions, terminal, single instance."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton
from smftp.models.entries import Connection, Entry
from smftp.ui.main_window import MainWindow
from test_audit_known_issues import close_window


def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window():
    app()
    win = MainWindow()
    yield win
    win.__dict__.pop("sync_deleter", None)
    close_window(win)


# ---------- connection in progress ----------

def test_a_connection_in_progress_can_be_cancelled(window, monkeypatch):
    aborted = []
    monkeypatch.setattr(window.session, "abort_connect", lambda: aborted.append(True))
    window.connection = Connection("lent.test", "u", "SFTP")
    monkeypatch.setattr(window, "connect_requested", type("S", (), {"emit": lambda self, c: None})())
    window.start_connection()
    assert window.disconnect_button.text() == "Annuler" and window.disconnect_button.isEnabled()
    window.disconnect_button.click()
    assert aborted == [True]
    from smftp.core.errors import ErrorInfo
    window.on_session_error(ErrorInfo("Connexion annulée.", ""))
    assert window.disconnect_button.text() == "Déconnecter" and not window.disconnect_button.isEnabled()
    assert not window.busy


def test_session_reports_a_cancelled_connection_quietly(monkeypatch):
    from smftp.workers import session as module
    app()
    session = module.Session()

    class Slow:
        def connect(self, connection):
            session.abort_connect()
            raise OSError("socket closed")

        def abort(self):
            pass

        def close(self):
            pass

        def report(self, message):
            pass
    monkeypatch.setattr(module, "make_client", lambda *args: Slow())
    failures = []
    session.failed.connect(failures.append)
    session.connect_to(Connection("h.test", "u", "SFTP"))
    assert [f.summary for f in failures] == ["Connexion annulée."]


def test_new_connection_while_connecting_explains_why(window):
    window.busy = True
    window.new_connection()
    assert "déjà en cours" in window.statusBar().currentMessage()
    window.busy = False


def test_window_title_names_the_connected_server(window):
    window.connection = Connection("ftp.exemple.fr", "u", "SFTP", name="Production")
    window.on_connected("/")
    assert window.windowTitle() == "SMFTP — Production"
    window.on_disconnected()
    assert window.windowTitle() == window.base_title


# ---------- recent connections and site manager ----------

def test_recent_connections_can_be_cleared_and_follow_deletions(window):
    from smftp.utils.profiles import identity
    first = Connection("a.test", "u", "SFTP", name="A")
    second = Connection("b.test", "u", "SFTP", name="B")
    window.remember_recent(first)
    window.remember_recent(second)
    window.forget_recent(first.identity)
    assert [identity(site) for site in window.recent_sites] == [second.identity]
    assert any(a.text() == "Effacer la liste" for a in window.recent_menu.actions())
    window.clear_recent()
    assert window.recent_sites == []
    assert [a.text() for a in window.recent_menu.actions()] == ["Aucune connexion récente"]


def test_site_manager_buttons_follow_the_selection(window, monkeypatch):
    window.sites = [dict(host="h.test", user="u", protocol="SFTP", port=22, name="Prod")]
    seen = {}

    def inspect(dialog):
        buttons = {b.text(): b for b in dialog.findChildren(QPushButton)}
        seen["before"] = {name: buttons[name].isEnabled() for name in ("Connecter", "Modifier", "Supprimer", "Nouveau", "Fermer")}
        from PySide6.QtWidgets import QTreeWidget
        tree = dialog.findChild(QTreeWidget)
        tree.setCurrentItem(tree.topLevelItem(0).child(0))
        seen["after"] = buttons["Supprimer"].isEnabled()
        return 0
    monkeypatch.setattr(QDialog, "exec", inspect)
    window.manage_sites()
    assert seen["before"] == {"Connecter": False, "Modifier": False, "Supprimer": False, "Nouveau": True, "Fermer": True}
    assert seen["after"]


# ---------- remote panel ----------

def test_remote_panel_counts_what_it_shows_and_lists_folders_first(window):
    entries = [Entry("b.txt", False, 1, 1.0), Entry(".htaccess", False, 1, 1.0), Entry("z-dossier", True, 0, 1.0),
               Entry("a-dossier", True, 0, 1.0), Entry("script.php", False, 3, 1.0)]
    window.remote.set_entries("/www", entries)
    assert window.remote.count.text() == "4 élément(s) sur 5"
    view = window.remote.view
    names = [view.topLevelItem(i).text(0) for i in range(view.topLevelItemCount())]
    assert names[:2] == ["a-dossier", "z-dossier"]
    view.sortItems(0, Qt.SortOrder.DescendingOrder)
    names = [view.topLevelItem(i).text(0) for i in range(view.topLevelItemCount())]
    assert names[:2] == ["z-dossier", "a-dossier"]
    php = next(view.topLevelItem(i) for i in range(view.topLevelItemCount()) if view.topLevelItem(i).text(0) == "script.php")
    assert php.text(2) == "Fichier PHP"


def test_context_menu_disables_what_cannot_run(window, tmp_path):
    window.remote.selected = lambda: []
    labels = {a.text(): a.isEnabled() for a in window.build_context_menu(window.remote).actions() if a.text()}
    assert not labels["Télécharger"] and not labels["Modifier / Aperçu…"] and not labels["Permissions…"]
    window.local.selected = lambda: [(str(tmp_path / "a.txt"), False)]
    labels = {a.text(): a.isEnabled() for a in window.build_context_menu(window.local).actions() if a.text()}
    assert not labels["Téléverser"]  # No server.
    assert labels["Renommer"] and labels["Supprimer"]


# ---------- permissions ----------

def test_permissions_accept_only_octal_digits(monkeypatch):
    from smftp.ui.permissions import PermissionsDialog
    app()
    dialog = PermissionsDialog(None, 0o644)
    validator = dialog.octal.validator()
    from PySide6.QtGui import QValidator
    assert validator.validate("8", 0)[0] == QValidator.State.Invalid
    assert validator.validate("75", 0)[0] != QValidator.State.Invalid
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warned.append(args[2]))
    dialog.octal.setText("64")
    dialog.accept_valid()
    assert warned and "644" in warned[0] and dialog.result() == 0


# ---------- terminal ----------

def test_terminal_text_zooms_with_ctrl_wheel(monkeypatch, tmp_path):
    import PySide6.QtCore as core
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    from smftp.ui.terminal import TerminalView
    app()
    ini = str(tmp_path / "terminal.ini")
    real = core.QSettings
    monkeypatch.setattr(core, "QSettings", lambda *args: real(ini, real.Format.IniFormat))
    view = TerminalView()
    before = view.term_font.pointSize()
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
    view.wheelEvent(event)
    assert view.term_font.pointSize() == before + 1
    assert real(ini, real.Format.IniFormat).value("terminal_font_size", type=int) == before + 1


def test_closing_a_live_terminal_tab_asks_first(monkeypatch):
    from smftp.ui.terminal import TerminalArea
    app()
    area = TerminalArea(True)
    closed, asked = [], []
    monkeypatch.setattr(area, "close_session", closed.append)

    class Live:
        state = "open"
        title = "serveur"
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: asked.append(args[2]) or QMessageBox.StandardButton.No)
    area.request_close(Live())
    assert asked and closed == []
    finished = type("Done", (), {"state": "closed", "title": "x"})()
    area.request_close(finished)
    assert closed == [finished]


# ---------- scheduled task: file or folder ----------

def test_cancelling_the_upload_choice_opens_no_other_picker(window, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from smftp.ui.scheduler import TaskEditor
    opened = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: opened.append("file") or ("", ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args, **kwargs: opened.append("folder") or "")
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)  # Closed without choosing.
    dialog = TaskEditor(window.scheduler, window)
    dialog.kind.setCurrentIndex(dialog.kind.findData("upload"))
    dialog.choose_local()
    assert opened == []


# ---------- second launch ----------

def test_second_launch_brings_the_window_back(window, monkeypatch):
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path
    from smftp.utils.storage import listen_for_other_instances
    revealed = []
    monkeypatch.setattr(window, "reveal", lambda: revealed.append(True))
    server = listen_for_other_instances(window)
    assert server is not None
    # A real second process, as when the user starts SMFTP again.
    root = Path(__file__).resolve().parents[1]
    second = subprocess.Popen([sys.executable, "-c", "from PySide6.QtCore import QCoreApplication; "
                               "app = QCoreApplication([]); from smftp.utils.storage import ask_running_instance_to_show; "
                               "raise SystemExit(0 if ask_running_instance_to_show() else 1)"],
                              cwd=root, env=dict(os.environ))
    deadline = time.monotonic() + 15
    while (not revealed or second.poll() is None) and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert revealed and second.wait(5) == 0
    server.close()


# ---------- help and sync ----------

def test_help_lists_the_keys_and_links_the_documentation(window, monkeypatch):
    from PySide6.QtWidgets import QTextBrowser
    shown = {}

    def capture(dialog):
        shown["text"] = dialog.findChild(QTextBrowser).toPlainText()
        return 0
    monkeypatch.setattr(QDialog, "exec", capture)
    window.show_help()
    for words in ("Ctrl+K", "Alt+← / Alt+→", "Ctrl+Maj+F", "Tâches planifiées", "Assistant IA", "Documentation complète"):
        assert words in shown["text"], words


def test_comparison_reports_its_progress(tmp_path):
    from smftp.workers.sync import SyncPlanner
    app()
    planner = SyncPlanner(Connection("h", "u"), str(tmp_path), "/", True)
    counts = []
    planner.progress.connect(counts.append)
    for _ in range(250):
        planner.count_seen()
    assert counts == [100, 200]
