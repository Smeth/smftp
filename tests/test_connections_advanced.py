"""Proxy (SOCKS5, HTTP CONNECT), PuTTY keys, SSH agent, FTP server time offset,
active-mode warning, resume after a long pause and the downloaded-file mark."""
import base64
import calendar
import hashlib
import hmac
import os
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread

import paramiko
import pytest

from proxy_servers import HTTPConnectProxy, Socks5Proxy
from smftp.core.clients import FTPClient, SFTPClient, ShellClient, detected_offsets
from smftp.models.entries import Connection


# ---------- servers ----------

@pytest.fixture
def ftp_server(tmp_path):
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    root = tmp_path / "ftp"
    root.mkdir()
    (root / "bonjour.txt").write_text("bonjour", encoding="utf-8")
    auth = DummyAuthorizer()
    auth.add_user("test", "secret", str(root), perm="elradfmwMT")

    class Handler(FTPHandler):
        pass
    Handler.authorizer = auth
    server = FTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"timeout": 0.05}, daemon=True)
    thread.start()
    yield server.socket.getsockname()[1], root, Handler
    server.close_all()
    thread.join(timeout=2)


@pytest.fixture
def ssh_server(tmp_path):
    """SSH server accepting the password "secret" and the keys put in ``allowed``."""
    root = tmp_path / "sftp"
    root.mkdir()
    (root / "hello.txt").write_text("hello", encoding="utf-8")
    host_key = paramiko.Ed25519Key.from_private_key_file(str(write_openssh_ed25519(tmp_path / "host_key")))
    allowed = []

    class Auth(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            return paramiko.AUTH_SUCCESSFUL if (username, password) == ("test", "secret") else paramiko.AUTH_FAILED

        def check_auth_publickey(self, username, key):
            return paramiko.AUTH_SUCCESSFUL if any(key.asbytes() == k.asbytes() for k in allowed) else paramiko.AUTH_FAILED

        def get_allowed_auths(self, username):
            return "password,publickey"

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    class Files(paramiko.SFTPServerInterface):
        def canonicalize(self, path):
            return "/"

        def list_folder(self, path):
            entries = []
            for item in root.iterdir():
                attrs = paramiko.SFTPAttributes.from_stat(item.stat())
                attrs.filename = item.name
                entries.append(attrs)
            return entries

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
    port = listener.getsockname()[1]
    known_hosts = tmp_path / "known_hosts"
    hosts = paramiko.HostKeys()
    hosts.add(f"[127.0.0.1]:{port}", host_key.get_name(), host_key)
    hosts.save(str(known_hosts))
    yield port, str(known_hosts), allowed
    stopped.set()
    listener.close()
    for transport in transports:
        transport.close()
    thread.join(timeout=2)


def write_openssh_ed25519(path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    key = ed25519.Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                       serialization.NoEncryption()))
    return path


@pytest.fixture
def socks_proxy():
    proxy = Socks5Proxy()
    yield proxy
    proxy.close()


@pytest.fixture
def socks_proxy_auth():
    proxy = Socks5Proxy("alice", "proxy-pass")
    yield proxy
    proxy.close()


@pytest.fixture
def http_proxy():
    proxy = HTTPConnectProxy("bob", "http-pass")
    yield proxy
    proxy.close()


def via(proxy, kind, base: Connection, user="", password="") -> Connection:
    base.proxy_type, base.proxy_host, base.proxy_port = kind, "127.0.0.1", proxy.port
    base.proxy_user, base.proxy_password = user, password
    return base


# ---------- proxy: FTP ----------

