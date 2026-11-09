"""Protocol integration tests use a loopback-only FTP server."""
from threading import Thread
import pytest
from smftp.core.clients import FTPClient, FTPSClient
from smftp.models.entries import Connection


@pytest.fixture
def ftp_server(tmp_path):
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    (tmp_path / "bonjour.txt").write_text("bonjour", encoding="utf-8")
    (tmp_path / "dossier").mkdir()
    auth = DummyAuthorizer()
    auth.add_user("test", "secret", str(tmp_path), perm="elradfmwMT")
    class Handler(FTPHandler):
        pass
    Handler.authorizer = auth
    server = FTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"timeout": 0.05}, daemon=True)
    thread.start()
    yield server.socket.getsockname()[1], tmp_path
    server.close_all()
    thread.join(timeout=2)


def test_ftp_listing(ftp_server):
    port, _ = ftp_server
    client = FTPClient()
    try:
        assert client.connect(Connection("127.0.0.1", "test", "FTP", port, "secret")) == "/"
        entries = {e.name: e for e in client.listdir("/")}
        assert entries["bonjour.txt"].size == 7
        assert entries["dossier"].is_dir
        assert entries["bonjour.txt"].modified > 0
        assert client.listdir("/dossier") == []
    finally:
        client.close()


def test_ftps_requires_valid_certificate(monkeypatch):
    import ssl
    import smftp.core.clients as module
    contexts = []
    class FakeTLS:
        def __init__(self, context):
            contexts.append(context)
        def connect(self, *args, **kwargs): pass
        def login(self, *args): pass
        def prot_p(self): self.protected = True
        def pwd(self): return "/"
    monkeypatch.setattr(module, "LoggedFTPS", FakeTLS)
    client = FTPSClient()
    client.connect(Connection("localhost", "test", "FTPS", 21))
    assert contexts[0].verify_mode == ssl.CERT_REQUIRED
    assert contexts[0].check_hostname
    assert client.ftp.protected


def test_failed_ftp_login_reports_real_server_response(ftp_server, tmp_path, monkeypatch):
    from smftp.workers.session import Session
    import smftp.workers.session as module
    port, _ = ftp_server
    monkeypatch.setattr(module, "data_dir", lambda: tmp_path)
    session = Session()
    failures, successes = [], []
    session.failed.connect(failures.append)
    session.connected.connect(successes.append)
    session.connect_to(Connection("127.0.0.1", "test", "FTP", port, "wrong-private-pass"))
    assert not successes
    assert len(failures) == 1
    assert "530" in failures[0].summary
    assert "Authentication failed" in failures[0].details
    assert "wrong-private-pass" not in failures[0].details
