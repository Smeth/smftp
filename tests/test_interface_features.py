"""Bookmarks, quick connect, synchronized browsing, recursive permissions, scaled fonts,
narrow panels, local dates, conflict menu, history and assistant reports in the interface language."""
import json
from datetime import datetime
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog
from smftp.core.errors import ErrorInfo
from smftp.models.entries import Connection, Entry
from smftp.ui.main_window import MainWindow
from test_audit_known_issues import close_window
from test_i18n import english, french_left  # noqa: F401  (fixture)
from test_ux_basics import wait_until


def new_window():
    QApplication.instance() or QApplication([])
    return MainWindow()


REMOTE_TREE = {
    "/": [Entry("www", True)],
    "/www": [Entry("css", True), Entry("data", True), Entry("lien", False, permissions="lrwxrwxrwx"),
             Entry("index.html", False, 10)],
    "/www/css": [Entry("img", True)],
    "/www/css/img": [],
    "/www/data": [],
    "/autre": [],
}


def fake_server(window, tree=REMOTE_TREE):
    """Browsing without a network: listings answer at once from ``tree``; a missing
    folder fails like a server refusal."""
    window.online = True
    window.connection = Connection("exemple.fr", "alice", "SFTP", 22)

    def answer(path):
        if path in tree:
            window.on_listed(path, tree[path])
        else:
            window.session.failed.emit(ErrorInfo("550 absent", ""))
    window.list_requested.connect(answer)
    window.on_listed("/www", tree["/www"])


def same(a, b):
    return Path(a).resolve() == Path(b).resolve()


# ----- bookmarks -----
def test_bookmarks_are_saved_listed_and_opened(tmp_path):
    from smftp.utils.storage import data_dir
    site = tmp_path / "site"
    (site / "css").mkdir(parents=True)
    window = new_window()
    try:
        assert window.add_bookmark_action.shortcut().toString() == "Ctrl+D"
        window.update_bookmarks_menu()
        assert not window.add_bookmark_action.isEnabled()  # Not connected.
        fake_server(window)
        window.local.navigate(str(site))
        bookmark = window.add_bookmark("Mon site")
        assert bookmark == {"name": "Mon site", "local": window.local.path, "remote": "/www"}
        saved = json.loads((data_dir() / "bookmarks.json").read_text(encoding="utf-8"))
        assert saved == {window.connection.identity: [bookmark]}
        window.update_bookmarks_menu()
        titles = [action.text() for action in window.bookmarks_menu.actions()]
        assert "Mon site" in titles and window.add_bookmark_action.isEnabled()
        # Elsewhere on both sides, then the bookmark brings both folders back.
        window.local.navigate(str(site / "css"))
        window.remote.navigate("/autre")
        assert window.remote.path == "/autre"
        next(a for a in window.bookmarks_menu.actions() if a.text() == "Mon site").trigger()
        assert same(window.local.path, site) and window.remote.path == "/www"
    finally:
        close_window(window)


def test_bookmarks_can_be_renamed_and_removed(tmp_path, monkeypatch):
    window = new_window()
    try:
        fake_server(window)
        window.local.navigate(str(tmp_path))
        window.add_bookmark("Un")
        window.add_bookmark("Deux")
        monkeypatch.setattr(QDialog, "exec", lambda dialog: 0)
        monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Premier", True))
        window.manage_bookmarks()
        actions = window.bookmarks_dialog_actions
        tree = actions["tree"]
        assert tree.topLevelItemCount() == 1 and tree.topLevelItem(0).childCount() == 2
        tree.setCurrentItem(tree.topLevelItem(0).child(0))
        actions["rename"]()
        assert [b["name"] for b in window.current_bookmarks()] == ["Premier", "Deux"]
        tree.setCurrentItem(tree.topLevelItem(0).child(1))
        actions["remove"]()
        assert [b["name"] for b in window.current_bookmarks()] == ["Premier"]
    finally:
        close_window(window)


