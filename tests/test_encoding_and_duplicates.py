"""FTP file-name encodings and duplicate-profile detection."""
from threading import Thread
import pytest
from PySide6.QtWidgets import QApplication
from smftp.core import clients
from smftp.core.clients import FTPClient
from smftp.models.entries import Connection, normalize_encoding
from smftp.utils.profiles import normalize_profile
from smftp.ui.connection_dialog import ConnectionDialog


@pytest.fixture
def latin1_server(tmp_path):
    """A server that sends and expects names in ISO-8859-1, like many old FTP servers."""
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    (tmp_path / "café.txt").write_text("crème", encoding="utf-8")
    auth = DummyAuthorizer()
    auth.add_user("test", "secret", str(tmp_path), perm="elradfmw")
    class Handler(FTPHandler):
        encoding = "latin-1"
    Handler.authorizer = auth
    server = FTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"timeout": 0.05}, daemon=True)
    thread.start()
    yield server.socket.getsockname()[1], tmp_path
    server.close_all()
    thread.join(timeout=2)


def connect(port, encoding, log=None):
    client = FTPClient((log if log is not None else []).append)
    client.connect(Connection("127.0.0.1", "test", "FTP", port, "secret", encoding=encoding))
    return client


def test_auto_switches_to_latin1_and_names_round_trip(latin1_server, tmp_path):
    port, _ = latin1_server
    clients.detected_encodings.clear()
    log = []
    client = connect(port, "auto", log)
    try:
        assert "café.txt" in [e.name for e in client.listdir("/")]
        assert client.ftp.encoding == "latin-1"
        assert any("Latin-1" in line for line in log)
        target = tmp_path / "copie.txt"
        client.download("/café.txt", str(target), lambda done, total: None)
        assert target.read_text(encoding="utf-8") == "crème"
    finally:
        client.close()
    # A transfer connection opened afterwards starts directly in the detected encoding.
    other = connect(port, "auto")
    try:
        assert other.ftp.encoding == "latin-1"
    finally:
        other.close()
        clients.detected_encodings.clear()


def test_explicit_encoding_is_used_from_the_start(latin1_server):
    port, _ = latin1_server
    client = connect(port, "latin-1")
    try:
        assert client.ftp.encoding == "latin-1"
        assert "café.txt" in [e.name for e in client.listdir("/")]
    finally:
        client.close()


def test_wrong_explicit_encoding_still_lists(latin1_server):
    port, _ = latin1_server
    log = []
    client = connect(port, "utf-8", log)
    try:
        names = [e.name for e in client.listdir("/")]
        assert any(name.startswith("caf") for name in names)  # Shown with a replacement mark.
        assert any("Changez l’encodage" in line for line in log)
        assert client.listdir("/")  # The control channel is still in step.
    finally:
        client.close()


@pytest.mark.parametrize("value, expected", [("auto", "auto"), ("", "auto"), ("ISO-8859-1", "latin-1"),
                                             ("UTF8", "utf-8"), ("windows-1252", "cp1252"), ("nope", "auto")])
def test_normalize_encoding(value, expected):
    assert normalize_encoding(value) == expected


def test_profile_and_dialog_keep_the_encoding():
    QApplication.instance() or QApplication([])
    assert normalize_profile(dict(host="h", user="u"))["encoding"] == "auto"
    form = ConnectionDialog(site=dict(host="h", user="u", protocol="FTP", port=21, encoding="cp1252"))
    assert form.encoding.currentData() == "cp1252" and form.connection().encoding == "cp1252"
    form.set_protocol("SFTP")
    assert not form.encoding.isEnabled() and form.connection().encoding == "auto"


SITES = [dict(host="ftp.a.test", user="alice", protocol="FTP", port=21, name="Production"),
         dict(host="ftp.b.test", user="bob", protocol="FTP", port=21, name="Recette")]


def form_for(site=None):
    QApplication.instance() or QApplication([])
    return ConnectionDialog(site=site, sites=[dict(s) for s in SITES])


def test_same_server_account_is_refused():
    form = form_for()
    form.set_protocol("FTP")  # "Production" is an FTP profile; the form defaults to SFTP.
    form.host.setText("ftp.a.test")
    form.user.setText("alice")
    assert "Production" in form.validation.text()
    assert not form.ok_button.isEnabled() and not form.save_button.isEnabled()
    assert form.test_button.isEnabled()  # Testing a duplicate is harmless.
    form.user.setText("carol")
    assert form.ok_button.isEnabled()


def test_same_name_is_refused_case_insensitively():
    form = form_for()
    form.host.setText("ftp.c.test")
    form.user.setText("carol")
    form.name.setText("recette")
    assert "Recette" in form.validation.text() and form.name.property("invalid")
    assert not form.save_button.isEnabled()
    form.name.setText("Recette 2")
    assert form.save_button.isEnabled() and not form.name.property("invalid")


def test_editing_a_profile_does_not_conflict_with_itself():
    form = form_for(SITES[0])
    assert form.ok_button.isEnabled(), form.validation.text()
    form.name.setText("Recette")  # Renaming onto another profile is still refused.
    assert not form.ok_button.isEnabled()