@pytest.mark.parametrize("kind", ["socks5", "socks5-auth", "http"])
def test_ftp_through_proxy_uses_the_tunnel_for_control_and_data(kind, ftp_server, tmp_path, request):
    port, root, _ = ftp_server
    proxy, user, password = {
        "socks5": (request.getfixturevalue("socks_proxy"), "", ""),
        "socks5-auth": (request.getfixturevalue("socks_proxy_auth"), "alice", "proxy-pass"),
        "http": (request.getfixturevalue("http_proxy"), "bob", "http-pass"),
    }[kind]
    connection = via(proxy, "http" if kind == "http" else "socks5",
                     Connection("127.0.0.1", "test", "FTP", port, "secret"), user, password)
    messages = []
    client = FTPClient(messages.append)
    try:
        client.connect(connection)
        assert "bonjour.txt" in {e.name for e in client.listdir("/")}
        target = tmp_path / "copie.txt"
        client.download("/bonjour.txt", str(target), lambda done, total: None)
        assert target.read_text(encoding="utf-8") == "bonjour"
        source = tmp_path / "envoi.txt"
        source.write_text("par le proxy", encoding="utf-8")
        client.upload(str(source), "/envoi.txt", lambda done, total: None)
        assert (root / "envoi.txt").read_text(encoding="utf-8") == "par le proxy"
    finally:
        client.close()
    # Control connection plus one tunnel per data connection, all to the server.
    assert proxy.targets[0] == ("127.0.0.1", port)
    assert len(proxy.targets) >= 4 and all(host == "127.0.0.1" for host, _ in proxy.targets)
    assert any("proxy" in message for message in messages)
    assert "proxy-pass" not in "\n".join(messages) and "http-pass" not in "\n".join(messages)


def test_ftp_active_mode_is_refused_through_a_proxy(ftp_server, socks_proxy):
    from smftp.core.protocol_log import ProxyActiveModeError
    port, _, _ = ftp_server
    connection = via(socks_proxy, "socks5", Connection("127.0.0.1", "test", "FTP", port, "secret", passive=False))
    client = FTPClient()
    with pytest.raises(ProxyActiveModeError, match="passif"):
        client.connect(connection)
    assert socks_proxy.targets == []


def test_wrong_proxy_credentials_give_a_clear_error(ftp_server, socks_proxy_auth, http_proxy):
    from smftp.core.errors import describe_error
    from smftp.core.proxy import ProxyError
    port, _, _ = ftp_server
    for proxy, kind, pattern in ((socks_proxy_auth, "socks5", "SOCKS5"), (http_proxy, "http", "407")):
        connection = via(proxy, kind, Connection("127.0.0.1", "test", "FTP", port, "secret"), "intrus", "mauvais")
        client = FTPClient()
        with pytest.raises(ProxyError, match=pattern) as caught:
            client.connect(connection)
        assert pattern in describe_error(caught.value, connection).summary
        assert proxy.refused == 1


def test_proxy_password_comes_from_the_vault_when_not_typed(monkeypatch, ftp_server, socks_proxy_auth):
    import keyring
    from smftp.utils.profiles import proxy_key
    port, _, _ = ftp_server
    connection = via(socks_proxy_auth, "socks5", Connection("127.0.0.1", "test", "FTP", port, "secret"), "alice", "")
    vault = {("SMFTP", proxy_key(connection.identity)): "proxy-pass"}
    monkeypatch.setattr(keyring, "get_password", lambda service, key: vault.get((service, key)))
    client = FTPClient()
    try:
        client.connect(connection)
        assert client.listdir("/")
    finally:
        client.close()


# ---------- proxy: SFTP and SSH ----------

@pytest.mark.parametrize("kind", ["socks5", "http"])
def test_sftp_and_ssh_through_proxy(kind, ssh_server, request):
    port, known_hosts, _ = ssh_server
    proxy = request.getfixturevalue("socks_proxy" if kind == "socks5" else "http_proxy")
    user, password = ("", "") if kind == "socks5" else ("bob", "http-pass")
    connection = via(proxy, kind, Connection("127.0.0.1", "test", "SFTP", port, "secret"), user, password)
    client = SFTPClient(known_hosts)
    try:
        client.connect(connection)
        assert "hello.txt" in {e.name for e in client.listdir("/")}
    finally:
        client.close()
    shell = ShellClient(known_hosts)
    try:
        shell.connect(via(proxy, kind, Connection("127.0.0.1", "test", "SSH", port, "secret"), user, password))
        assert shell.ssh.get_transport().is_authenticated()
    finally:
        shell.close()
    assert proxy.targets == [("127.0.0.1", port)] * 2


# ---------- profile, form and vault ----------

