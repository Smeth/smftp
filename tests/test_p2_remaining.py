"""Global rate limit, planned sync copies, local conflicts and safe imports."""
from pathlib import Path
import time
from PySide6.QtWidgets import QApplication, QMessageBox
from smftp.models.entries import Connection, Entry
from smftp.utils.paths import unique_copy_name
from smftp.workers.local import LocalOperation
from smftp.workers.transfer import RateLimiter, Transfer, TransferWorker
from smftp.workers.sync import SyncPlanner
from test_clients import ftp_server


# ---------- global rate limit ----------

def test_rate_limiter_is_shared_between_transfers():
    limiter = RateLimiter(1000)
    # Two transfers report 500 bytes each: together they used one second of budget.
    first = limiter.reserve(500)
    second = limiter.reserve(500)
    assert second > first
    assert 0.6 < second <= 1.0


def test_rate_limiter_unlimited_and_updates():
    limiter = RateLimiter(0)
    assert limiter.reserve(10_000_000) == 0
    limiter.set_rate(100)
    assert limiter.reserve(100) > 0.5


def test_transfer_queue_workers_share_the_queue_limiter(tmp_path):
    QApplication.instance() or QApplication([])
    from smftp.ui.transfers import Transfers
    transfers = Transfers()
    transfers.pause_all()
    transfers.add(Transfer(Connection("a.invalid", "u"), str(tmp_path / "a"), "/a", True))
    transfers.add(Transfer(Connection("a.invalid", "u"), str(tmp_path / "b"), "/b", True))
    transfers.speed.setValue(64)
    workers = [r["worker"] for r in transfers.records]
    assert all(w.limiter is transfers.limiter for w in workers)
    assert transfers.limiter.rate == 64 * 1024


# ---------- synchronization ----------

def test_sync_plan_carries_destination_metadata(ftp_server, tmp_path):
    port, root = ftp_server
    local = tmp_path / "local"
    local.mkdir()
    (local / "same.txt").write_bytes(b"12345")
    (root / "same.txt").write_bytes(b"1")
    worker = SyncPlanner(Connection("127.0.0.1", "test", "FTP", port, "secret"), str(local), "/", True)
    plans = []
    worker.planned.connect(plans.append)
    worker.run()
    row = next(r for r in plans[0] if r[0] == "same.txt")
    assert row[1] == "Modifié" and row[3][0] == 1


class MemoryClient:
    def __init__(self, entry):
        self.entry = entry
        self.uploaded = []
    def exists(self, path):
        return self.entry if not path.endswith(".part") else Entry("x.part", False, 3)
    def makedirs(self, path): pass
    def upload(self, local, remote, callback, offset=0):
        self.uploaded.append(remote)
        callback(3, 3)
    def replace(self, source, target): pass


def planned_worker(tmp_path, expected):
    source = tmp_path / "f.txt"
    source.write_bytes(b"new")
    task = Transfer(Connection("a.invalid", "u"), str(source), "/f.txt", True,
                    conflict="ask", planned=True, expected=expected)
    return TransferWorker(task), source


def test_planned_copy_overwrites_unchanged_destination(tmp_path):
    worker, source = planned_worker(tmp_path, (1, 100.0))
    asked = []
    worker.conflict = lambda name: asked.append(name) or "skip"
    client = MemoryClient(Entry("f.txt", False, 1, 100.0))
    worker.copy_file(client, source, "/f.txt")
    assert client.uploaded and not asked


def test_planned_copy_asks_when_destination_changed_since_scan(tmp_path):
    worker, source = planned_worker(tmp_path, (1, 100.0))
    asked = []
    worker.conflict = lambda name: asked.append(name) or "skip"
    client = MemoryClient(Entry("f.txt", False, 2, 200.0))
    worker.copy_file(client, source, "/f.txt")
    assert asked == ["f.txt"] and not client.uploaded


def test_planned_copy_asks_when_destination_appeared_since_scan(tmp_path):
    worker, source = planned_worker(tmp_path, None)
    asked = []
    worker.conflict = lambda name: asked.append(name) or "skip"
    worker.copy_file(MemoryClient(Entry("f.txt", False, 1, 1.0)), source, "/f.txt")
    assert asked == ["f.txt"]


