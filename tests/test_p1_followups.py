"""Edit continuations stay bound to their server; remote moves never replace silently."""
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from smftp.core.clients import FTPClient, DestinationExists
from smftp.models.entries import Connection, Entry
from smftp.ui.main_window import MainWindow
from smftp.workers.session import Session
from test_clients import ftp_server
from test_audit_known_issues import close_window


def connect(port):
    client = FTPClient()
    client.connect(Connection("127.0.0.1", "test", "FTP", port, "secret"))
    return client


def test_move_refuses_existing_destination(ftp_server):
    port, root = ftp_server
    (root / "a.txt").write_bytes(b"source")
    (root / "b.txt").write_bytes(b"keep me")
    client = connect(port)
    try:
        with pytest.raises(DestinationExists):
            client.move("/a.txt", "/b.txt")
        assert (root / "b.txt").read_bytes() == b"keep me"
        assert (root / "a.txt").exists()
        client.move("/a.txt", "/c.txt")
        assert (root / "c.txt").read_bytes() == b"source"
    finally:
        client.close()


def test_move_replace_requires_explicit_choice_and_never_replaces_folders(ftp_server):
    port, root = ftp_server
    (root / "a.txt").write_bytes(b"source")
    client = connect(port)
    try:
        with pytest.raises(IsADirectoryError):
            client.move("/a.txt", "/dossier", replace=True)
        assert (root / "dossier").is_dir()
    finally:
        client.close()


def test_session_reports_move_conflict_instead_of_error(ftp_server):
    port, root = ftp_server
    (root / "a.txt").write_bytes(b"source")
    (root / "b.txt").write_bytes(b"keep me")
    session = Session()
    session.client = connect(port)
    session.connection = Connection("127.0.0.1", "test", "FTP", port, "secret")
    conflicts, failures = [], []
    session.move_conflict.connect(lambda s, t: conflicts.append((s, t)))
    session.failed.connect(failures.append)
    try:
        session.operate("rename", ["/a.txt", "/b.txt"])
        assert conflicts == [("/a.txt", "/b.txt")] and not failures
        assert (root / "b.txt").read_bytes() == b"keep me"
    finally:
        session.client.close()


def test_session_inspects_only_the_original_server(ftp_server):
    port, root = ftp_server
    session = Session()
    session.client = connect(port)
    session.connection = Connection("127.0.0.1", "test", "FTP", port, "secret")
    inspected, failed = [], []
    session.inspected.connect(lambda token, entry: inspected.append(token))
    session.inspect_failed.connect(failed.append)
    session.failed.connect(lambda error: None)
    try:
        session.inspect_path("t1", "/bonjour.txt", "SFTP://other@elsewhere:22")
        assert failed == ["t1"] and inspected == []
        session.inspect_path("t2", "/bonjour.txt", session.connection.identity)
        assert inspected == ["t2"]
    finally:
        session.client.close()


def test_same_path_on_two_servers_keeps_separate_saves(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    window = MainWindow()
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)
    first = Connection("one.invalid", "audit")
    second = Connection("two.invalid", "audit")
    requests = []
    window.inspect_requested.connect(lambda token, path, identity: requests.append((token, identity)))
    try:
        window.transfers.pause_all()  # Never start the follow-up upload to the fake servers.
        window.online = True
        records = []
        for connection in (first, second):
            window.connection = connection
            record = dict(remote="/index.php", local=str(tmp_path / connection.host), connection=connection, baseline=None)
            window.save_remote(record)
            records.append(record)
        assert [identity for _, identity in requests] == [first.identity, second.identity]
        assert all(any(r is record for r in window.pending_saves.values()) for record in records)
        # The answer for the first server releases only the first record.
        window.inspected_file(requests[0][0], None)
        assert [r for r in window.pending_saves.values()] == [records[1]]
    finally:
        window.transfers.pause_all()
        close_window(window)


def test_upload_finished_after_server_change_does_not_inspect_other_server(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    window = MainWindow()
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: asked.append(args[2]) or QMessageBox.StandardButton.No)
    original = Connection("one.invalid", "audit")
    requests = []
    window.inspect_requested.connect(lambda token, path, identity: requests.append(identity))
    try:
        from smftp.workers.transfer import Transfer
        record = dict(remote="/index.php", local=str(tmp_path / "index.php"), connection=original,
                      baseline=(1, 1.0), unsent=True)
        task = Transfer(original, record["local"], record["remote"], True)
        window.edit_uploads[id(task)] = record
        window.online = True
        window.connection = Connection("two.invalid", "audit")
        window.transfer_completed(task)
        assert requests == [] and record["unsent"] is False and record["baseline_unknown"]
        # Back on the original server, the next save asks before overwriting.
        window.connection = original
        window.save_remote(record)
        token = next(iter(window.pending_saves))
        window.inspected_file(token, Entry("index.php", False, 1, 1.0))
        assert asked and "n’a pas pu être relevée" in asked[0]
    finally:
        close_window(window)