def test_deleting_a_profile_deletes_its_bookmarks(tmp_path, monkeypatch):
    import smftp.ui.main_window as module
    from smftp.utils.profiles import normalize_profile, identity
    from smftp.utils.storage import load_bookmarks
    monkeypatch.setattr(module, "store_credential", lambda connection: None)
    window = new_window()
    try:
        site = normalize_profile(dict(host="exemple.fr", user="alice", protocol="SFTP", port=22, name="Exemple"))
        window.sites = [site]
        window.refresh_sites()
        window.bookmarks = {identity(site): [{"name": "a", "local": str(tmp_path), "remote": "/"}],
                            "FTP://autre@ailleurs:21": [{"name": "b", "local": str(tmp_path), "remote": "/"}]}
        window.save_bookmark_changes()
        window.delete_profile(site)
        assert window.sites == []
        assert list(load_bookmarks()) == ["FTP://autre@ailleurs:21"]
    finally:
        close_window(window)


def test_unreadable_bookmarks_file_is_set_aside():
    from smftp.utils.storage import data_dir, load_bookmarks, save_bookmarks
    path = data_dir() / "bookmarks.json"
    path.write_text("{ pas du json", encoding="utf-8")
    problems = []
    assert load_bookmarks(problems) == {}
    assert problems and list(data_dir().glob("bookmarks.illisible-*.json"))
    # Readable entries are kept, broken ones reported.
    path.write_text(json.dumps({"SFTP://a@h:22": [{"name": "ok", "local": "C:/x", "remote": "/"}, {"name": ""}]}),
                    encoding="utf-8")
    problems = []
    assert load_bookmarks(problems) == {"SFTP://a@h:22": [{"name": "ok", "local": "C:/x", "remote": "/"}]}
    assert len(problems) == 1
    save_bookmarks({"SFTP://a@h:22": [{"name": "ok", "local": "C:/x", "remote": "/"}]})
    assert not list(data_dir().glob("bookmarks-*.tmp"))  # Atomic write leaves no temporary file.


# ----- quick connect -----
def test_quick_connect_starts_on_ftp_and_offers_every_protocol(monkeypatch):
    window = new_window()
    try:
        started = []
        monkeypatch.setattr(window, "start_connection", lambda: started.append(window.connection))
        bar = window.quick_bar
        assert bar.protocol_value() == "FTP"
        assert [bar.protocol.itemData(i) for i in range(bar.protocol.count())] == ["FTP", "FTPS", "FTPSI", "SFTP", "SSH"]
        # A bare host on FTP: anonymous login, port 21.
        bar.host.setText("ftp.exemple.fr")
        bar.button.click()
        assert (started[-1].protocol, started[-1].port, started[-1].user) == ("FTP", 21, "anonymous")
        # Implicit FTPS chosen in the list: port 990 when "auto".
        bar.protocol.setCurrentIndex(bar.protocol.findData("FTPSI"))
        bar.host.setText("secure.exemple.fr")
        bar.user.setText("alice")
        bar.button.click()
        assert (started[-1].protocol, started[-1].port) == ("FTPSI", 990)
        # A full address sets the list to its scheme.
        bar.host.setText("sftp://bob@ssh.exemple.fr")
        assert bar.protocol_value() == "SFTP"
    finally:
        close_window(window)


def test_quick_connect_accepts_a_full_address_without_creating_a_profile(monkeypatch):
    from smftp.utils.storage import data_dir
    window = new_window()
    try:
        started = []
        monkeypatch.setattr(window, "start_connection", lambda: started.append(window.connection))
        bar = window.quick_bar
        bar.host.setText("ftp://alice@exemple.fr:2121/www")
        bar.password.setText("motdepasse")
        bar.button.click()
        connection = started[-1]
        assert (connection.protocol, connection.host, connection.port, connection.user) == ("FTP", "exemple.fr", 2121, "alice")
        assert connection.password == "motdepasse" and connection.remote_directory == "/www"
        assert window.sites == [] and not (data_dir() / "sites.json").exists()
        assert window.quick_history[0] == "ftp://alice@exemple.fr:2121/www"
        assert "motdepasse" not in json.dumps(window.settings.value("quick_connect/history", [], type=list))
        # The address chose FTP in the protocol list.
        assert bar.protocol_value() == "FTP"
        # A bare host with the protocol chosen in the list: SFTP needs a user.
        bar.protocol.setCurrentIndex(bar.protocol.findData("SFTP"))
        bar.host.setText("serveur.exemple.fr")
        bar.password.clear()
        bar.button.click()
        assert len(started) == 1 and "utilisateur" in window.statusBar().currentMessage()
        bar.user.setText("bob")
        bar.button.click()
        assert (started[-1].protocol, started[-1].port, started[-1].user) == ("SFTP", 22, "bob")
        for number in range(12):
            bar.host.setText(f"h{number}.exemple.fr")
            bar.button.click()
        assert len(window.quick_history) == 10 and window.quick_history[0] == "sftp://bob@h11.exemple.fr:22"
    finally:
        close_window(window)


