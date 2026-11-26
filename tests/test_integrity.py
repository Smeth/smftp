"""Fingerprint verification, directory resume, mirror sync and implicit FTPS on loopback servers."""
import datetime
import hashlib
import ipaddress
import ssl
from dataclasses import replace
from threading import Thread
import pytest
from smftp.core.clients import FTPClient, SFTPClient, ImplicitFTPSClient, FTPSClient
from smftp.models.entries import Connection
from smftp.workers.transfer import Transfer, TransferWorker
from smftp.workers.sync import SyncPlanner, SyncDeleter, CONTENT, DELETE_FILE, DELETE_DIR


def serve(handler, tmp_path):
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.servers import FTPServer
    auth = DummyAuthorizer()
    auth.add_user("test", "secret", str(tmp_path), perm="elradfmwMT")
    handler.authorizer = auth
    server = FTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, kwargs={"timeout": 0.05}, daemon=True)
    thread.start()
    return server, thread


def hashing_handler(base):
    class Handler(base):
        corrupt = False
        proto_cmds = dict(base.proto_cmds)
        proto_cmds["HASH"] = dict(perm=None, auth=True, arg=True, help="Syntax: HASH <SP> path")

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._extra_feats = [*getattr(self, "_extra_feats", []), "HASH SHA-256*;MD5"]

        def ftp_HASH(self, line):
            path = self.fs.ftp2fs(line)
            with open(path, "rb") as stream:
                data = stream.read()
            digest = hashlib.sha256(b"x" + data if Handler.corrupt else data).hexdigest()
            self.respond(f"213 SHA-256 0-{len(data)} {digest} {line}")
    return Handler


@pytest.fixture
def hash_server(tmp_path):
    from pyftpdlib.handlers import FTPHandler
    root = tmp_path / "remote"
    root.mkdir()
    handler = hashing_handler(FTPHandler)
    server, thread = serve(handler, root)
    yield server.socket.getsockname()[1], root, handler
    server.close_all()
    thread.join(timeout=2)


def connection(port, protocol="FTP"):
    return Connection("127.0.0.1", "test", protocol, port, "secret")


def run(task):
    worker = TransferWorker(task)
    messages, outcome = [], []
    worker.activity.connect(messages.append)
    worker.result.connect(outcome.append)
    worker.failed.connect(lambda error: messages.append(error.summary + " " + error.details))
    worker.run()
    return outcome[0], messages


# ---------- server fingerprints ----------

def test_ftp_hash_command_is_detected_and_parsed(hash_server):
    port, root, _ = hash_server
    (root / "a.txt").write_bytes(b"bonjour")
    client = FTPClient()
    try:
        client.connect(connection(port))
        assert client.checksum("/a.txt") == ("sha256", hashlib.sha256(b"bonjour").hexdigest())
    finally:
        client.close()


def test_ftp_without_hash_support_returns_none(tmp_path):
    from pyftpdlib.handlers import FTPHandler
    class Plain(FTPHandler):
        pass
    (tmp_path / "a.txt").write_bytes(b"x")
    server, thread = serve(Plain, tmp_path)
    client = FTPClient()
    try:
        client.connect(connection(server.socket.getsockname()[1]))
        assert client.checksum("/a.txt") is None
        assert client.hash_method is False
    finally:
        client.close()
        server.close_all()
        thread.join(timeout=2)


def test_sftp_checksum_runs_sha256sum_and_falls_back_quietly():
    class Channel:
        def __init__(self, replies, command_log):
            self.replies, self.log, self.sent = replies, command_log, False
        def settimeout(self, value): pass
        def exec_command(self, command):
            self.log.append(command)
            self.output, self.status = self.replies.get(command.split(" ")[0], (b"", 127))
        def shutdown_write(self): pass
        def recv(self, size):
            if self.sent:
                return b""
            self.sent = True
            return self.output
        def recv_exit_status(self): return self.status
        def close(self): pass
    digest = hashlib.sha256(b"x").hexdigest()
    commands = []
    replies = {"sha256sum": (f"{digest} */srv/a b.txt\n".encode(), 0)}
    client = SFTPClient("unused")
    client.timeout = 5
    client.ssh = type("SSH", (), {"get_transport": lambda self: type("T", (), {
        "open_session": lambda self, timeout=None: Channel(replies, commands)})()})()
    assert client.checksum("/srv/a b.txt") == ("sha256", digest)
    assert commands[0] == "sha256sum -b -- '/srv/a b.txt'"
    # internal-sftp only: every command fails, detection is not repeated.
    replies.clear()
    client.hash_command = None
    assert client.checksum("/x") is None
    tried = len(commands)
    assert client.checksum("/y") is None and len(commands) == tried