def test_profile_keeps_proxy_settings_but_never_the_proxy_password():
    from smftp.utils.profiles import normalize_profile, profile_for
    connection = Connection("h.test", "u", "FTP", 21, proxy_type="http", proxy_host="proxy.test", proxy_port=3128,
                            proxy_user="bob", proxy_password="secret-proxy", use_agent=True, time_offset=120)
    profile = profile_for(connection)
    assert profile["proxy_type"] == "http" and profile["proxy_host"] == "proxy.test" and profile["proxy_port"] == 3128
    assert profile["proxy_user"] == "bob" and profile["time_offset"] == 120 and profile["use_agent"] is True
    assert "proxy_password" not in profile and "secret-proxy" not in repr(profile) + repr(connection)
    old = normalize_profile({"host": "h.test", "user": "u", "protocol": "FTP"})
    assert old["proxy_type"] == "none" and old["time_offset"] is None and old["use_agent"] is False
    with pytest.raises(ValueError):
        normalize_profile({"host": "h", "user": "u", "proxy_type": "socks5", "proxy_host": ""})
    with pytest.raises(ValueError):
        normalize_profile({"host": "h", "user": "u", "proxy_type": "socks4", "proxy_host": "p"})


def test_proxy_password_is_stored_in_the_vault_only(monkeypatch):
    import keyring
    from smftp.utils.profiles import proxy_key, store_proxy_credential
    vault = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, k, v: vault.__setitem__((s, k), v))
    monkeypatch.setattr(keyring, "get_password", lambda s, k: vault.get((s, k)))
    monkeypatch.setattr(keyring, "delete_password", lambda s, k: vault.pop((s, k)))
    connection = Connection("h.test", "u", "SFTP", 22, proxy_type="socks5", proxy_host="p", proxy_user="alice",
                            proxy_password="pp")
    store_proxy_credential(connection)
    assert vault == {("SMFTP", proxy_key(connection.identity)): "pp"}
    connection.proxy_type = "none"
    store_proxy_credential(connection)
    assert vault == {}


def test_connection_dialog_proxy_agent_offset_and_active_warning(qapp_dialog):
    dialog = qapp_dialog
    dialog.host.setText("ftp.test")
    dialog.user.setText("u")
    dialog.set_protocol("FTP")
    assert "Mode actif" not in dialog.warning.text()
    dialog.transfer_mode.setCurrentIndex(1)
    assert "Mode actif" in dialog.warning.text()
    dialog.set_protocol("FTPS")
    assert "Mode actif" not in dialog.warning.text()
    dialog.set_protocol("FTP")
    dialog.transfer_mode.setCurrentIndex(0)
    # Proxy: the host is required, and active mode cannot go through it.
    dialog.proxy_type.setCurrentIndex(dialog.proxy_type.findData("http"))
    assert dialog.proxy_port.value() == 8080 and not dialog.ok_button.isEnabled()
    dialog.proxy_host.setText("proxy.test")
    dialog.proxy_user.setText("bob")
    dialog.proxy_password.setText("pp")
    assert dialog.ok_button.isEnabled()
    dialog.transfer_mode.setCurrentIndex(1)
    assert not dialog.ok_button.isEnabled()
    dialog.transfer_mode.setCurrentIndex(0)
    dialog.time_offset.setCurrentIndex(dialog.time_offset.findData(-300))
    connection = dialog.connection()
    assert (connection.proxy_type, connection.proxy_host, connection.proxy_port) == ("http", "proxy.test", 8080)
    assert connection.proxy_password == "pp" and connection.time_offset == -300 and not connection.use_agent
    assert not dialog.use_agent.isEnabled()
    dialog.set_protocol("SFTP")
    assert dialog.use_agent.isEnabled() and not dialog.time_offset.isEnabled()
    dialog.use_agent.setChecked(True)
    assert dialog.connection().use_agent and dialog.connection().time_offset is None


