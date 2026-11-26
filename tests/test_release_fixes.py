"""Defects fixed before the Microsoft Store and Uptodown release."""
from threading import Thread
import pytest
from smftp.core.clients import FTPClient
from smftp.models.entries import Connection
from smftp.workers.transfer import Transfer, TransferWorker


# ---------- FTP servers without MLSD that hide dotfiles (vsftpd) ----------

def vsftpd_like_server(root, honours_list_a):
    """MLSD missing; LIST hides names starting with "." unless "LIST -a" is honoured."""
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.filesystems import AbstractedFS
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer

    class HidingFS(AbstractedFS):
        def listdir(self, path):
            names = super().listdir(path)
            if getattr(self.cmd_channel, "show_all", False):
                return names
            return [name for name in names if not name.startswith(".")]

    class Handler(FTPHandler):
        abstracted_fs = HidingFS
        proto_cmds = {k: v for k, v in FTPHandler.proto_cmds.items() if k not in ("MLSD", "MLST")}
        show_all = False

        def pre_process_command(self, line, cmd, arg):
            self.show_all = False
            if honours_list_a and cmd == "LIST" and arg.startswith("-a"):
                self.show_all = True
                arg = arg[2:].strip()
            return super().pre_process_command(line, cmd, arg)

    auth = DummyAuthorizer()
    auth.add_user("test", "secret", str(root), perm="elradfmwMT")
    Handler.authorizer = auth
    server = FTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"timeout": 0.05}, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture(params=[True, False], ids=["LIST-a", "sans-LIST-a"])
def hiding_server(tmp_path, request):
    root = tmp_path / "remote"
    root.mkdir()
    server, thread = vsftpd_like_server(root, request.param)
    yield server.socket.getsockname()[1], root, request.param
    server.close_all()
    thread.join(timeout=2)


def test_safe_upload_succeeds_when_the_listing_hides_the_partial_file(hiding_server, tmp_path):
    port, root, _ = hiding_server
    source = tmp_path / "site.txt"
    source.write_bytes(b"hello" * 1000)
    worker = TransferWorker(Transfer(Connection("127.0.0.1", "test", "FTP", port, "secret"),
                                     str(source), "/site.txt", True))
    results, errors = [], []
    worker.result.connect(results.append)
    worker.failed.connect(lambda error: errors.append(error.summary))
    worker.run()
    assert results == ["Terminé"], errors
    assert (root / "site.txt").read_bytes() == source.read_bytes()
    assert not [p for p in root.iterdir() if p.name.endswith(".part")]


def test_hidden_remote_file_is_found_and_listed_when_possible(hiding_server):
    port, root, honours_list_a = hiding_server
    (root / ".htaccess").write_bytes(b"Deny from all")
    client = FTPClient()
    try:
        client.connect(Connection("127.0.0.1", "test", "FTP", port, "secret"))
        found = client.exists("/.htaccess")
        assert found is not None and found.size == 13 and not found.is_dir
        assert found.modified > 0
        names = {entry.name for entry in client.listdir("/")}
        assert (".htaccess" in names) == honours_list_a
        assert client.exists("/absent.txt") is None
    finally:
        client.close()


# ---------- a resumed or restored task never overwrites silently ----------

class FakeClient:
    def __init__(self, files):
        self.files = files

    def exists(self, path):
        from smftp.models.entries import Entry
        return Entry(path.rsplit("/", 1)[1], False, len(self.files[path]), 1000.0) if path in self.files else None

    def download(self, remote, local, progress, offset=0):
        with open(local, "ab" if offset else "wb") as stream:
            stream.write(self.files[remote][offset:])

    def upload(self, local, remote, progress, offset=0):
        from pathlib import Path
        data = Path(local).read_bytes()
        self.files[remote] = (self.files.get(remote, b"")[:offset] if offset else b"") + data[offset:]

    def checksum(self, path):
        return None

    def makedirs(self, path):
        pass

    def replace(self, source, target):
        self.files[target] = self.files.pop(source)


def resumed(tmp_path, local_name, upload, conflict):
    task = Transfer(Connection("h.test", "u", "SFTP"), str(tmp_path / local_name), f"/r/{local_name}",
                    upload, resume=True, conflict=conflict)
    return TransferWorker(task)


def test_restored_download_applies_the_skip_rule(tmp_path):
    (tmp_path / "keep.txt").write_bytes(b"KEEP")
    worker = resumed(tmp_path, "keep.txt", False, "skip")
    worker.copy_file(FakeClient({"/r/keep.txt": b"remote"}), tmp_path / "keep.txt", "/r/keep.txt")
    assert (tmp_path / "keep.txt").read_bytes() == b"KEEP"


def test_restored_download_asks_before_replacing_local_edits(tmp_path):
    (tmp_path / "report.txt").write_bytes(b"MY LOCAL EDITS")
    worker = resumed(tmp_path, "report.txt", False, "ask")
    asked = []

    def answer(name):
        asked.append(name)
        worker.choice = "skip"
        worker.answer.set()
    worker.conflict_found.connect(answer)
    worker.copy_file(FakeClient({"/r/report.txt": b"server version"}), tmp_path / "report.txt", "/r/report.txt")
    assert asked == ["report.txt"]
    assert (tmp_path / "report.txt").read_bytes() == b"MY LOCAL EDITS"


def test_restored_upload_applies_the_skip_rule(tmp_path):
    (tmp_path / "page.html").write_bytes(b"new page")
    remote = FakeClient({"/r/page.html": b"server page"})
    worker = resumed(tmp_path, "page.html", True, "skip")
    worker.copy_file(remote, tmp_path / "page.html", "/r/page.html")
    assert remote.files["/r/page.html"] == b"server page"


def test_restored_download_with_approved_overwrite_still_replaces(tmp_path):
    (tmp_path / "data.bin").write_bytes(b"old")
    worker = resumed(tmp_path, "data.bin", False, "overwrite")
    worker.copy_file(FakeClient({"/r/data.bin": b"new content"}), tmp_path / "data.bin", "/r/data.bin")
    assert (tmp_path / "data.bin").read_bytes() == b"new content"
