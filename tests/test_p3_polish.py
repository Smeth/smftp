"""Finishing touches: breadcrumbs, permissions labels, editor tools, preview, search reveal."""
from pathlib import Path
from PySide6.QtWidgets import QApplication
from smftp.models.entries import Entry
from smftp.ui.explorer import breadcrumb_parts


def app():
    return QApplication.instance() or QApplication([])


def test_windows_breadcrumb_keeps_drive_root():
    crumbs = breadcrumb_parts(r"C:\Users\me", False)
    assert crumbs == [("C:\\", "C:\\"), ("Users", r"C:\Users"), ("me", r"C:\Users\me")]
    assert breadcrumb_parts("C:\\", False) == [("C:\\", "C:\\")]


def test_unc_and_remote_breadcrumbs():
    unc = "\\\\nas\\share\\docs"
    root = "\\\\nas\\share\\"
    assert breadcrumb_parts(unc, False)[0] == (root, root)
    assert breadcrumb_parts(unc, False)[-1] == ("docs", unc)
    assert breadcrumb_parts("/var/www", True) == [("/", "/"), ("var", "/var"), ("www", "/var/www")]


def test_permissions_dialog_names_folders_and_boxes():
    app()
    from smftp.ui.permissions import PermissionsDialog
    dialog = PermissionsDialog(None, 0o755, directory=True, name="site")
    assert dialog.windowTitle() == "Permissions du dossier — site"
    assert dialog.boxes[2].accessibleName() == "Propriétaire — Traverser"
    assert dialog.mode() == 0o755


def make_editor(tmp_path, raw):
    app()
    from smftp.ui.editor import Editor
    path = tmp_path / "f.txt"
    path.write_bytes(raw)
    return Editor(str(path), "t"), path


def test_editor_preserves_crlf_and_bom(tmp_path):
    editor, path = make_editor(tmp_path, b"\xef\xbb\xbfa\r\nb\r\n")
    try:
        assert "BOM" in editor.status.text() and "CRLF" in editor.status.text()
        editor.text.moveCursor(editor.text.textCursor().MoveOperation.End)
        editor.text.insertPlainText("c")
        editor.save()
        assert path.read_bytes() == b"\xef\xbb\xbfa\r\nb\r\nc"
    finally:
        editor.dirty = False
        editor.close()


def test_editor_replace_all_is_one_undo_step(tmp_path):
    editor, path = make_editor(tmp_path, b"foo bar foo\nFoo\n")
    try:
        editor.show_find(True)
        editor.find_field.setText("foo")
        editor.replace_field.setText("baz")
        assert editor.replace_all() == 3  # Case-insensitive by default.
        assert editor.text.toPlainText() == "baz bar baz\nbaz\n"
        editor.text.undo()
        assert editor.text.toPlainText() == "foo bar foo\nFoo\n"
        editor.case.setChecked(True)
        assert editor.replace_all() == 2
    finally:
        editor.dirty = False
        editor.close()


def test_escape_in_find_bar_only_closes_the_bar(tmp_path):
    editor, _ = make_editor(tmp_path, b"text")
    try:
        editor.show()
        editor.show_find(False)
        editor.find_field.setFocus()
        QApplication.processEvents()
        editor.reject()
        assert editor.isVisible() and not editor.find_bar.isVisible()
    finally:
        editor.close()


def test_image_preview_reports_unreadable_file(tmp_path):
    app()
    from smftp.ui.editor import ImagePreview
    path = tmp_path / "broken.png"
    path.write_bytes(b"not an image")
    preview = ImagePreview(str(path))
    assert "Impossible d’afficher" in preview.label.text()
    assert not any(button.isEnabled() for button in preview.controls)


def test_image_preview_zoom(tmp_path):
    app()
    from PySide6.QtGui import QImage, QColor
    from smftp.ui.editor import ImagePreview
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    path = tmp_path / "ok.png"
    image.save(str(path))
    preview = ImagePreview(str(path))
    assert "200 × 100 px" in preview.info.text()
    preview.set_zoom(2.0)
    assert preview.label.pixmap().width() == 400


