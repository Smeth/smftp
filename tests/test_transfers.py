from pathlib import Path
from smftp.core.clients import FTPClient
from smftp.models.entries import Connection
from smftp.workers.transfer import Transfer, TransferWorker
from test_clients import ftp_server


def test_upload_download_and_resume(ftp_server, tmp_path):
    port, root = ftp_server
    connection = Connection("127.0.0.1", "test", "FTP", port, "secret")
    source = tmp_path / "source.bin"
    data = bytes(range(256)) * 1000
    source.write_bytes(data)
    client = FTPClient()
    client.connect(connection)
    updates = []
    try:
        client.upload(str(source), "/remote.bin", lambda done, total: updates.append((done, total)))
        assert (root / "remote.bin").read_bytes() == data
        assert updates[-1] == (len(data), len(data))
        destination = tmp_path / "received.bin"
        destination.write_bytes(data[:8000])
        client.download("/remote.bin", str(destination), lambda *args: None, 8000)
        assert destination.read_bytes() == data
        (root / "remote.bin").write_bytes(data[:5000])
        client.upload(str(source), "/remote.bin", lambda *args: None, 5000)
        assert (root / "remote.bin").read_bytes() == data
    finally:
        client.close()


def test_recursive_worker_and_skip(ftp_server, tmp_path):
    port, root = ftp_server
    folder = tmp_path / "upload"
    (folder / "nested").mkdir(parents=True)
    (folder / "nested" / "hello.txt").write_text("hello")
    connection = Connection("127.0.0.1", "test", "FTP", port, "secret")
    task = Transfer(connection, str(folder), "/copied", True, True, conflict="skip")
    worker = TransferWorker(task)
    results = []
    worker.result.connect(results.append)
    worker.run()
    assert results == ["Terminé"]
    assert (root / "copied" / "nested" / "hello.txt").read_text() == "hello"
    (folder / "nested" / "hello.txt").write_text("changed")
    worker.run()
    assert (root / "copied" / "nested" / "hello.txt").read_text() == "hello"


def test_recursive_delete_and_rename(ftp_server):
    port, root = ftp_server
    client = FTPClient()
    try:
        client.connect(Connection("127.0.0.1", "test", "FTP", port, "secret"))
        client.mkdir("/created")
        client.rename("/created", "/renamed")
        (root / "renamed" / "file.txt").write_text("delete me")
        client.remove("/renamed", True)
        assert not (root / "renamed").exists()
    finally:
        client.close()


def test_cancelled_worker_does_not_connect():
    worker = TransferWorker(Transfer(Connection("invalid", "test"), "local", "/remote", True))
    results = []
    worker.result.connect(results.append)
    worker.cancel()
    worker.run()
    assert results == ["Annulé"]


def test_upload_creates_missing_parent_folders(ftp_server, tmp_path):
    port, root = ftp_server
    source = tmp_path / "single.txt"
    source.write_text("nested upload")
    task = Transfer(Connection("127.0.0.1", "test", "FTP", port, "secret"),
                    str(source), "/new/deep/single.txt", True, conflict="skip")
    results = []
    worker = TransferWorker(task)
    worker.result.connect(results.append)
    worker.run()
    assert results == ["Terminé"]
    assert (root / "new" / "deep" / "single.txt").read_text() == "nested upload"