def test_connection_dialog_loads_saved_proxy_and_options(monkeypatch):
    import keyring
    from PySide6.QtWidgets import QApplication
    from smftp.ui.connection_dialog import ConnectionDialog
    from smftp.utils.profiles import proxy_key
    QApplication.instance() or QApplication([])
    site = {"host": "h.test", "user": "u", "protocol": "SFTP", "port": 22, "proxy_type": "socks5",
            "proxy_host": "p.test", "proxy_port": 1081, "proxy_user": "alice", "use_agent": True}
    vault = {("SMFTP", proxy_key("SFTP://u@h.test:22")): "pp"}
    monkeypatch.setattr(keyring, "get_password", lambda s, k: vault.get((s, k)))
    dialog = ConnectionDialog(site=site, sites=[site])
    connection = dialog.connection()
    assert (connection.proxy_type, connection.proxy_host, connection.proxy_port) == ("socks5", "p.test", 1081)
    assert connection.proxy_user == "alice" and connection.proxy_password == "pp" and connection.use_agent
    dialog.deleteLater()


@pytest.fixture
def qapp_dialog():
    from PySide6.QtWidgets import QApplication
    from smftp.ui.connection_dialog import ConnectionDialog
    QApplication.instance() or QApplication([])
    dialog = ConnectionDialog(sites=[])
    yield dialog
    dialog.deleteLater()


# ---------- SSH agent ----------

def connect_arguments(monkeypatch, tmp_path, connection):
    seen = {}

    def fake_connect(self, *args, **kwargs):
        seen.update(kwargs)
        raise paramiko.AuthenticationException("stop")
    monkeypatch.setattr(paramiko.SSHClient, "connect", fake_connect)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("")
    with pytest.raises(paramiko.AuthenticationException):
        SFTPClient(str(known_hosts)).open_ssh(connection)
    return seen


def test_ssh_agent_only_when_the_profile_asks(monkeypatch, tmp_path):
    assert connect_arguments(monkeypatch, tmp_path, Connection("h.test", "u", "SFTP", 22, "pw"))["allow_agent"] is False
    seen = connect_arguments(monkeypatch, tmp_path, Connection("h.test", "u", "SSH", 22, "pw", use_agent=True))
    assert seen["allow_agent"] is True and seen["look_for_keys"] is False


# ---------- PuTTY keys ----------