def test_quick_connect_bar_visibility_is_remembered():
    window = new_window()
    try:
        assert window.quick_bar_action.isChecked() and not window.quick_bar.isHidden()
        window.quick_bar_action.setChecked(False)
        assert window.quick_bar.isHidden()
    finally:
        close_window(window)
    window = new_window()
    try:
        assert not window.quick_bar_action.isChecked() and window.quick_bar.isHidden()
    finally:
        close_window(window)


# ----- synchronized browsing -----
def test_synchronized_navigation_follows_both_sides(tmp_path):
    site = tmp_path / "site"
    (site / "css" / "img").mkdir(parents=True)
    (site / "js").mkdir()
    window = new_window()
    try:
        window.sync_navigation_action.setChecked(True)
        assert not window.sync_navigation_action.isChecked()  # Not connected: refused.
        fake_server(window)
        window.local.navigate(str(site))
        window.sync_navigation_button.click()  # The small button and the menu share their state.
        assert window.sync_navigation_action.isChecked()
        window.local.navigate(str(site / "css"))
        assert window.remote.path == "/www/css"
        window.remote.navigate("/www/css/img")
        assert same(window.local.path, site / "css" / "img")
        window.remote.parent_folder()
        assert same(window.local.path, site / "css") and window.remote.path == "/www/css"
        window.local.parent_folder()
        assert window.remote.path == "/www"
        # "js" does not exist on the server: message and the mode stops.
        window.local.navigate(str(site / "js"))
        assert not window.sync_navigation_action.isChecked() and not window.sync_navigation_button.isChecked()
        assert "/www/js" in window.statusBar().currentMessage()
        assert window.remote.path == "/www"
    finally:
        close_window(window)


def test_synchronized_navigation_stops_when_a_folder_is_missing(tmp_path):
    site = tmp_path / "site"
    (site / "css" / "img").mkdir(parents=True)
    window = new_window()
    try:
        fake_server(window)
        window.local.navigate(str(site))
        window.sync_navigation_action.setChecked(True)
        # Missing on this PC.
        window.remote.navigate("/www/data")
        assert not window.sync_navigation_action.isChecked() and "data" in window.statusBar().currentMessage()
        # Refused by the server (deeper than the listing on screen).
        window.remote.navigate("/www")
        window.local.navigate(str(site))
        window.sync_navigation_action.setChecked(True)
        (site / "neuf" / "sous").mkdir(parents=True)
        window.local.navigate(str(site / "neuf" / "sous"))
        assert not window.sync_navigation_action.isChecked()
        assert "/www/neuf/sous" in window.statusBar().currentMessage()
    finally:
        close_window(window)


# ----- recursive permissions -----
class TreeClient:
    def __init__(self):
        self.tree = {"/www": [Entry("css", True, permissions="drwxr-xr-x"), Entry("index.html", False),
                              Entry("lien", True, permissions="lrwxrwxrwx"), Entry("verrou.txt", False)],
                     "/www/css": [Entry("style.css", False)]}
        self.changes = {}

    def listdir(self, path):
        return self.tree[path]

    def chmod(self, path, mode):
        if path.endswith("verrou.txt"):
            raise PermissionError("550 refusé")
        self.changes[path] = mode

    def report(self, message):
        pass