def test_remote_reveal_selects_result_after_listing():
    app()
    from smftp.ui.explorer import Explorer
    panel = Explorer(remote=True)
    requested = []
    panel.requested.connect(requested.append)
    panel.reveal("/www", "index.php")
    assert requested == ["/www"]
    panel.set_entries("/www", [Entry("a.txt", False), Entry("index.php", False)])
    assert [item.text(0) for item in panel.view.selectedItems()] == ["index.php"]
    assert panel.pending_select is None


def test_local_reveal_selects_file(tmp_path):
    app()
    from smftp.ui.explorer import Explorer
    (tmp_path / "target.txt").write_text("x")
    panel = Explorer(remote=False)
    panel.reveal(str(tmp_path), "target.txt")
    for _ in range(50):
        QApplication.processEvents()
        if panel.pending_select is None:
            break
    assert Path(panel.model.filePath(panel.view.currentIndex())).name == "target.txt"


def test_search_reports_unreadable_folders_and_limit(tmp_path):
    from smftp.workers.search import SearchWorker
    (tmp_path / "a.txt").write_text("x")
    worker = SearchWorker(str(tmp_path / "missing"), "*", None)
    worker.run()
    assert worker.unreadable == 1 and not worker.limit_reached


def test_diagnostic_and_about_dialogs_open(monkeypatch):
    app()
    from PySide6.QtWidgets import QDialog, QMessageBox
    from smftp.ui.main_window import MainWindow
    from test_audit_known_issues import close_window
    texts = []
    monkeypatch.setattr(QDialog, "exec", lambda dialog: texts.append(dialog.windowTitle()) or 0)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: texts.append(box.text()) or 0)
    window = MainWindow()
    try:
        window.show_diagnostic()
        window.show_about()
        assert texts[0] == "Diagnostic"
        assert "github.com/Smeth/smftp" in texts[1] and "PolyForm Noncommercial 1.0.0" in texts[1]
    finally:
        close_window(window)


def test_local_folder_button_opens_chosen_folder(tmp_path, monkeypatch):
    app()
    from PySide6.QtWidgets import QFileDialog
    from smftp.ui.explorer import Explorer
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    asked = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda parent, title, start: asked.append(start) or str(chosen))
    panel = Explorer(remote=False)
    start = panel.path
    panel.browse_button.click()
    assert asked == [start]
    assert panel.path == str(chosen.resolve())
    panel.travel(-1)
    assert panel.path == start


def test_local_folder_button_cancel_keeps_folder(monkeypatch):
    app()
    from PySide6.QtWidgets import QFileDialog
    from smftp.ui.explorer import Explorer
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: "")
    panel = Explorer(remote=False)
    start = panel.path
    panel.browse_button.click()
    assert panel.path == start


def test_remote_panel_has_no_local_folder_button():
    app()
    from smftp.ui.explorer import Explorer
    assert not hasattr(Explorer(remote=True), "browse_button")


def test_preferences_folder_buttons_fill_fields(tmp_path, monkeypatch):
    app()
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QFileDialog
    from smftp.ui.settings import SettingsDialog
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    settings.setValue("local_directory", str(tmp_path))
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    asked = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda parent, title, start: asked.append(start) or chosen.as_posix())
    dialog = SettingsDialog(settings)
    dialog.local_picker.click()
    assert asked == [str(tmp_path)]
    assert dialog.local.text() == str(chosen)
    dialog.temp_picker.click()
    assert dialog.temp.text() == str(chosen)
    dialog.persist(settings)
    assert settings.value("local_directory") == str(chosen)


def test_preferences_folder_button_cancel_keeps_value(tmp_path, monkeypatch):
    app()
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QFileDialog
    from smftp.ui.settings import SettingsDialog
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    settings.setValue("local_directory", r"D:\Projets")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: "")
    dialog = SettingsDialog(settings)
    dialog.local_picker.click()
    assert dialog.local.text() == r"D:\Projets"
