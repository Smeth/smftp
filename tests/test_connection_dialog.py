"""New-connection form: SFTP default, required markers, in-field password toggle, pasted URLs."""
import pytest
from PySide6.QtWidgets import QApplication, QLineEdit
from smftp.ui.connection_dialog import ConnectionDialog, parse_address


def dialog():
    QApplication.instance() or QApplication([])
    return ConnectionDialog()


def test_defaults_to_sftp_and_marks_required_fields():
    form = dialog()
    assert form.protocol_value() == "SFTP" and form.port.value() == 22
    assert form.windowTitle() == "Nouvelle connexion"
    for label in ("Protocole", "Hôte", "Port", "Utilisateur"):
        assert form.labels[label].text().endswith("*")
    for label in ("Nom du profil", "Mot de passe / phrase secrète", "Groupe de serveurs"):
        assert not form.labels[label].text().endswith("*")
    assert not form.ok_button.isEnabled() and form.validation.text() == ""  # No error on an empty form.


def test_required_fields_report_errors_and_enable_buttons():
    form = dialog()
    form.host.setText("ftp.example.test")
    assert "utilisateur" in form.validation.text().lower()
    assert form.user.property("invalid") and not form.ok_button.isEnabled()
    form.user.setText("alice")
    assert form.ok_button.isEnabled() and form.save_button.isEnabled() and not form.validation.text()


def test_password_toggle_lives_inside_the_field():
    form = dialog()
    assert form.show_password in form.password.actions()
    assert form.password.echoMode() == QLineEdit.EchoMode.Password
    form.show_password.trigger()
    assert form.password.echoMode() == QLineEdit.EchoMode.Normal
    form.reject()  # Closing hides the secret again.
    assert form.password.echoMode() == QLineEdit.EchoMode.Password


def test_protocol_change_keeps_a_custom_port():
    form = dialog()
    form.set_protocol("SFTP")
    assert form.port.value() == 22
    form.port.setValue(2222)
    form.set_protocol("FTPS")
    assert form.port.value() == 2222


@pytest.mark.parametrize("text, expected", [
    ("sftp://bob@files.test:2222/var/www", dict(protocol="SFTP", user="bob", host="files.test", port=2222, remote_directory="/var/www")),
    ("ftps://files.test", dict(protocol="FTPSI", host="files.test")),
    ("bob@files.test:21", dict(user="bob", host="files.test", port=21)),
    ("files.test", dict(host="files.test")),
    ("gopher://files.test", {}),
])
def test_parse_address(text, expected):
    assert parse_address(text) == expected


def test_pasted_url_fills_the_form():
    form = dialog()
    form.host.setText("sftp://bob@files.test:2222/srv")
    form.expand_address()
    assert form.host.text() == "files.test" and form.protocol_value() == "SFTP"
    assert form.port.value() == 2222 and form.user.text() == "bob" and form.remote_directory.text() == "/srv"
    assert form.ok_button.isEnabled()


def test_missing_ssh_key_is_reported(tmp_path):
    form = dialog()
    form.set_protocol("SFTP")
    form.host.setText("files.test")
    form.user.setText("bob")
    form.key.setText(str(tmp_path / "absent"))
    assert not form.ok_button.isEnabled() and "introuvable" in form.validation.text()
