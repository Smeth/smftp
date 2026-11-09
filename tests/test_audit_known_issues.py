"""Executable audit findings, deliberately xfailed until the defects are fixed.

Assertions describe the required behavior, not the currently faulty behavior.
strict=True makes a future correction visible as XPASS until this file is updated.
"""
from pathlib import Path
import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from smftp.models.entries import Connection, Entry
from smftp.ui.editor import Editor
from smftp.ui.main_window import MainWindow
from smftp.ui.permissions import PermissionsDialog
from smftp.workers.transfer import Transfer, TransferWorker
from test_clients import ftp_server


def close_window(window):
    window.online = False
    window.close()
    loop = QEventLoop()
    window.thread.finished.connect(loop.quit)
    QTimer.singleShot(2500, loop.quit)
    loop.exec()


@pytest.mark.xfail(strict=True, reason="AUD-001: QDialog.reject bypasses Editor.closeEvent")
def test_editor_escape_must_keep_unsaved_document_visible(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "document.txt"
    path.write_text("original", encoding="utf-8")
    editor = Editor(str(path), "Audit")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    try:
        editor.show()
        editor.text.setPlainText("unsaved change")
        editor.reject()  # QDialog's Escape action.
        assert editor.isVisible()
    finally:
        editor.dirty = False
        editor.close()


@pytest.mark.xfail(strict=True, reason="AUD-002: syntax highlighting marks an untouched editor dirty")
def test_highlighting_must_not_mark_document_modified(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "example.py"
    path.write_text("print('hello')\n", encoding="utf-8")
    editor = Editor(str(path), "Audit")
    try:
        editor.show()
        for _ in range(3):
            app.processEvents()
        assert not editor.dirty
    finally:
        editor.dirty = False
        editor.close()


@pytest.mark.xfail(strict=True, reason="AUD-003: a second save clears the first pending inspection")
def test_repeated_save_must_preserve_pending_upload(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)
    connection = Connection("audit.invalid", "audit", password="dummy-only")
    window.connection = connection
    window.online = True
    record = dict(remote="/file.txt", local=str(tmp_path / "file.txt"), connection=connection)
    try:
        window.save_remote(record)
        window.save_remote(record)
        assert window.pending_saves.get("/file.txt") is record
    finally:
        close_window(window)


@pytest.mark.xfail(strict=True, reason="AUD-004: download resume accepts stale content of the same size")
def test_resume_must_not_report_success_with_wrong_download_content(ftp_server, tmp_path):
    port, root = ftp_server
    (root / "remote.bin").write_bytes(b"new-data")
    local = tmp_path / "partial.bin"
    local.write_bytes(b"old-data")
    worker = TransferWorker(Transfer(Connection("127.0.0.1", "test", "FTP", port, "secret"),
                                    str(local), "/remote.bin", False, resume=True))
    worker.run()
    assert local.read_bytes() == b"new-data"


@pytest.mark.xfail(strict=True, reason="AUD-005: clearing finished jobs also erases failed jobs")
def test_clear_finished_must_retain_failed_jobs(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.transfers.pause_all()
        task = Transfer(Connection("audit.invalid", "audit"), str(tmp_path / "file"), "/file", True)
        window.transfers.add(task)
        record = window.transfers.records[0]
        window.transfers.done(record, "Échoué")
        window.transfers.clear_finished()
        assert window.transfers.tables[1].topLevelItemCount() == 1
    finally:
        close_window(window)


@pytest.mark.xfail(strict=True, reason="AUD-006: directory progress is reset for each child file")
def test_directory_progress_must_measure_total_bytes(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "one.txt").write_bytes(b"1234")
    (folder / "two.txt").write_bytes(b"5678")

    class MemoryClient:
        entries = {}
        def exists(self, path): return self.entries.get(path)
        def mkdir(self, path): self.entries[path] = Entry(Path(path).name, True)
        def makedirs(self, path): pass
        def upload(self, local, remote, callback, offset=0):
            size = Path(local).stat().st_size
            self.entries[remote] = Entry(Path(remote).name, False, size)
            callback(size, size)
        def replace(self, source, target): self.entries[target] = self.entries.pop(source)

    worker = TransferWorker(Transfer(Connection("audit.invalid", "audit"), str(folder), "/folder", True, True))
    progress = []
    worker.updated.connect(lambda done, total, speed: progress.append((done, total)))
    worker.copy(MemoryClient(), folder, "/folder", True)
    assert progress[-1] == (8, 8)


@pytest.mark.xfail(strict=True, reason="AUD-007: chmod dialog starts at 644 instead of the selected mode")
def test_permissions_dialog_must_start_with_selected_directory_mode(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    values = []
    monkeypatch.setattr(PermissionsDialog, "exec", lambda dialog: values.append(dialog.mode()) or 0)
    try:
        window.remote.set_entries("/", [Entry("folder", True, permissions="drwxr-xr-x")])
        window.remote.view.topLevelItem(0).setSelected(True)
        window.online = True
        window.file_action("chmod", window.remote)
        assert values == [0o755]
    finally:
        close_window(window)


@pytest.mark.xfail(strict=True, reason="AUD-008: profile says remembered even when the vault write fails")
def test_failed_vault_save_must_not_claim_secret_is_remembered(monkeypatch):
    app = QApplication.instance() or QApplication([])
    import smftp.ui.main_window as module
    window = MainWindow()
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)
    def unavailable(connection):
        raise RuntimeError("audit vault unavailable")
    monkeypatch.setattr(module, "store_credential", unavailable)
    try:
        window.persist_profile(Connection("audit.invalid", "audit", password="dummy-only", remember=True))
        assert window.sites[0]["remember"] is False
    finally:
        close_window(window)