def test_empty_sync_plan_shows_message_and_no_dialog(monkeypatch):
    QApplication.instance() or QApplication([])
    from smftp.ui.main_window import MainWindow
    from test_audit_known_issues import close_window
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: shown.append(a[2]))
    window = MainWindow()
    try:
        window.show_sync_plan([], Connection("a.invalid", "u"), ".", "/", True)
        assert shown and "déjà synchronisés" in shown[0]
    finally:
        close_window(window)


# ---------- local copy conflicts ----------

def test_unique_copy_names_are_numbered(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "a (copie).txt").write_text("2")
    assert unique_copy_name(tmp_path / "a.txt", Path.exists).name == "a (copie 2).txt"
    assert unique_copy_name(Path("site.v2"), lambda p: False, directory=True).name == "site.v2 (copie)"


def run_local(operation, *arguments):
    worker = LocalOperation(operation, list(arguments))
    errors = []
    worker.failed.connect(errors.append)
    worker.run()
    return errors


def test_local_copy_refuses_existing_unless_overwrite(tmp_path):
    source, target = tmp_path / "s.txt", tmp_path / "t.txt"
    source.write_text("new")
    target.write_text("old")
    errors = run_local("copy", str(source), str(target))
    assert errors and "existe déjà" in errors[0] and target.read_text() == "old"
    assert run_local("copy", str(source), str(target), True) == []
    assert target.read_text() == "new" and source.exists()
    assert not list(tmp_path.glob("*.smftp-tmp"))


def test_local_move_overwrite_replaces_file_only(tmp_path):
    source, target = tmp_path / "s.txt", tmp_path / "t.txt"
    source.write_text("new")
    target.write_text("old")
    assert run_local("move", str(source), str(target), True) == []
    assert target.read_text() == "new" and not source.exists()
    folder = tmp_path / "folder"
    folder.mkdir()
    (tmp_path / "file").write_text("x")
    errors = run_local("move", str(tmp_path / "file"), str(folder), True)
    assert errors and folder.is_dir()


# ---------- import ----------

FILEZILLA = """<?xml version="1.0" encoding="UTF-8"?>
<FileZilla3><Servers><Server>
<Host>ftp.example.invalid</Host><Port>21</Port><Protocol>0</Protocol><Type>0</Type>
<User>alice</User><Pass encoding="base64">c2VjcmV0</Pass><Logontype>1</Logontype>
<PasvMode>MODE_ACTIVE</PasvMode><TimezoneOffset>60</TimezoneOffset>
<EncodingType>Custom</EncodingType><CustomEncoding>ISO-8859-1</CustomEncoding>
<MaximumMultipleConnections>2</MaximumMultipleConnections><Name>Site</Name>
</Server></Servers></FileZilla3>"""


def test_filezilla_ignored_options_are_reported(tmp_path):
    from smftp.utils.profiles import read_profiles
    path = tmp_path / "fz.xml"
    path.write_text(FILEZILLA, encoding="utf-8")
    result = read_profiles(str(path))
    warning = next(w for w in result.warnings if "non reprises" in w)
    for expected in ("mode FTP actif", "60 min", "ISO-8859-1", "2 connexion"):
        assert expected in warning


def test_import_removes_secrets_when_profiles_cannot_be_finalized(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    import keyring
    import smftp.ui.main_window as module
    from smftp.ui.main_window import MainWindow
    from smftp.utils.profiles import read_profiles
    from test_audit_known_issues import close_window
    vault = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, k: vault.get((s, k)))
    monkeypatch.setattr(keyring, "set_password", lambda s, k, v: vault.__setitem__((s, k), v))
    monkeypatch.setattr(keyring, "delete_password", lambda s, k: vault.pop((s, k)))
    path = tmp_path / "fz.xml"
    path.write_text(FILEZILLA, encoding="utf-8")
    result = read_profiles(str(path))
    window = MainWindow()
    calls = []
    def save(sites):
        calls.append(sites)
        if len(calls) == 2:
            raise OSError("disk full")
    monkeypatch.setattr(module, "save_sites", save)
    try:
        from smftp.utils.profiles import merge_profiles
        imported = merge_profiles([], result.profiles)
        updated = merge_profiles(window.sites, imported)
        module.save_sites(updated)
        window.import_secrets(imported, updated, result)
        assert vault == {}
        assert not any(item.get("remember") for item in updated)
        assert any("retirés du coffre-fort" in w for w in result.warnings)
    finally:
        close_window(window)