# ---------- verification after transfer ----------

def test_upload_and_download_are_verified_by_fingerprint(hash_server, tmp_path):
    port, root, _ = hash_server
    source = tmp_path / "source.bin"
    source.write_bytes(b"0123456789" * 1000)
    status, messages = run(Transfer(connection(port), str(source), "/source.bin", True))
    assert status == "Terminé"
    assert any("SHA-256 vérifiée" in m for m in messages)
    target = tmp_path / "back.bin"
    status, messages = run(Transfer(connection(port), str(target), "/source.bin", False))
    assert status == "Terminé" and target.read_bytes() == source.read_bytes()
    assert any("SHA-256 vérifiée" in m for m in messages)


def test_mismatched_upload_keeps_original_and_removes_temporary(hash_server, tmp_path):
    port, root, handler = hash_server
    (root / "f.txt").write_bytes(b"original")
    source = tmp_path / "f.txt"
    source.write_bytes(b"nouveau contenu")
    handler.corrupt = True
    status, messages = run(Transfer(connection(port), str(source), "/f.txt", True, conflict="overwrite"))
    assert status == "Échoué"
    assert (root / "f.txt").read_bytes() == b"original"
    assert not list(root.glob(".f.txt.smftp-*.part"))
    assert any("empreinte" in m.lower() for m in messages)


def test_mismatched_download_keeps_local_file(hash_server, tmp_path):
    port, root, handler = hash_server
    (root / "f.txt").write_bytes(b"distant")
    target = tmp_path / "f.txt"
    target.write_bytes(b"local")
    handler.corrupt = True
    status, _ = run(Transfer(connection(port), str(target), "/f.txt", False, conflict="overwrite"))
    assert status == "Échoué"
    assert target.read_bytes() == b"local"
    assert not list(tmp_path.glob(".f.txt.smftp-*.part"))


def test_verification_can_be_disabled(hash_server, tmp_path):
    port, _, handler = hash_server
    handler.corrupt = True
    source = tmp_path / "f.txt"
    source.write_bytes(b"x")
    worker = TransferWorker(Transfer(connection(port), str(source), "/f.txt", True))
    worker.verify = False
    outcome = []
    worker.result.connect(outcome.append)
    worker.run()
    assert outcome == ["Terminé"]


# ---------- directory resume ----------

def test_resumed_directory_skips_files_already_copied(hash_server, tmp_path):
    port, root, _ = hash_server
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "a.txt").write_bytes(b"aaa")
    (folder / "z.txt").write_bytes(b"zzz")
    task = Transfer(connection(port), str(folder), "/site", True, directory=True, conflict="overwrite")
    status, _ = run(task)
    assert status == "Terminé" and set(task.completed) == {"/site/a.txt", "/site/z.txt"}
    (folder / "c.txt").write_bytes(b"ccc")  # New file.
    (folder / "z.txt").write_bytes(b"changed")  # Changed source: sent again.
    _, messages = run(replace(task, resume=True))
    skipped = [m for m in messages if "déjà copié" in m]
    assert len(skipped) == 1 and "a.txt" in skipped[0]
    assert (root / "site" / "c.txt").read_bytes() == b"ccc"
    # Replacing z.txt then depends on the server's RNTO semantics (refused by
    # pyftpdlib on Windows); the new content has been sent and verified anyway.
    assert any("vérifiée : z.txt" in m for m in messages)


