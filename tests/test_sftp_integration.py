"""Real SSH/SFTP handshake and listing against an isolated loopback server."""
import socket
import threading
import time
import os
from pathlib import Path
import paramiko
import pytest
from smftp.core.clients import SFTPClient, UnknownHostError
from smftp.models.entries import Connection


@pytest.fixture
def sftp_server(tmp_path):
    root = tmp_path / "remote"
    root.mkdir()
    (root / "hello.txt").write_text("hello", encoding="utf-8")
    host_key = paramiko.RSAKey.generate(2048)

    class Auth(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            return paramiko.AUTH_SUCCESSFUL if (username, password) == ("test", "secret") else paramiko.AUTH_FAILED
        def get_allowed_auths(self, username):
            return "password"
        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    class Files(paramiko.SFTPServerInterface):
        def resolve(self, path):
            target = (root / path.lstrip("/")).resolve()
            target.relative_to(root.resolve())
            return target
        def canonicalize(self, path):
            return "/"
        def list_folder(self, path):
            entries = []
            for item in self.resolve(path).iterdir():
                attrs = paramiko.SFTPAttributes.from_stat(item.stat())
                attrs.filename = item.name
                entries.append(attrs)
            return entries
        def stat(self, path):
            try:
                return paramiko.SFTPAttributes.from_stat(self.resolve(path).stat())
            except OSError as error:
                return paramiko.SFTPServer.convert_errno(error.errno)
        def rename(self, oldpath, newpath):
            try:
                self.resolve(oldpath).rename(self.resolve(newpath))
                return paramiko.SFTP_OK
            except OSError as error:
                return paramiko.SFTPServer.convert_errno(error.errno)
        def posix_rename(self, oldpath, newpath):
            try:
                os.replace(self.resolve(oldpath), self.resolve(newpath))
                return paramiko.SFTP_OK
            except OSError as error:
                return paramiko.SFTPServer.convert_errno(error.errno)
        def chattr(self, path, attr):
            try:
                if attr.st_mode is not None:
                    os.chmod(self.resolve(path), attr.st_mode)
                return paramiko.SFTP_OK
            except OSError as error:
                return paramiko.SFTPServer.convert_errno(error.errno)
        def open(self, path, flags, attr):
            try:
                fd = os.open(self.resolve(path), flags, 0o600)
                mode = "r+b" if flags & os.O_RDWR else ("wb" if flags & os.O_WRONLY else "rb")
                stream = os.fdopen(fd, mode)
                handle = paramiko.SFTPHandle(flags)
                if mode != "wb":
                    handle.readfile = stream
                if mode != "rb":
                    handle.writefile = stream
                return handle
            except OSError as error:
                return paramiko.SFTPServer.convert_errno(error.errno)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    stopped = threading.Event()
    transports = []
    def serve():
        while not stopped.is_set():
            try:
                sock, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            transport = paramiko.Transport(sock)
            transports.append(transport)
            transport.add_server_key(host_key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Files)
            try:
                transport.start_server(server=Auth())
            except (EOFError, OSError, paramiko.SSHException):
                transport.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield listener.getsockname()[1], host_key
    stopped.set()
    listener.close()
    for transport in transports:
        transport.close()
    thread.join(timeout=2)


def test_real_sftp_unknown_host(sftp_server, tmp_path):
    port, key = sftp_server
    known_hosts = tmp_path / "known_hosts"
    known_hosts.touch()
    connection = Connection("127.0.0.1", "test", "SFTP", port, "secret")
    client = SFTPClient(str(known_hosts))
    try:
        with pytest.raises(UnknownHostError):
            client.connect(connection)
    finally:
        client.close()


def test_real_sftp_transfer_and_resume(sftp_server, tmp_path):
    port, key = sftp_server
    connection = Connection("127.0.0.1", "test", "SFTP", port, "secret")
    known_hosts = tmp_path / "trusted_hosts"
    trusted = paramiko.HostKeys()
    trusted.add(f"[127.0.0.1]:{port}", key.get_name(), key)
    trusted.save(str(known_hosts))
    messages = []
    client = SFTPClient(str(known_hosts), messages.append)
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    content = bytes(range(256)) * 512
    source.write_bytes(content)
    try:
        client.connect(Connection("127.0.0.1", "test", "SFTP", port, "secret"))
        trace = "\n".join(messages)
        assert "connexion TCP établie" in trace
        assert "négociation SSH terminée" in trace
        assert "authentification SSH par mot de passe" in trace
        assert "sous-système SFTP ouvert" in trace
        assert "secret" not in trace
        client.upload(str(source), "/transfer.bin", lambda *args: None)
        destination.write_bytes(content[:1234])
        client.download("/transfer.bin", str(destination), lambda *args: None, 1234)
        assert destination.read_bytes() == content
        # Start with a shorter remote file, then resume the upload.
        shorter = tmp_path / "short.bin"
        shorter.write_bytes(content[:1000])
        client.upload(str(shorter), "/transfer.bin", lambda *args: None)
        client.upload(str(source), "/transfer.bin", lambda *args: None, 1000)
        client.download("/transfer.bin", str(destination), lambda *args: None)
        assert destination.read_bytes() == content
    finally:
        client.close()
    trusted = paramiko.HostKeys()
    trusted.add(f"[127.0.0.1]:{port}", key.get_name(), key)
    trusted.save(str(known_hosts))
    client = SFTPClient(str(known_hosts))
    try:
        assert client.connect(connection) == "/"
        entries = client.listdir("/")
        assert {e.name: e for e in entries}["hello.txt"].size == 5
    finally:
        client.close()


def test_worker_publishes_sftp_temp_file_atomically(sftp_server, tmp_path):
    from smftp.utils.storage import data_dir
    from smftp.workers.transfer import Transfer, TransferWorker
    port, key = sftp_server
    hosts = paramiko.HostKeys()
    hosts.add(f"[127.0.0.1]:{port}", key.get_name(), key)
    hosts.save(str(data_dir() / "known_hosts"))
    local = tmp_path / "replacement.txt"
    local.write_bytes(b"replacement-content" * 10000)
    task = Transfer(Connection("127.0.0.1", "test", "SFTP", port, "secret"), str(local), "/hello.txt", True, conflict="overwrite")
    worker = TransferWorker(task)
    results = []
    worker.result.connect(results.append)
    worker.run()
    assert results == ["Terminé"]
    assert (tmp_path / "remote" / "hello.txt").read_bytes() == local.read_bytes()
    assert not list((tmp_path / "remote").glob(".*.part"))
    wrong_key = paramiko.RSAKey.generate(2048)
    hosts.add(f"[127.0.0.1]:{port}", wrong_key.get_name(), wrong_key)
    hosts.save(str(data_dir() / "known_hosts"))
    client = SFTPClient(str(data_dir() / "known_hosts"))
    try:
        with pytest.raises(paramiko.BadHostKeyException):
            client.connect(task.connection)
    finally:
        client.close()
