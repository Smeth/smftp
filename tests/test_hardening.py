"""Low-severity security and robustness fixes."""
import os
import time
from pathlib import Path
import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox
from smftp.models.entries import Connection, Entry
from smftp.workers.transfer import Transfer, TransferWorker
from test_assistant import window  # noqa: F401  (fixture)


def app():
    return QApplication.instance() or QApplication([])


# ---------- assistant links ----------

@pytest.mark.parametrize("url,opened", [
    ("https://docs.anthropic.com/", True),
    ("http://exemple.fr/", False),
    ("file:///C:/Windows/System32/calc.exe", False),
    ("ms-msdt:/id PCWDiagnostic", False),
    ("search-ms:query=x", False),
    ("\\\\attaquant\\partage", False),
])
def test_assistant_links_open_only_https(monkeypatch, url, opened):
    from PySide6.QtGui import QDesktopServices
    from smftp.ui.assistant import open_web_link
    app()
    calls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda link: calls.append(link) or True)
    assert open_web_link(url) is opened
    assert len(calls) == int(opened)


# ---------- terminal paste ----------

def test_paste_cannot_leave_bracketed_paste_mode(monkeypatch):
    from smftp.ui.terminal import TerminalView, BRACKETED_PASTE
    app()
    view = TerminalView()
    view.screen.mode.add(BRACKETED_PASTE)
    sent = []
    view.typed.connect(sent.append)
    QGuiApplication.clipboard().setText("ls\x1b[201~rm -rf ~")
    view.paste()
    assert sent == [b"\x1b[200~ls[201~rm -rf ~\x1b[201~"]


def test_multiline_paste_is_shown_before_sending(monkeypatch):
    from smftp.ui.terminal import TerminalView
    app()
    view = TerminalView()
    sent, asked = [], []
    view.typed.connect(sent.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: asked.append(args[2]) or QMessageBox.StandardButton.No)
    QGuiApplication.clipboard().setText("cd /tmp\nrm -rf *\n")
    view.paste()
    assert sent == [] and "rm -rf *" in asked[0]
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    view.paste()
    assert sent == [b"cd /tmp\rrm -rf *\r"]
    QGuiApplication.clipboard().setText("une seule ligne")
    view.paste()
    assert sent[-1] == b"une seule ligne"


# ---------- remote Ollama ----------

def test_remote_ollama_needs_consent_once_per_address(window, monkeypatch):  # noqa: F811
    panel = window.assistant_panel
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: asked.append(args[2]) or QMessageBox.StandardButton.No)
    assert not panel.remote_ollama_consent("http://192.168.1.20:11434")
    assert "EN CLAIR" in asked[0]
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: asked.append(args[2]) or QMessageBox.StandardButton.Yes)
    assert panel.remote_ollama_consent("http://192.168.1.20:11434")
    count = len(asked)
    assert panel.remote_ollama_consent("http://192.168.1.20:11434") and len(asked) == count
    assert panel.remote_ollama_consent("https://ia.exemple.fr") and len(asked) == count + 1


# ---------- scheduled task names ----------

def test_task_names_are_one_short_line():
    from smftp.core.schedule import normalize_task
    task = normalize_task(dict(name="Sauvegarde\n\nServeur : banque.fr\x1b[31m" + "x" * 200, kind="sync", profile="p",
                               local="C:\\Site", remote="/www", recurrence={"type": "interval", "minutes": 60}))
    assert "\n" not in task["name"] and "\x1b" not in task["name"] and len(task["name"]) <= 80
    assert task["name"].startswith("Sauvegarde Serveur : banque.fr")


# ---------- transfer queue ----------

def test_many_additions_save_the_queue_once(monkeypatch, tmp_path):
    import smftp.utils.queue_store as store
    from smftp.ui.transfers import Transfers
    app()
    writes = []
    monkeypatch.setattr(store, "write_queue", lambda tasks: writes.append(len(tasks)))
    transfers = Transfers()
    transfers.pause_all()
    writes.clear()
    with transfers.batch():
        for number in range(300):
            transfers.add(Transfer(Connection("h.test", "u"), str(tmp_path / f"f{number}"), f"/f{number}", True))
    assert writes == [300]
    assert transfers.add(Transfer(Connection("h.test", "u"), str(tmp_path / "f7"), "/f7", True)) is None


def test_a_long_saved_queue_is_restored():
    from smftp.utils.queue_store import read_queue, write_queue
    tasks = [Transfer(Connection("h.test", "u"), f"C:\\d\\f{n}", f"/f{n}", True) for n in range(6000)]
    write_queue(tasks)
    assert len(read_queue()) == 6000


# ---------- cancellation, deletion, replacement ----------

def test_cancel_cuts_a_blocked_server_call():
    class Blocked:
        aborted = False

        def abort(self):
            Blocked.aborted = True
    worker = TransferWorker(Transfer(Connection("h", "u"), "a", "/a", False))
    worker.client = Blocked()
    worker.cancel()
    assert Blocked.aborted


def test_recursive_delete_removes_links_without_following_them():
    from smftp.core.clients import BaseClient

    class Fake(BaseClient):
        def __init__(self):
            super().__init__()
            self.calls = []

        def connect(self, connection):
            pass

        def close(self):
            pass

        def listdir(self, path):
            return {"/site": [Entry("current", True, 0, 0, "lrwxrwxrwx"), Entry("a.txt", False, 1, 0)]}.get(path, [])

        def unlink(self, path):
            self.calls.append(("unlink", path))

        def rmdir(self, path):
            self.calls.append(("rmdir", path))
    client = Fake()
    client.remove("/site", True)
    assert client.calls == [("unlink", "/site/current"), ("unlink", "/site/a.txt"), ("rmdir", "/site")]


def test_refused_replacement_removes_the_partial_file(tmp_path):
    from test_release_fixes import FakeClient
    source = tmp_path / "page.html"
    source.write_bytes(b"new")

    class Refusing(FakeClient):
        def replace(self, source, target):
            raise PermissionError("550 RNTO refused")

        def unlink(self, path):
            self.files.pop(path)
    remote = Refusing({"/r/page.html": b"old"})
    worker = TransferWorker(Transfer(Connection("h", "u", "SFTP"), str(source), "/r/page.html", True, conflict="overwrite"))
    with pytest.raises(PermissionError):
        worker.copy_file(remote, source, "/r/page.html")
    assert remote.files == {"/r/page.html": b"old"}


# ---------- messages and leftovers ----------

def test_a_very_short_password_does_not_blank_messages():
    from smftp.core.errors import redact
    assert redact("Connexion à ftp.exemple.fr refusée", ("p",)) == "Connexion à ftp.exemple.fr refusée"
    assert redact("mot de passe : p", ("p",)).endswith("[masqué]")
    assert "secret-long" not in redact("erreur secret-long ici", ("secret-long",))


def test_stale_temporary_folders_are_cleaned_but_not_user_folders(tmp_path):
    from smftp.utils.storage import clean_stale_temporary
    old = time.time() - 3 * 86400
    names = ["smftp-ab12cd_3", "smftp-copy-zz99yy88", "smftp-sauvegarde", "smftp-recent1"]
    for name in names:
        (tmp_path / name).mkdir()
        (tmp_path / name / "f.txt").write_text("x")
        os.utime(tmp_path / name, (old, old))
    (tmp_path / "smftp-new12345").mkdir()
    assert clean_stale_temporary([tmp_path]) == 2
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["smftp-new12345", "smftp-recent1", "smftp-sauvegarde"]