def test_resumed_download_recopies_a_destination_changed_since(hash_server, tmp_path):
    port, root, _ = hash_server
    (root / "site").mkdir()
    (root / "site" / "a.txt").write_bytes(b"aaa")
    local = tmp_path / "site"
    task = Transfer(connection(port), str(local), "/site", False, directory=True, conflict="overwrite")
    assert run(task)[0] == "Terminé"
    (local / "a.txt").write_bytes(b"locally edited")
    status, messages = run(replace(task, resume=True))
    assert status == "Terminé" and not any("déjà copié" in m for m in messages)
    assert (local / "a.txt").read_bytes() == b"aaa"


def test_completed_files_survive_the_saved_queue(tmp_path):
    from smftp.utils.queue_store import write_queue, read_queue
    task = Transfer(Connection("h.test", "u", "FTP", 21), str(tmp_path), "/d", True, True,
                    completed={"/d/a.txt": [3, 1.5, 3, None]})
    write_queue([task])
    assert read_queue()[0].completed == {"/d/a.txt": [3, 1.5, 3, None]}


# ---------- synchronization ----------

def plan_for(port, local, upload, **options):
    plans = []
    worker = SyncPlanner(connection(port), str(local), "/", upload, **options)
    worker.planned.connect(plans.append)
    worker.run()
    return {row[0]: row for row in plans[0]}


def test_content_comparison_finds_same_size_changes_and_ignores_dates(hash_server, tmp_path):
    port, root, _ = hash_server
    local = tmp_path / "local"
    local.mkdir()
    (local / "same.txt").write_bytes(b"abc")
    (root / "same.txt").write_bytes(b"abc")
    (local / "edited.txt").write_bytes(b"abc")
    (root / "edited.txt").write_bytes(b"xyz")
    import os
    os.utime(local / "same.txt", (4_000_000_000, 4_000_000_000))  # Newer, identical content.
    plan = plan_for(port, local, True, compare_content=True)
    assert set(plan) == {"edited.txt"} and plan["edited.txt"][1] == CONTENT
    assert "same.txt" in plan_for(port, local, True)  # Date rule without hashing.


def test_mirror_upload_deletes_remote_extras_but_keeps_changed_files(hash_server, tmp_path):
    port, root, _ = hash_server
    local = tmp_path / "local"
    local.mkdir()
    (local / "keep.txt").write_bytes(b"k")
    (root / "keep.txt").write_bytes(b"k")
    (root / "extra.txt").write_bytes(b"e")
    (root / "changed.txt").write_bytes(b"c")
    (root / "old").mkdir()
    (root / "old" / "inner.txt").write_bytes(b"i")
    plan = plan_for(port, local, True, mirror=True)
    assert plan["extra.txt"][1] == DELETE_FILE and plan["old/inner.txt"][1] == DELETE_FILE
    assert plan["old"][1] == DELETE_DIR and "keep.txt" not in plan
    (root / "changed.txt").write_bytes(b"changed after the comparison")
    rows = [plan[k] for k in ("extra.txt", "changed.txt", "old/inner.txt", "old")]
    done = []
    deleter = SyncDeleter(connection(port), str(local), "/", True, rows)
    deleter.done.connect(lambda deleted, kept: done.append((deleted, kept)))
    deleter.run()
    assert done == [(3, 1)]
    assert sorted(p.name for p in root.iterdir()) == ["changed.txt", "keep.txt"]


def test_mirror_download_deletes_local_extras(hash_server, tmp_path):
    port, root, _ = hash_server
    local = tmp_path / "local"
    (local / "empty" / "deep").mkdir(parents=True)
    (local / "extra.txt").write_bytes(b"e")
    plan = plan_for(port, local, False, mirror=True)
    assert [plan[k][1] for k in ("extra.txt", "empty/deep", "empty")] == [DELETE_FILE, DELETE_DIR, DELETE_DIR]
    rows = [plan["extra.txt"], plan["empty/deep"], plan["empty"]]
    deleter = SyncDeleter(connection(port), str(local), "/", False, rows)
    deleter.run()
    assert list(local.iterdir()) == []


def test_without_mirror_nothing_is_planned_for_deletion(hash_server, tmp_path):
    port, root, _ = hash_server
    local = tmp_path / "local"
    local.mkdir()
    (root / "extra.txt").write_bytes(b"e")
    assert plan_for(port, local, True) == {}