def test_recursive_permissions_skip_links_and_report_errors():
    from smftp.workers.session import Session
    session = Session()
    session.client = TreeClient()
    messages, done = [], []
    session.progress.connect(messages.append)
    session.operated.connect(lambda: done.append(True))
    session.operate("chmod_recursive", ["/www", 0o750, 0o755, 0o644])
    assert session.client.changes == {"/www": 0o750, "/www/css": 0o755, "/www/index.html": 0o644,
                                      "/www/css/style.css": 0o644}
    assert done and any("verrou.txt" in m for m in messages)
    assert "4" in messages[-1] and "1 erreur" in messages[-1] and "1 lien" in messages[-1]


def test_permissions_dialog_offers_content_modes_for_remote_folders():
    from smftp.ui.permissions import PermissionsDialog
    QApplication.instance() or QApplication([])
    file_dialog = PermissionsDialog(None, 0o644, False, "a.txt")
    assert file_dialog.recursive_box.isHidden() and file_dialog.recursive() is None
    dialog = PermissionsDialog(None, 0o750, True, "www", recursive=True)
    assert not dialog.recursive_box.isHidden() and not dialog.file_mode.isEnabled()
    dialog.recursive_box.setChecked(True)
    assert dialog.file_mode.isEnabled()
    dialog.file_mode.setText("640")
    assert dialog.recursive() == (0o755, 0o640) and dialog.mode() == 0o750


def test_context_menu_sends_a_recursive_chmod(monkeypatch):
    from smftp.ui import permissions
    window = new_window()
    try:
        fake_server(window)
        sent = []
        window.operation_requested.connect(lambda operation, arguments: sent.append((operation, arguments)))

        def accept(dialog):
            dialog.octal.setText("750")
            dialog.recursive_box.setChecked(True)
            return 1
        monkeypatch.setattr(permissions.PermissionsDialog, "exec", accept)
        item = next(window.remote.view.topLevelItem(i) for i in range(window.remote.view.topLevelItemCount())
                    if window.remote.view.topLevelItem(i).text(0) == "css")
        item.setSelected(True)
        window.file_action("chmod", window.remote)
        assert sent and sent[-1][0] == "chmod_recursive" and sent[-1][1][0] == "/www/css"
        assert sent[-1][1][2:] == [0o755, 0o644]
    finally:
        close_window(window)


# ----- fonts follow Windows -----
def test_theme_font_sizes_follow_the_system_font():
    from smftp.ui.main_window import scale_font_sizes
    assert scale_font_sizes("QWidget { font-size: 12px; }", 9.0) == "QWidget { font-size: 9.0pt; }"
    assert scale_font_sizes("QLabel { font-size: 24px; }", 12.0) == "QLabel { font-size: 24.0pt; }"
    app = QApplication.instance() or QApplication([])
    window = new_window()
    previous = app.font()
    try:
        font = app.font()
        font.setPointSizeF(9.0)
        app.setFont(font)
        window.apply_theme()
        normal = window.styleSheet()
        font.setPointSizeF(13.5)
        app.setFont(font)
        window.apply_theme()
        larger = window.styleSheet()
        assert "px;" not in larger.split("font-size")[1][:12]
        assert "font-size: 9.0pt" in normal and "font-size: 13.5pt" in larger
    finally:
        app.setFont(previous)
        close_window(window)


# ----- display -----
def test_local_dates_use_the_same_format_as_the_server_panel(tmp_path):
    from smftp.ui.explorer import Explorer
    QApplication.instance() or QApplication([])
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    panel = Explorer()
    panel.navigate(str(tmp_path))
    model = panel.model
    assert wait_until(lambda: model.rowCount(model.index(str(tmp_path))) == 1)
    index = model.index(str(tmp_path / "note.txt")).siblingAtColumn(3)
    expected = datetime.fromtimestamp((tmp_path / "note.txt").stat().st_mtime).strftime("%d/%m/%Y %H:%M")
    assert model.data(index) == expected


