"""Window size and layout memory, keyboard navigation, menus, rename, first launch."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QInputDialog
from smftp.ui.main_window import MainWindow
from test_audit_known_issues import close_window


def new_window():
    QApplication.instance() or QApplication([])
    return MainWindow()


def wait_until(condition, timeout=3.0):
    import time
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return condition()


def test_window_fits_the_screen():
    window = new_window()
    try:
        available = window.screen().availableGeometry()
        assert window.width() <= max(window.minimumWidth(), available.width())
        assert window.height() <= max(window.minimumHeight(), available.height())
    finally:
        close_window(window)


def test_layout_and_view_options_are_remembered():
    window = new_window()
    try:
        window.show()  # Splitter sizes only exist once laid out.
        QApplication.processEvents()
        window.vertical_action.setChecked(True)
        window.remote_hidden_action.setChecked(True)
        window.workspace_splitter.setSizes([260, 1000])
        window.local.view.header().resizeSection(1, 150)
        window.save_layout()
        sizes = window.workspace_splitter.sizes()
    finally:
        close_window(window)
    window = new_window()
    try:
        window.show()
        QApplication.processEvents()
        assert window.vertical_action.isChecked() and window.remote_hidden_action.isChecked()
        assert window.panel_splitter.orientation() == Qt.Orientation.Vertical
        assert window.remote.hidden
        restored = window.workspace_splitter.sizes()
        assert restored[0] < restored[1] and abs(restored[0] / sum(restored) - sizes[0] / sum(sizes)) < 0.05
        assert window.local.view.header().sectionSize(1) == 150
        assert not window.icon_view_action.isChecked()
    finally:
        close_window(window)


def test_menus_open_with_alt_letters():
    window = new_window()
    try:
        titles = [action.text() for action in window.menuBar().actions()]
        assert titles == ["&Fichier", "É&dition", "&Affichage", "&Serveurs", "Si&gnets", "&Transferts", "&Outils", "A&ide"]
    finally:
        close_window(window)


# ---------- keyboard in the file panels ----------

def test_enter_opens_a_folder_and_backspace_goes_up(tmp_path):
    tmp_path = tmp_path / "zone"  # tmp_path also holds the test's application data.
    (tmp_path / "dossier").mkdir(parents=True)
    (tmp_path / "dossier" / "fichier.txt").write_text("x")
    window = new_window()
    try:
        window.show()
        panel = window.local
        panel.navigate(str(tmp_path))
        # The local model reads folders in the background.
        assert wait_until(lambda: panel.model.rowCount(panel.view.rootIndex()) == 1)
        index = panel.model.index(str(tmp_path / "dossier"))
        assert wait_until(lambda: panel.model.isDir(index))
        panel.view.setCurrentIndex(index)
        panel.open_current()
        assert panel.path == str((tmp_path / "dossier").resolve())
        panel.parent_folder()
        assert panel.path == str(tmp_path.resolve())
        panel.travel(-1)  # Alt+Left: back to the folder opened before going up.
        assert panel.path == str((tmp_path / "dossier").resolve())
    finally:
        close_window(window)


def test_explorer_keys_are_bound_to_the_file_list():
    from PySide6.QtGui import QKeySequence, QShortcut
    window = new_window()
    try:
        keys = {shortcut.key().toString() for shortcut in window.local.view.findChildren(QShortcut)}
        assert {"Return", "Enter", "Backspace"} <= keys
        panel_keys = {shortcut.key().toString(QKeySequence.SequenceFormat.PortableText)
                      for shortcut in window.local.findChildren(QShortcut)}
        assert {"Alt+Up", "Alt+Left", "Alt+Right"} <= panel_keys
    finally:
        close_window(window)


def test_breadcrumbs_are_skipped_by_tab(tmp_path):
    from PySide6.QtWidgets import QPushButton
    window = new_window()
    try:
        window.local.navigate(str(tmp_path))
        crumbs = [b for b in window.local.findChildren(QPushButton) if b.objectName() == "breadcrumb"]
        assert crumbs and all(b.focusPolicy() == Qt.FocusPolicy.NoFocus for b in crumbs)
    finally:
        close_window(window)


def test_remote_keys_do_nothing_when_disconnected():
    window = new_window()
    try:
        requested = []
        window.remote.requested.connect(requested.append)
        window.remote.parent_folder()
        window.remote.travel(-1)
        window.remote.open_current()
        assert requested == []
    finally:
        close_window(window)


def test_disconnecting_forgets_the_remote_history():
    from smftp.models.entries import Entry
    window = new_window()
    try:
        window.remote.set_entries("/www", [Entry("a.txt", False, 1, 1.0)])
        window.remote.set_entries("/www/img", [])
        window.on_disconnected()
        assert window.remote.history == [] and window.remote.position == -1
        assert not window.remote.navigation_buttons[0].isEnabled()
    finally:
        close_window(window)


# ---------- rename ----------

def test_rename_starts_from_the_current_name(tmp_path, monkeypatch):
    target = tmp_path / "rapport annuel.docx"
    target.write_text("x")
    seen = {}

    def fake_exec(dialog):
        from PySide6.QtWidgets import QLineEdit
        QApplication.processEvents()
        field = dialog.findChild(QLineEdit)
        seen["text"], seen["selected"] = dialog.textValue(), field.selectedText() if field else None
        dialog.setTextValue("rapport 2026.docx")
        return QInputDialog.DialogCode.Accepted
    monkeypatch.setattr(QInputDialog, "exec", fake_exec)
    window = new_window()
    started = []
    monkeypatch.setattr(window, "start_local", lambda operation, arguments: started.append((operation, arguments)))
    try:
        window.local.navigate(str(tmp_path))
        window.local.selected = lambda: [(str(target), False)]
        window.file_action("rename", window.local)
    finally:
        close_window(window)
    assert seen["text"] == "rapport annuel.docx"
    assert started == [("rename", [str(target), str(tmp_path / "rapport 2026.docx")])]


def test_an_invalid_new_name_is_explained(tmp_path, monkeypatch):
    target = tmp_path / "a.txt"
    target.write_text("x")

    def fake_exec(dialog):
        dialog.setTextValue("dossier/a.txt")
        return QInputDialog.DialogCode.Accepted
    monkeypatch.setattr(QInputDialog, "exec", fake_exec)
    window = new_window()
    errors = []
    monkeypatch.setattr(window, "on_error", errors.append)
    try:
        window.local.selected = lambda: [(str(target), False)]
        window.file_action("rename", window.local)
        assert errors and "Nom refusé" in str(errors[0])
    finally:
        close_window(window)


# ---------- first launch ----------

def test_first_launch_invites_to_add_or_import_a_server():
    window = new_window()
    try:
        window.sites = []
        window.refresh_sites()
        assert not window.welcome.isHidden() and window.site_list.isHidden()
        window.sites = [dict(host="h.test", user="u", protocol="SFTP", port=22, name="Prod")]
        window.refresh_sites()
        assert window.welcome.isHidden() and not window.site_list.isHidden()
    finally:
        close_window(window)
