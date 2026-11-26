"""Conflict question, queue columns, folder uploads, editor encodings."""
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from smftp.models.entries import Connection
from smftp.ui.transfers import duration_label
from smftp.workers.transfer import Transfer, TransferWorker
from test_clients import ftp_server  # noqa: F401  (fixture)


def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("seconds,label", [(0, "0 s"), (45.4, "45 s"), (185, "3 min 05 s"), (3725, "1 h 02 min")])
def test_remaining_time_is_readable(seconds, label):
    assert duration_label(seconds) == label


def test_conflict_question_shows_both_files(tmp_path, monkeypatch):
    from smftp.ui.transfers import Transfers
    from test_release_fixes import FakeClient
    app()
    (tmp_path / "page.html").write_bytes(b"local version")
    shown = {}

    def fake_exec(box):
        shown["text"], shown["info"] = box.text(), box.informativeText()
        shown["buttons"] = [button.text() for button in box.buttons()]
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    transfers = Transfers()
    transfers.pause_all()
    task = Transfer(Connection("h.test", "u", "SFTP"), str(tmp_path / "page.html"), "/r/page.html", False)
    record = transfers.add(task)
    worker = record["worker"]
    worker.conflict_found.connect(lambda name: transfers.resolve_conflict(record, name))
    worker.copy_file(FakeClient({"/r/page.html": b"server"}), tmp_path / "page.html", "/r/page.html")
    assert "page.html" in shown["text"]
    assert "sur le serveur" in shown["info"] and "sur ce PC" in shown["info"] and "13 o" in shown["info"]
    assert "Écraser si plus récent" in shown["buttons"] and "Écraser si la taille diffère" in shown["buttons"]
    assert (tmp_path / "page.html").read_bytes() == b"local version"  # No button clicked: skipped.


def test_queue_shows_destination_and_size(tmp_path):
    from smftp.ui.transfers import Transfers
    app()
    transfers = Transfers()
    transfers.pause_all()
    record = transfers.add(Transfer(Connection("ftp.exemple.fr", "alice"), str(tmp_path / "a.zip"), "/www/a.zip", True))
    assert record["item"].text(7) == "alice@ftp.exemple.fr:/www/a.zip"
    transfers.update_progress(record, 50, 2048, 10.0)
    assert record["item"].text(6) == "2.0 Ko"


def test_overwrite_is_a_default_rule_in_preferences():
    from PySide6.QtCore import QSettings
    from smftp.ui.settings import SettingsDialog
    app()
    dialog = SettingsDialog(QSettings())
    assert dialog.conflict.findData("overwrite") >= 0


def test_folder_upload_reads_each_remote_folder_once(ftp_server, tmp_path, monkeypatch):  # noqa: F811
    from smftp.core.clients import FTPClient
    port, root = ftp_server
    folder = tmp_path / "local" / "site"  # The test server's root is tmp_path itself.
    folder.mkdir(parents=True)
    for number in range(30):
        (folder / f"page{number}.html").write_text(str(number))
    (root / "site").mkdir()
    (root / "site" / "page3.html").write_text("ancien")
    listings = []
    original = FTPClient.listdir
    monkeypatch.setattr(FTPClient, "listdir", lambda self, path: listings.append(path) or original(self, path))
    worker = TransferWorker(Transfer(Connection("127.0.0.1", "test", "FTP", port, "secret"), str(folder), "/site", True,
                                     directory=True, conflict="skip"))
    results = []
    worker.result.connect(results.append)
    worker.run()
    assert results == ["Terminé"]
    assert (root / "site" / "page3.html").read_text() == "ancien"  # Existing file seen: skip rule applied.
    assert (root / "site" / "page29.html").read_text() == "29"
    # Before: the parent folder was listed again for every file (about 90 listings).
    assert listings.count("/site") < 35


# ---------- editor ----------

def test_editor_keeps_windows_1252_files(tmp_path):
    from smftp.ui.editor import Editor
    app()
    path = tmp_path / "ancien.html"
    path.write_bytes("<p>Été à Noël – 10 €</p>\r\n".encode("cp1252"))
    editor = Editor(str(path), "/www/ancien.html")
    assert editor.encoding == "cp1252" and "Été à Noël – 10 €" in editor.text.toPlainText()
    assert editor.windowTitle() == "/www/ancien.html"
    editor.text.insertPlainText("Ça ")
    assert editor.windowTitle() == "* /www/ancien.html"
    editor.save()
    assert path.read_bytes().decode("cp1252").startswith("Ça <p>Été")
    assert editor.windowTitle() == "/www/ancien.html"
    editor.close()


def test_editor_refuses_binary_files(tmp_path):
    from smftp.ui.editor import detect_encoding
    with pytest.raises(UnicodeError):
        detect_encoding(b"\x89PNG\r\n\x1a\n\x00\x00")
    assert detect_encoding("é".encode("utf-8")) == "utf-8"
    assert detect_encoding(b"\x81\x8d") == "latin-1"


def test_editor_offers_utf8_when_new_characters_do_not_fit(tmp_path, monkeypatch):
    from smftp.ui.editor import Editor
    app()
    path = tmp_path / "vieux.txt"
    path.write_bytes("café".encode("cp1252"))
    editor = Editor(str(path), "/vieux.txt")
    editor.text.insertPlainText("😀 ")
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    editor.save()
    assert path.read_bytes().decode("utf-8") == "😀 café" and editor.encoding == "utf-8"
    editor.close()