def ssh_string(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def ssh_mpint(value: int) -> bytes:
    data = value.to_bytes((value.bit_length() + 8) // 8, "big") if value else b""
    return ssh_string(data)


def blobs(key):
    """(algorithm, public blob, private blob) following PuTTY's key formats."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
    if isinstance(key, rsa.RSAPrivateKey):
        numbers = key.private_numbers()
        public = ssh_string(b"ssh-rsa") + ssh_mpint(numbers.public_numbers.e) + ssh_mpint(numbers.public_numbers.n)
        private = ssh_mpint(numbers.d) + ssh_mpint(numbers.p) + ssh_mpint(numbers.q) + ssh_mpint(numbers.iqmp)
        return "ssh-rsa", public, private
    if isinstance(key, ed25519.Ed25519PrivateKey):
        raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
        return "ssh-ed25519", ssh_string(b"ssh-ed25519") + ssh_string(raw), ssh_string(seed)
    curve = {"secp256r1": "nistp256", "secp384r1": "nistp384", "secp521r1": "nistp521"}[key.curve.name]
    algorithm = f"ecdsa-sha2-{curve}"
    point = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    public = ssh_string(algorithm.encode()) + ssh_string(curve.encode()) + ssh_string(point)
    return algorithm, public, ssh_mpint(key.private_numbers().private_value)


def write_ppk(path: Path, key, version: int, passphrase: str = "", comment: str = "clé de test",
              kdf: str = "Argon2id") -> Path:
    """A .ppk file written from the PuTTY specification (sshpubk.c)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    algorithm, public, private = blobs(key)
    encryption = "aes256-cbc" if passphrase else "none"
    secret = passphrase.encode()
    extra = []
    if passphrase:
        private += hashlib.sha1(private).digest()[:(-len(private)) % 16]  # Pad to the AES block size.
        if version == 2:
            cipher_key = (hashlib.sha1(b"\0\0\0\0" + secret).digest() + hashlib.sha1(b"\0\0\0\1" + secret).digest())[:32]
            iv, mac_key = bytes(16), hashlib.sha1(b"putty-private-key-file-mac-key" + secret).digest()
        else:
            from cryptography.hazmat.primitives.kdf import argon2
            salt = os.urandom(16)
            material = getattr(argon2, kdf)(salt=salt, length=80, iterations=2, lanes=1, memory_cost=256).derive(secret)
            cipher_key, iv, mac_key = material[:32], material[32:48], material[48:]
            extra = [f"Key-Derivation: {kdf}", "Argon2-Memory: 256", "Argon2-Passes: 2",
                     "Argon2-Parallelism: 1", f"Argon2-Salt: {salt.hex()}"]
        encryptor = Cipher(algorithms.AES(cipher_key), modes.CBC(iv)).encryptor()
        encrypted = encryptor.update(private) + encryptor.finalize()
    else:
        mac_key = hashlib.sha1(b"putty-private-key-file-mac-key").digest() if version == 2 else b""
        encrypted = private
    data = b"".join(ssh_string(part) for part in (algorithm.encode(), encryption.encode(), comment.encode(),
                                                  public, private))
    mac = hmac.new(mac_key, data, hashlib.sha1 if version == 2 else hashlib.sha256).hexdigest()

    def lines(blob):
        text = base64.b64encode(blob).decode()
        return [text[i:i + 64] for i in range(0, len(text), 64)]
    public_lines, private_lines = lines(public), lines(encrypted)
    content = [f"PuTTY-User-Key-File-{version}: {algorithm}", f"Encryption: {encryption}", f"Comment: {comment}",
               f"Public-Lines: {len(public_lines)}", *public_lines, *extra,
               f"Private-Lines: {len(private_lines)}", *private_lines, f"Private-MAC: {mac}"]
    path.write_bytes(("\r\n".join(content) + "\r\n").encode("utf-8"))
    return path


def generated_keys():
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
    return {"rsa": rsa.generate_private_key(public_exponent=65537, key_size=2048),
            "ed25519": ed25519.Ed25519PrivateKey.generate(),
            "ecdsa": ec.generate_private_key(ec.SECP256R1())}


KEYS = generated_keys()


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("passphrase", ["", "phrase très secrète"])
@pytest.mark.parametrize("kind", ["rsa", "ed25519", "ecdsa"])
def test_ppk_files_are_read_into_paramiko_keys(tmp_path, kind, passphrase, version):
    from smftp.core.ppk import load_ppk
    key = KEYS[kind]
    path = write_ppk(tmp_path / f"{kind}.ppk", key, version, passphrase)
    loaded = load_ppk(str(path), passphrase or None)
    _, public, _ = blobs(key)
    assert loaded.asbytes() == public
    # The key signs: paramiko can authenticate with it.
    signature = paramiko.Message(loaded.sign_ssh_data(b"data").asbytes())
    assert paramiko.PKey.from_type_string(loaded.get_name(), public).verify_ssh_sig(b"data", signature)


@pytest.mark.parametrize("kdf", ["Argon2i", "Argon2d"])
def test_ppk_v3_other_argon2_variants(tmp_path, kdf):
    from smftp.core.ppk import load_ppk
    path = write_ppk(tmp_path / "k.ppk", KEYS["ed25519"], 3, "pw", kdf=kdf)
    assert load_ppk(str(path), "pw").asbytes() == blobs(KEYS["ed25519"])[1]


@pytest.mark.parametrize("version", [2, 3])
def test_ppk_errors_are_clear(tmp_path, version):
    from smftp.core.ppk import PPKError, PPKPassphraseRequired, load_ppk
    path = write_ppk(tmp_path / "k.ppk", KEYS["ed25519"], version, "bonne")
    with pytest.raises(PPKPassphraseRequired, match="phrase secrète"):
        load_ppk(str(path))
    with pytest.raises(PPKError, match="incorrecte"):
        load_ppk(str(path), "mauvaise")
    plain = write_ppk(tmp_path / "plain.ppk", KEYS["ed25519"], version)
    plain.write_bytes(plain.read_bytes().replace("Comment: clé de test".encode(), "Comment: modifié".encode()))
    with pytest.raises(PPKError, match="MAC"):
        load_ppk(str(plain))
    other = tmp_path / "other.ppk"
    other.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n", encoding="utf-8")
    with pytest.raises(PPKError):
        load_ppk(str(other))


@pytest.mark.parametrize("protocol", ["SFTP", "SSH"])
def test_login_with_a_ppk_key(ssh_server, tmp_path, protocol):
    port, known_hosts, allowed = ssh_server
    from smftp.core.ppk import load_ppk
    path = write_ppk(tmp_path / "login.ppk", KEYS["ed25519"], 3, "phrase")
    allowed.append(load_ppk(str(path), "phrase"))
    messages = []
    client = (SFTPClient if protocol == "SFTP" else ShellClient)(known_hosts, messages.append)
    try:
        # The secret is the key's passphrase: it is never offered as a password.
        client.connect(Connection("127.0.0.1", "test", protocol, port, "phrase", key_file=str(path)))
        assert client.ssh.get_transport().is_authenticated()
    finally:
        client.close()
    assert any(".ppk" in message for message in messages)


def test_key_picker_offers_ppk_files():
    import inspect
    from smftp.ui.connection_dialog import ConnectionDialog
    assert "*.ppk" in inspect.getsource(ConnectionDialog.choose_key)


# ---------- FTP server time offset ----------

class StubFTP:
    def __init__(self, mdtm):
        self.mdtm, self.commands = mdtm, []

    def sendcmd(self, command):
        import ftplib
        self.commands.append(command)
        if command.startswith("MDTM"):
            if self.mdtm is None:
                raise ftplib.error_perm("500 MDTM not understood")
            return "213 " + self.mdtm
        return "200 OK"


def stub_list_client(lines, mdtm, offset=None):
    import ftplib
    client = FTPClient()
    client.reports = []
    client.reporter = client.reports.append
    client.ftp = StubFTP(mdtm)
    client.identity = f"FTP://u@stub-{time.time_ns()}:21"
    client.time_offset = offset
    client.offset_checked = offset is not None

    def read_lines(command):
        if command.startswith("MLSD"):
            raise ftplib.error_perm("500 MLSD not understood")
        return lines
    client.read_lines = read_lines
    return client


def recent_listing(server_time: datetime) -> str:
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"-rw-r--r-- 1 u g 5 {months[server_time.month - 1]} {server_time.day:2d} {server_time:%H:%M} recent.txt"


def test_list_times_are_corrected_with_the_offset_found_by_mdtm():
    utc = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)).replace(second=0, microsecond=0)
    server = utc + timedelta(hours=5, minutes=30)  # Server clock: UTC+05:30.
    lines = [recent_listing(server), "-rw-r--r-- 1 u g 9 Jan 02 2020 old.txt", "drwxr-xr-x 2 u g 0 Jan 02 2020 dir"]
    client = stub_list_client(lines, f"{utc:%Y%m%d%H%M}17")
    entries = {e.name: e for e in client.listdir("/data")}
    assert client.time_offset == 330 and detected_offsets[client.identity] == 330
    assert entries["recent.txt"].modified == calendar.timegm(utc.timetuple())
    assert entries["old.txt"].modified == calendar.timegm((2020, 1, 2, 0, 0, 0)) - 330 * 60
    assert client.ftp.commands == ["MDTM /data/recent.txt"]
    assert any("UTC+05:30" in r for r in client.reports)
    client.listdir("/data")  # Detected once per session.
    assert client.ftp.commands == ["MDTM /data/recent.txt"]