# ---------- FTPS over a real TLS server ----------

@pytest.fixture
def certificate(tmp_path):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    path = tmp_path / "server.pem"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return path


def tls_server(tmp_path, certificate, implicit):
    from pyftpdlib.handlers import TLS_FTPHandler
    root = tmp_path / "tls-remote"
    root.mkdir()
    (root / "hello.txt").write_bytes(b"hello")

    class Handler(hashing_handler(TLS_FTPHandler)):
        certfile = str(certificate)
        ssl_context = None
        tls_control_required = True
        tls_data_required = True

        def handle(self):
            if implicit:
                self.secure_connection(self.ssl_context)  # TLS before the 220 greeting.
            else:
                super().handle()

        def handle_ssl_established(self):
            if implicit and not getattr(self, "greeted", False):
                self.greeted = True
                TLS_FTPHandler.handle(self)

    server, thread = serve(Handler, root)
    return server, thread, root


@pytest.mark.parametrize("implicit", [True, False])
def test_ftps_end_to_end_with_certificate_check(tmp_path, certificate, monkeypatch, implicit):
    original = ssl.create_default_context
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: original(cafile=str(certificate)))
    server, thread, root = tls_server(tmp_path, certificate, implicit)
    port = server.socket.getsockname()[1]
    protocol = "FTPSI" if implicit else "FTPS"
    client = (ImplicitFTPSClient if implicit else FTPSClient)()
    try:
        client.connect(connection(port, protocol))
        assert "hello.txt" in {e.name for e in client.listdir("/")}
        # Servers such as vsftpd (require_ssl_reuse) refuse a data connection without it.
        assert client.ftp.last_data_session_reused is True
        assert client.checksum("/hello.txt")[1] == hashlib.sha256(b"hello").hexdigest()
    finally:
        client.close()
    source = tmp_path / "up.txt"
    source.write_bytes(b"over tls")
    assert run(Transfer(connection(port, protocol), str(source), "/up.txt", True))[0] == "Terminé"
    assert (root / "up.txt").read_bytes() == b"over tls"
    target = tmp_path / "down.txt"
    assert run(Transfer(connection(port, protocol), str(target), "/hello.txt", False))[0] == "Terminé"
    assert target.read_bytes() == b"hello"
    server.close_all()
    thread.join(timeout=2)


def test_implicit_ftps_rejects_an_untrusted_certificate(tmp_path, certificate):
    server, thread, _ = tls_server(tmp_path, certificate, True)
    client = ImplicitFTPSClient()
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            client.connect(Connection("127.0.0.1", "test", "FTPSI", server.socket.getsockname()[1], "secret", timeout=5))
    finally:
        client.close()
        server.close_all()
        thread.join(timeout=2)


def test_mirror_preview_confirms_then_copies_and_deletes(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    import smftp.workers.sync as sync_module
    from test_audit_known_issues import close_window
    QApplication.instance() or QApplication([])
    from smftp.ui.main_window import MainWindow
    asked, started = [], []
    monkeypatch.setattr(QDialog, "exec", lambda self: 1)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)

    class Deleter:
        def __init__(self, connection, local, remote, remote_side, rows, parent=None):
            started.append((remote_side, [r[0] for r in rows]))
            self.report = self.done = type("S", (), {"connect": lambda *a: None})()
        def start(self): pass
        def isRunning(self): return False
        def requestInterruption(self): pass
    monkeypatch.setattr(sync_module, "SyncDeleter", Deleter)
    window = MainWindow()
    try:
        added = []
        monkeypatch.setattr(window.transfers, "add", lambda task, **k: added.append(task.remote))
        plan = [("new.txt", "Nouveau", 3, None), ("old.txt", DELETE_FILE, 1, (1, 1.0)), ("gone", DELETE_DIR, 0, None)]
        window.show_sync_plan(plan, connection(21), str(tmp_path), "/www", True)
        assert added == ["/www/new.txt"]
        assert started == [(True, ["old.txt", "gone"])]
        assert asked and "2 élément(s)" in asked[0] and "serveur" in asked[0]
    finally:
        close_window(window)
