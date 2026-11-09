from pathlib import Path
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog
from smftp.models.entries import Connection, Entry
from smftp.ui.main_window import MainWindow
from smftp.ui.connection_dialog import ConnectionDialog
from smftp.ui.explorer import Explorer
from smftp.workers.transfer import Transfer, TransferWorker
from smftp.utils.queue_store import write_queue, read_queue
from smftp.core.listing import parse_list_line
from smftp.core.clients import FTPClient
from smftp.workers.search import SearchWorker
from test_clients import ftp_server
import pytest


def finish_window(window):
    window.close()
    loop = QEventLoop()
    window.thread.finished.connect(loop.quit)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()


def test_saved_profile_connects_without_dialog(monkeypatch):
    import keyring
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    started = []
    monkeypatch.setattr(keyring, "get_password", lambda *args: "stored-secret")
    monkeypatch.setattr(window, "new_connection", lambda *args: pytest.fail("Connection form must stay closed"))
    monkeypatch.setattr(window, "start_connection", lambda: started.append(window.connection))
    try:
        window.connect_profile(dict(host="test.local", user="alice", protocol="SFTP", port=22,
                                    name="Production", group="Web", remote_directory="/www"))
        assert started[0].password == "stored-secret"
        assert started[0].remote_directory == "/www"
        titles = [a.text() for a in window.menuBar().actions()]
        assert titles == ["Fichier", "Édition", "Affichage", "Serveurs", "Transferts", "Outils", "Aide"]
    finally:
        finish_window(window)


