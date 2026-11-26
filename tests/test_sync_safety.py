"""Mirror deletions never remove what may still exist; tolerant LIST and MLSD parsing."""
import os
import pytest
from smftp.core.clients import FTPClient
from smftp.core.listing import parse_list_line, parse_listing
from smftp.models.entries import Connection, Entry
from smftp.workers.sync import SyncPlanner, DELETE_FILE, DELETE_DIR
from smftp.workers.transfer import Transfer, TransferWorker
from test_integrity import hash_server, plan_for, connection  # noqa: F401  (fixture)


# ---------- LIST lines ----------

@pytest.mark.parametrize("line,name,is_dir,size", [
    ("-rw-r--r-- 1 user group 123 Jan 02 2025 my file.txt", "my file.txt", False, 123),
    ("-rw-r--r-- 1 ftp 2048 Mar 15 10:30 sans-groupe.txt", "sans-groupe.txt", False, 2048),  # NAS: no group
    ("-rw-r--r-- 1 2048 Mar 15 10:30 ni-proprietaire.txt", "ni-proprietaire.txt", False, 2048),
    ("drwxr-xr-x 2 user group 4096 janv. 09 2025 Dossier été", "Dossier été", True, 0),
    ("-rw-r--r-- 1 user group 10 9 déc. 12:30 facture.pdf", "facture.pdf", False, 10),
    ("-rw-r--r-- 1 user group 77 2025-03-01 08:15 iso.log", "iso.log", False, 77),
    ("lrwxrwxrwx 1 user group 11 Feb 01 2024 current -> releases/12", "current", False, 0),
    ("crw-rw-rw- 1 root root 1, 3 Jan 01 2024 null", "null", False, 0),
    ("srwxr-xr-x 1 user group 0 Jan 01 2024 agent.sock", "agent.sock", False, 0),
    ("prw-r--r-- 1 user group 0 Jan 01 2024 fifo", "fifo", False, 0),
    ("11-09-26  02:30PM       <DIR>          Images", "Images", True, 0),
    ("11-09-26  14:30       1234           iis 24h.zip", "iis 24h.zip", False, 1234),
])
def test_list_lines_are_understood(line, name, is_dir, size):
    entry = parse_list_line(line)
    assert (entry.name, entry.is_dir, entry.size) == (name, is_dir, size)
    assert entry.modified > 0


def test_one_strange_line_no_longer_hides_the_whole_folder():
    entries, skipped = parse_listing([
        "total 8",
        "-rw-r--r-- 1 user group 5 Jan 02 2025 a.txt",
        "ligne totalement inconnue",
        "-rw-r--r-- 1 user group 5 Feb 30 2025 date-impossible.txt",
        "drwxr-xr-x 2 user group 4096 Jan 02 2025 b",
    ])
    assert [e.name for e in entries] == ["a.txt", "b"] and skipped == 2


def test_an_unknown_format_still_fails():
    with pytest.raises(ValueError):
        parse_listing(["format inconnu", "toujours inconnu"])


def stub_client(lines, mlsd=True):
    import ftplib
    client = FTPClient()
    client.reports = []
    client.reporter = client.reports.append

    def read_lines(command):
        if command.startswith("MLSD") and not mlsd:
            raise ftplib.error_perm("500 MLSD not understood")
        return list(lines)
    client.read_lines = read_lines
    return client


def test_unreadable_list_lines_are_reported_and_mark_the_listing_incomplete():
    client = stub_client(["-rw-r--r-- 1 u g 5 Jan 02 2025 a.txt", "???"], mlsd=False)
    assert [e.name for e in client.listdir("/")] == ["a.txt"]
    assert client.incomplete_listing and any("1 ligne" in r for r in client.reports)


def test_mlsd_links_and_malformed_facts():
    client = stub_client([
        "type=file;size=12;modify=20250102030405; ok.txt",
        "type=file;size=abc;modify=pas-une-date; abime.txt",
        "type=OS.unix=slink:/var/www/releases/12;modify=20250102030405; current",
        "type=dir;modify=20250102030405; dossier",
    ])
    entries = {e.name: e for e in client.listdir("/")}
    assert entries["ok.txt"].size == 12 and entries["ok.txt"].modified > 0
    assert entries["abime.txt"].size == 0 and entries["abime.txt"].modified == 0
    assert entries["current"].permissions.startswith("l") and not entries["current"].is_dir
    assert entries["dossier"].is_dir


# ---------- mirror deletions ----------

