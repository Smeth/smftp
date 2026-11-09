import base64
import pytest
import paramiko
from smftp.core.clients import SFTPClient, UnknownHostError
from smftp.models.entries import Connection


def test_unknown_host_requires_confirmation(tmp_path, monkeypatch):
    hosts = tmp_path / "known_hosts"
    hosts.touch()
    key = paramiko.RSAKey.generate(2048)
    def connect(client, *args, **kwargs):
        client._policy.missing_host_key(client, "example.test", key)
    monkeypatch.setattr(paramiko.SSHClient, "connect", connect)
    client = SFTPClient(str(hosts))
    try:
        with pytest.raises(UnknownHostError) as exc:
            client.connect(Connection("example.test", "test"))
        assert exc.value.fingerprint.startswith("SHA256:")
        assert base64.b64decode(exc.value.key) == key.asbytes()
        assert hosts.read_text() == ""
    finally:
        client.close()