def test_narrow_panel_hides_permissions_then_type_and_restores_them():
    from smftp.ui.explorer import Explorer
    QApplication.instance() or QApplication([])
    panel = Explorer(remote=True)
    panel.resize(1100, 400)
    panel.show()
    QApplication.processEvents()
    panel.set_entries("/www", [Entry("index.html", False, 1234, 1_700_000_000, "-rw-r--r--"),
                               Entry("images", True, 0, 1_700_000_000, "drwxr-xr-x")])
    header = panel.view.header()
    header.resizeSection(2, 123)  # The user's own width.
    header.resizeSection(4, 95)
    seen = []
    for width in [*range(1100, 300, -20), *range(320, 1120, 20)]:
        panel.resize(width, 400)
        QApplication.processEvents()
        hidden = (header.isSectionHidden(4), header.isSectionHidden(2))
        seen.append(hidden)
        assert hidden != (False, True)  # Permissions go first, Type second.
        others = sum(header.sectionSize(c) for c in range(1, 5) if not header.isSectionHidden(c))
        assert panel.view.viewport().width() - others >= panel.NAME_ROOM or hidden == (True, True)
    assert (True, True) in seen and (True, False) in seen
    # Room again: both are back with the widths chosen by the user.
    assert seen[-1] == (False, False) and header.sectionSize(2) == 123 and header.sectionSize(4) == 95
    panel.close()


# ----- languages of stored or reported texts -----
def test_schedule_history_is_shown_in_the_interface_language(english, monkeypatch):
    from smftp.core import schedule as S
    from smftp.i18n import N_
    import smftp.utils.profiles as profiles
    from smftp.ui.scheduler import ScheduleDialog
    monkeypatch.setattr(profiles, "read_credential", lambda key: None)
    window = new_window()
    try:
        site = profiles.normalize_profile(dict(host="127.0.0.1", user="test", protocol="FTP", port=21, name="Local"))
        window.sites = [site]
        key = profiles.identity(site)
        item = window.scheduler.add_task(dict(name="Nuit", kind="sync", profile=key, local="C:/", remote="/",
                                              recurrence={"type": "interval", "minutes": 60}))
        window.scheduler.run_now(item["id"])
        row = window.scheduler.store.history[-1]
        # Stored in neutral form (French source text), shown in English.
        assert row["detail_source"].startswith("Mot de passe non mémorisé")
        assert S.detail_text(row).startswith("Password not remembered") and S.detail_french(row) == row["detail_source"]
        neutral = {"detail": "texte d’origine"}
        S.store_detail(neutral, S.Detail(N_("{copied} fichier(s) traité(s)."), copied=3))
        assert S.detail_text(neutral).startswith("3 ") and "fichier" in S.detail_french(neutral)
        window.scheduler.store.history.append({"name": "Ancienne", "status": S.SUCCESS, "started": "",
                                               "detail": "Ancienne ligne en français"})
        monkeypatch.setattr(QDialog, "exec", lambda dialog: 0)
        dialog = ScheduleDialog(window.scheduler, window)
        texts = [dialog.history.topLevelItem(i).text(3) for i in range(dialog.history.topLevelItemCount())]
        assert texts[0] == "Ancienne ligne en français"  # Old rows stay as they were.
        assert texts[1].startswith("Password not remembered")
    finally:
        close_window(window)
    # Saved and read back: the neutral form survives the file.
    store = S.ScheduleStore(window.scheduler.store.path)
    store.load()
    assert any(r.get("detail_source") for r in store.history)


def test_silent_model_report_is_translated_only_for_display(english):
    from smftp.ui.assistant import translate_report, REPORT_CREATED, REPORT_STARTED
    report = REPORT_CREATED.format(id="abc", next="aucune")
    assert report.startswith("Tâche créée")  # What the model receives stays French.
    shown = translate_report(report)
    assert shown.startswith("Task created") and "abc" in shown and "none" in shown
    assert translate_report("Erreur : Tâche introuvable.") == "Error: Task not found."
    assert translate_report(REPORT_STARTED.format(status="En cours", detail="Démarrage…")).startswith("Task started")


def test_new_windows_are_in_english(english, monkeypatch):
    from smftp.ui.permissions import PermissionsDialog
    monkeypatch.setattr(QDialog, "exec", lambda dialog: 0)
    window = new_window()
    try:
        window.update_bookmarks_menu()
        fake_server(window)
        window.update_bookmarks_menu()
        window.manage_bookmarks()
        PermissionsDialog(window, 0o755, True, "www", recursive=True).close()
        titles = [action.text() for action in window.menuBar().actions()]
        assert "&Bookmarks" in titles
    finally:
        close_window(window)
    assert not french_left(english), french_left(english)