def deletions(source, destination, source_dirs=(), destination_dirs=(), links=(), hides_dotfiles=False):
    planner = SyncPlanner(Connection("h", "u"), ".", "/", False)
    notes = []
    planner.note.connect(notes.append)
    rows = planner.mirror_deletions({p: (1, 1.0) for p in source}, {p: (1, 1.0) for p in destination},
                                    set(source_dirs), set(destination_dirs), set(links), hides_dotfiles)
    return {row[0]: row[1] for row in rows}, notes


def test_mirror_keeps_names_that_differ_only_by_case_or_unicode_form():
    rows, notes = deletions(["Photo.JPG", "été.txt"], ["photo.jpg", "été.txt", "vraiment-en-trop.txt"])
    assert rows == {"vraiment-en-trop.txt": DELETE_FILE}
    assert notes and "conservé" in notes[0]


def test_mirror_keeps_everything_at_or_under_a_link():
    rows, _ = deletions([], ["current/index.html", "current", "autre.txt"], destination_dirs=["current"],
                        links=["current"])
    assert rows == {"autre.txt": DELETE_FILE}


def test_mirror_keeps_local_dotfiles_when_the_server_hides_them():
    rows, _ = deletions(["index.html"], ["index.html", ".htaccess", ".git/config", "old.txt"],
                        destination_dirs=[".git"], hides_dotfiles=True)
    assert rows == {"old.txt": DELETE_FILE}
    rows, _ = deletions(["index.html"], ["index.html", ".htaccess"])
    assert rows == {".htaccess": DELETE_FILE}


def test_mirror_still_deletes_real_extras_files_then_folders():
    rows, _ = deletions(["a.txt"], ["a.txt", "old/b.txt"], destination_dirs=["old"])
    assert rows == {"old/b.txt": DELETE_FILE, "old": DELETE_DIR}


@pytest.mark.skipif(os.name != "nt", reason="jonctions Windows")
def test_mirror_does_not_walk_into_a_junction(hash_server, tmp_path):  # noqa: F811
    import _winapi
    port, root, _ = hash_server
    outside = tmp_path / "ailleurs"
    outside.mkdir()
    (outside / "precieux.txt").write_bytes(b"ne pas toucher")
    local = tmp_path / "local"
    local.mkdir()
    (local / "keep.txt").write_bytes(b"k")
    (root / "keep.txt").write_bytes(b"k")
    _winapi.CreateJunction(str(outside), str(local / "lien"))
    plan = plan_for(port, local, False, mirror=True)
    assert not any(path.startswith("lien") for path in plan)
    assert (outside / "precieux.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="jonctions Windows")
def test_folder_upload_skips_a_junction_instead_of_failing(hash_server, tmp_path):  # noqa: F811
    import _winapi
    port, root, _ = hash_server
    outside = tmp_path / "ailleurs"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"s")
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "index.html").write_bytes(b"<p>")
    _winapi.CreateJunction(str(outside), str(folder / "lien"))
    worker = TransferWorker(Transfer(connection(port), str(folder), "/site", True, directory=True, conflict="overwrite"))
    results, messages = [], []
    worker.result.connect(results.append)
    worker.activity.connect(messages.append)
    worker.run()
    assert results == ["Terminé"]
    assert (root / "site" / "index.html").exists() and not (root / "site" / "lien").exists()
    assert any("ignoré" in m for m in messages)


# ---------- deletions wait for the copies ----------

def test_mirror_deletions_start_after_every_copy(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from smftp.ui.main_window import MainWindow
    from test_audit_known_issues import close_window
    import smftp.workers.sync as sync
    started = []

    class FakeDeleter:
        def __init__(self, *args):
            self.report, self.done = FakeSignal(), FakeSignal()

        def start(self):
            started.append(True)

        def isRunning(self):
            return False

    class FakeSignal:
        def connect(self, slot):
            pass
    monkeypatch.setattr(sync, "SyncDeleter", FakeDeleter)
    QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        first, second = Transfer(Connection("h", "u"), "a", "/a", False), Transfer(Connection("h", "u"), "b", "/b", False)
        window.delete_after_copies({id(first), id(second)}, Connection("h", "u"), ".", "/", False, [("x", DELETE_FILE, 1, (1, 1.0))])
        assert started == []
        window.transfers.outcome.emit(first, "Terminé")
        assert started == []
        window.transfers.outcome.emit(second, "Échoué")
        assert started == [True]
        # Cancelled copies: nothing is deleted.
        third = Transfer(Connection("h", "u"), "c", "/c", False)
        window.delete_after_copies({id(third)}, Connection("h", "u"), ".", "/", False, [("y", DELETE_FILE, 1, (1, 1.0))])
        window.transfers.outcome.emit(third, "Annulé")
        assert started == [True]
    finally:
        window.__dict__.pop("sync_deleter", None)  # The fake is not a QThread.
        close_window(window)