def test_key_picker_and_profile_paths(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "id_ed25519"
    path.write_text("test-key")
    dialog = ConnectionDialog()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    dialog.key_picker.click()
    assert dialog.key.text() == str(path)
    dialog.group.setText("Production")
    dialog.local_directory.setText(str(tmp_path))
    dialog.remote_directory.setText("/www")
    assert dialog.connection().group == "Production"
    dialog.protocol.setCurrentText("FTP")
    assert not dialog.key_picker.isEnabled()


def test_close_can_be_cancelled(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    questions = []
    def reject(*args):
        questions.append(args)
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", reject)
    assert not window.close()
    assert window.isVisible()
    assert window.thread.isRunning()
    assert questions[0][-1] == QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    finish_window(window)


def test_queue_persistence_excludes_password_and_restores_paused(tmp_path):
    app = QApplication.instance() or QApplication([])
    task = Transfer(Connection("test.local", "alice", password="not-on-disk"), str(tmp_path / "source"), "/file", True)
    write_queue([task])
    from smftp.utils.storage import data_dir
    assert "not-on-disk" not in (data_dir() / "queue.json").read_text()
    restored = read_queue()
    assert restored[0].connection.password == ""
    assert restored[0].resume
    window = MainWindow()
    try:
        window.transfers.restore()
        assert window.transfers.records[0]["state"] == "paused_pending"
        assert not window.transfers.active()
        window.transfers.authorize(task.connection)
        assert not window.transfers.records[0]["restored"]
        window.transfers.pause_all()
    finally:
        finish_window(window)


def test_legacy_list_parsing():
    entry = parse_list_line("-rw-r--r-- 1 user group 123 Jan 02 2025 my file.txt")
    assert entry.name == "my file.txt" and entry.size == 123
    assert parse_list_line("drwxr-xr-x 2 user group 4096 Nov 09 12:30 assets").is_dir
    assert parse_list_line("11-09-26  02:30PM       <DIR>          Images").is_dir
    assert parse_list_line("11-09-26  02:30PM       1234           file name.zip").size == 1234
    with pytest.raises(ValueError):
        parse_list_line("unrecognized listing")


def test_ftp_list_fallback(monkeypatch):
    import ftplib
    class Legacy:
        def mlsd(self, path):
            raise ftplib.error_perm("502 MLSD unavailable")
        def retrlines(self, command, callback):
            callback("-rw-r--r-- 1 user group 123 Jan 02 2025 file.txt")
    client = FTPClient()
    client.ftp = Legacy()
    assert client.listdir("/")[0].name == "file.txt"


def test_atomic_upload_keeps_original_on_failed_rename(ftp_server, tmp_path, monkeypatch):
    port, root = ftp_server
    source = tmp_path / "new.txt"
    source.write_text("new data")
    target = root / "target.txt"
    target.write_text("old data")
    def reject(*args):
        raise PermissionError("RNTO denied")
    monkeypatch.setattr(FTPClient, "replace", reject)
    task = Transfer(Connection("127.0.0.1", "test", "FTP", port, "secret"), str(source), "/target.txt", True, conflict="overwrite")
    worker = TransferWorker(task)
    results = []
    worker.result.connect(results.append)
    worker.run()
    assert results == ["Échoué"]
    assert target.read_text() == "old data"
    assert list(root.glob(".target.txt.smftp-*.part"))


def test_atomic_upload_replaces_after_full_upload(ftp_server, tmp_path):
    port, root = ftp_server
    source = tmp_path / "new.txt"
    source.write_text("new data")
    task = Transfer(Connection("127.0.0.1", "test", "FTP", port, "secret"), str(source), "/target.txt", True, conflict="overwrite")
    worker = TransferWorker(task)
    worker.run()
    assert (root / "target.txt").read_text() == "new data"
    assert not list(root.glob(".target.txt.smftp-*.part"))


def test_recursive_search_and_icon_filter(tmp_path):
    app = QApplication.instance() or QApplication([])
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "code.py").write_text("test")
    matches = []
    worker = SearchWorker(str(tmp_path), "*.py")
    worker.found.connect(lambda path, entry: matches.append(path))
    worker.run()
    assert matches == [str(tmp_path / "nested" / "code.py")]
    panel = Explorer(remote=True)
    panel.set_entries("/", [Entry("code.py", False), Entry(".hidden", False)])
    panel.set_icon_mode(True)
    assert panel.stack.currentWidget() is panel.icons
    assert panel.icons.item(1).isHidden()
    panel.show_hidden(True)
    assert not panel.icons.item(1).isHidden()


def test_reconnect_only_for_transient_errors():
    app = QApplication.instance() or QApplication([])
    from smftp.core.errors import describe_error
    import ftplib
    window = MainWindow()
    window.settings.setValue("auto_reconnect", True)
    try:
        window.on_session_error(describe_error(ftplib.error_perm("530 denied")))
        assert not window.retry_timer.isActive()
        window.on_session_error(describe_error(ConnectionRefusedError(10061, "refused")))
        assert window.retry_timer.isActive()
        assert window.retry_count == 1
    finally:
        finish_window(window)


def test_select_all_in_icon_view_updates_file_selection():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.remote.set_entries("/www", [Entry("one.txt", False), Entry("two.txt", False)])
        window.remote.set_icon_mode(True)
        window.last_panel = window.remote
        window.select_all_files()
        assert len(window.remote.icons.selectedItems()) == 2
        assert len(window.remote.selected()) == 2
    finally:
        finish_window(window)


def test_comparison_policy_skips_same_size(ftp_server, tmp_path):
    port, root = ftp_server
    source = tmp_path / "source.bin"
    source.write_bytes(b"changed!")
    (root / "target.bin").write_bytes(b"original")
    task = Transfer(Connection("127.0.0.1", "test", "FTP", port, "secret"), str(source), "/target.bin", True, conflict="different")
    worker = TransferWorker(task)
    worker.run()
    assert (root / "target.bin").read_bytes() == b"original"
    assert not list(root.glob(".target.bin*.part"))


def test_source_change_during_upload_never_publishes(ftp_server, tmp_path, monkeypatch):
    port, root = ftp_server
    source = tmp_path / "source.bin"
    source.write_bytes(b"initial")
    (root / "target.bin").write_bytes(b"original")
    upload = FTPClient.upload
    def changing_upload(client, local, remote, progress, offset=0):
        upload(client, local, remote, progress, offset)
        Path(local).write_bytes(b"changed-content")
    monkeypatch.setattr(FTPClient, "upload", changing_upload)
    task = Transfer(Connection("127.0.0.1", "test", "FTP", port, "secret"), str(source), "/target.bin", True, conflict="overwrite")
    worker = TransferWorker(task)
    errors = []
    worker.failed.connect(errors.append)
    worker.run()
    assert "changé" in errors[0].details
    assert (root / "target.bin").read_bytes() == b"original"


@pytest.mark.parametrize("name", ["..", "../escape", "C:\\escape", "file/sub", "file\\sub"])
def test_remote_names_cannot_escape_local_destination(tmp_path, name):
    from smftp.utils.paths import local_child
    with pytest.raises(ValueError):
        local_child(tmp_path, name)
