import base64
import ftplib
import socket
from urllib.parse import quote
import paramiko
import pytest
from smftp.core.errors import describe_error, redact
from smftp.models.entries import Connection
from smftp.workers.session import Session


@pytest.mark.parametrize("error,expected", [
    (ftplib.error_perm("530 Login incorrect"), "Authentification FTP refusée"),
    (ftplib.error_perm("550 Permission denied"), "550"),
    (ftplib.error_temp("425 Can't open data connection"), "canal de données"),
    (ConnectionRefusedError(10061, "refused"), "Connexion refusée"),
    (socket.gaierror(-2, "Name not known"), "Hôte introuvable"),
    (TimeoutError("timed out"), "Délai"),
    (paramiko.AuthenticationException("Authentication failed"), "Authentification SSH refusée"),
    (paramiko.PasswordRequiredException("Private key is encrypted"), "phrase secrète"),
])
def test_diagnostics_preserve_actual_cause(error, expected):
    info = describe_error(error, Connection("test.local", "alice"), "Connexion")
    assert expected in info.summary
    assert str(error) in info.details
    assert type(error).__name__ in info.details
    assert "SFTP test.local:22" in info.details


def test_nested_connection_refused():
    error = paramiko.ssh_exception.NoValidConnectionsError({("127.0.0.1", 22): ConnectionRefusedError(10061, "refused")})
    info = describe_error(error)
    assert "Connexion refusée" in info.summary
    assert "10061" in info.details


def test_password_redacted_in_all_diagnostic_forms():
    secret = "sëcret:p@ss!"
    encoded = base64.b64encode(secret.encode()).decode()
    error = ftplib.error_perm(f"530 rejected {secret} {quote(secret, safe='')} {encoded}")
    info = describe_error(error, Connection("test.local", "alice", password=secret))
    for value in [secret, quote(secret, safe=""), encoded]:
        assert value not in info.details
    assert "530" in info.details
    assert "masqué" in info.details
    assert "hidden" not in redact("ftp://user:hidden@host PASS other-secret")
    assert "other-secret" not in redact("PASS other-secret")


def test_session_signal_and_log_are_sanitized(caplog):
    session = Session()
    session.connection = Connection("test.local", "alice", "FTP", 21, "private-pass")
    errors = []
    session.failed.connect(errors.append)
    session.report(ftplib.error_perm("530 Login refused private-pass"))
    assert "530" in errors[0].details
    assert "private-pass" not in errors[0].details
    assert "private-pass" not in caplog.text