def test_offset_detection_falls_back_without_mdtm_and_manual_offset_wins():
    utc = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)).replace(second=0, microsecond=0)
    lines = [recent_listing(utc + timedelta(hours=2))]
    client = stub_list_client(lines, None)
    entry = client.listdir("/")[0]
    assert client.time_offset is None
    assert entry.modified == (utc + timedelta(hours=2)).timestamp()  # Unchanged: this computer's local time.
    manual = stub_list_client(lines, f"{utc:%Y%m%d%H%M%S}", offset=-60)
    assert manual.listdir("/")[0].modified == calendar.timegm((utc + timedelta(hours=3)).timetuple())
    assert manual.ftp.commands == []
    # A file listed with its year only (00:00) cannot give an offset.
    old = stub_list_client(["-rw-r--r-- 1 u g 9 Jan 02 2020 old.txt"], "20200102143721")
    old.listdir("/")
    assert old.time_offset is None


def test_offset_detected_on_a_real_server_without_mlsd(ftp_server):
    from pyftpdlib.handlers import FTPHandler
    port, root, handler = ftp_server
    handler.proto_cmds = {k: v for k, v in FTPHandler.proto_cmds.items() if k not in ("MLSD", "MLST")}
    handler.use_gmt_times = False  # LIST in the server's local time, like most servers.

    def ftp_MDTM(self, path):  # MDTM always in UTC (RFC 3659).
        self.use_gmt_times = True
        try:
            return FTPHandler.ftp_MDTM(self, path)
        finally:
            self.use_gmt_times = False
    handler.ftp_MDTM = ftp_MDTM
    recent = root / "bonjour.txt"
    stamp = time.time() - 3600
    os.utime(recent, (stamp, stamp))
    expected = round(time.localtime(stamp).tm_gmtoff / 900) * 15
    client = FTPClient()
    try:
        client.connect(Connection("127.0.0.1", "test", "FTP", port, "secret"))
        entries = {e.name: e for e in client.listdir("/")}
    finally:
        client.close()
    assert client.time_offset == expected
    assert abs(entries["bonjour.txt"].modified - stamp) < 61


