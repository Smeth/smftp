"""Security fixes: network paths, FileZilla protocol mapping, SSH key passphrase."""
import json
from pathlib import Path
import pytest
from PySide6.QtWidgets import QMessageBox
from smftp.models.entries import Connection
from smftp.utils.paths import is_network_path
from smftp.utils.profiles import read_profiles
from test_assistant import window  # noqa: F401  (fixture)


@pytest.mark.parametrize("path", [r"\\attaquant.example\x", "//attaquant/x", r"\\?\UNC\h\s", r"\\.\pipe\x",
                                  r"\/h/s", " \\\\h\\s"])
def test_network_paths_are_recognised(path):
    assert is_network_path(path)


@pytest.mark.parametrize("path", [r"C:\Site", "C:/Site", "/home/u", r"Z:\partage", "", "relatif\\x"])
def test_local_paths_are_not_network_paths(path):
    assert not is_network_path(path)


# ---------- assistant tools ----------

def test_assistant_refuses_to_list_a_network_folder(window, monkeypatch):  # noqa: F811
    touched = []
    monkeypatch.setattr(Path, "is_dir", lambda self: touched.append(str(self)) or True)
    output, error = window.assistant_panel.toolbox.execute("list_local_directory", {"path": r"\\attaquant.example\x"})
    assert error and "réseau" in output and touched == []


def test_assistant_transfer_to_a_network_path_is_refused_before_any_question(window, monkeypatch):  # noqa: F811
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: asked.append(args) or QMessageBox.StandardButton.No)
    output, error = window.assistant_panel.toolbox.execute("queue_transfer", {
        "profile": "Production", "direction": "telecharger", "local": r"\\attaquant\x\f.txt",
        "remote": "/f.txt", "directory": False})
    assert error and "réseau" in output and asked == []


def test_assistant_transfer_reads_the_disk_only_after_approval(window, monkeypatch, tmp_path):  # noqa: F811
    touched = []
    original = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: touched.append(str(self)) or original(self))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    output, error = window.assistant_panel.toolbox.execute("queue_transfer", {
        "profile": "Production", "direction": "envoyer", "local": str(tmp_path / "f.txt"),
        "remote": "/f.txt", "directory": False})
    assert not error and "refusé" in output
    assert str(tmp_path / "f.txt") not in touched


def test_assistant_cannot_schedule_a_network_folder(window):  # noqa: F811
    from test_assistant import task_input
    output, error = window.assistant_panel.toolbox.execute(
        "create_scheduled_task", task_input(Path("."), local=r"\\attaquant\partage"))
    assert error and "réseau" in output
    assert not window.assistant_panel.toolbox.scheduler.store.tasks


# ---------- imported profiles ----------

def test_json_import_drops_network_paths(tmp_path):
    path = tmp_path / "profils.json"
    path.write_text(json.dumps([{"name": "NAS", "host": "h.test", "user": "u", "protocol": "SFTP", "port": 22,
                                 "local_directory": r"\\attaquant\partage", "key_file": "//attaquant/cle"},
                                {"name": "OK", "host": "o.test", "user": "u", "protocol": "SFTP", "port": 22,
                                 "local_directory": r"C:\Sites", "key_file": r"C:\cles\id_ed25519"}]), encoding="utf-8")
    result = read_profiles(str(path))
    nas, ok = result.profiles
    assert nas["local_directory"] == "" and nas["key_file"] == ""
    assert ok["local_directory"] == r"C:\Sites" and ok["key_file"] == r"C:\cles\id_ed25519"
    assert len([w for w in result.warnings if "réseau" in w]) == 2


def filezilla(tmp_path, servers):
    path = tmp_path / "sitemanager.xml"
    path.write_text(f"<FileZilla3><Servers>{servers}</Servers></FileZilla3>", encoding="utf-8")
    return read_profiles(str(path))


def test_filezilla_tls_if_available_is_imported_as_ftps(tmp_path):
    result = filezilla(tmp_path, "<Server><Host>a.test</Host><Protocol>0</Protocol><User>u</User></Server>")
    assert result.profiles[0]["protocol"] == "FTPS"
    assert any("TLS" in w for w in result.warnings)


def test_filezilla_plain_ftp_is_imported_with_a_warning(tmp_path):
    result = filezilla(tmp_path, "<Server><Host>a.test</Host><Protocol>6</Protocol><User>u</User></Server>")
    assert result.profiles[0]["protocol"] == "FTP"
    assert any("non chiffré" in w for w in result.warnings)


def test_filezilla_network_key_file_is_dropped(tmp_path):
    result = filezilla(tmp_path, "<Server><Host>a.test</Host><Protocol>1</Protocol><User>u</User>"
                                 r"<Keyfile>\\attaquant\cle</Keyfile></Server>")
    assert result.profiles[0]["key_file"] == ""
    assert any("réseau" in w for w in result.warnings)


# ---------- SSH key passphrase ----------

def connect_arguments(monkeypatch, tmp_path, connection):
    import paramiko
    from smftp.core.clients import SFTPClient
    seen = {}

    def fake_connect(self, *args, **kwargs):
        seen.update(kwargs)
        raise paramiko.AuthenticationException("stop")
    monkeypatch.setattr(paramiko.SSHClient, "connect", fake_connect)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("")
    client = SFTPClient(str(known_hosts))
    with pytest.raises(paramiko.AuthenticationException):
        client.open_ssh(connection)
    return seen


def test_key_passphrase_is_never_offered_as_a_password(monkeypatch, tmp_path):
    seen = connect_arguments(monkeypatch, tmp_path, Connection("h.test", "u", "SFTP", 22, "phrase-secrete",
                                                     key_file=str(tmp_path / "id_ed25519")))
    assert seen["password"] is None and seen["passphrase"] == "phrase-secrete"


def test_password_login_without_key_is_unchanged(monkeypatch, tmp_path):
    seen = connect_arguments(monkeypatch, tmp_path, Connection("h.test", "u", "SFTP", 22, "mot-de-passe"))
    assert seen["password"] == "mot-de-passe" and seen["passphrase"] is None