# ---------- transfers: long pause, downloaded-file mark ----------

def test_download_resumes_after_the_server_dropped_the_paused_connection(ftp_server, tmp_path, monkeypatch):
    from pyftpdlib.handlers import DTPHandler
    from smftp.workers.transfer import Transfer, TransferWorker
    port, root, handler = ftp_server
    monkeypatch.setattr(DTPHandler, "timeout", 1)
    content = bytes(range(256)) * (128 * 1024)  # 32 MB: more than the socket buffers hold.
    (root / "gros.bin").write_bytes(content)
    target = tmp_path / "gros.bin"
    marked = []
    monkeypatch.setattr("smftp.utils.paths.mark_from_internet", marked.append)
    class Worker(TransferWorker):
        calls = 0

        def progress(self, done, total):
            Worker.calls += 1
            if Worker.calls == 3:
                self.running.clear()  # The user pauses, long enough for the server
                # to drop the stalled transfer, then resumes.
                threading.Timer(3, self.running.set).start()
            super().progress(done, total)

    worker = Worker(Transfer(Connection("127.0.0.1", "test", "FTP", port, "secret"),
                             str(target), "/gros.bin", False))
    worker.verify = False
    results, activity, failures = [], [], []
    worker.result.connect(results.append)
    worker.activity.connect(activity.append)
    worker.failed.connect(failures.append)
    worker.run()
    assert results == ["Terminé"], [f.details for f in failures]
    assert any("reconnexion" in line for line in activity)
    assert target.read_bytes() == content
    assert marked == [target]


def test_downloads_are_marked_unless_the_preference_is_off(ftp_server, tmp_path, monkeypatch):
    from smftp.workers.transfer import Transfer, TransferWorker
    port, _, _ = ftp_server
    marked = []
    monkeypatch.setattr("smftp.utils.paths.mark_from_internet", marked.append)
    for enabled, name in ((True, "a.txt"), (False, "b.txt")):
        worker = TransferWorker(Transfer(Connection("127.0.0.1", "test", "FTP", port, "secret"),
                                         str(tmp_path / name), "/bonjour.txt", False))
        worker.mark_downloads = enabled
        results = []
        worker.result.connect(results.append)
        worker.run()
        assert results == ["Terminé"]
    assert marked == [tmp_path / "a.txt"]


def test_mark_preference_reaches_the_transfer_workers(tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from smftp.ui.settings import SettingsDialog
    QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    dialog = SettingsDialog(settings)
    assert dialog.mark_downloads.isChecked()  # On by default.
    dialog.mark_downloads.setChecked(False)
    dialog.persist(settings)
    assert settings.value("mark_downloads", True, type=bool) is False
    dialog.deleteLater()


def test_mark_from_internet_writes_the_zone_identifier(tmp_path):
    from smftp.utils.paths import mark_from_internet
    path = tmp_path / "f.txt"
    path.write_text("x")
    mark_from_internet(path)
    if os.name == "nt":
        try:
            text = Path(f"{path}:Zone.Identifier").read_text(encoding="ascii")
        except OSError:
            pytest.skip("File system without alternate data streams")
        assert "ZoneId=3" in text
