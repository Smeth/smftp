"""Active / passive FTP mode and the connection dialog's test button."""
import time
import pytest
from PySide6.QtWidgets import QApplication
from smftp.core.clients import FTPClient
from smftp.models.entries import Connection
from smftp.utils.profiles import normalize_profile, profile_for
from smftp.ui.connection_dialog import ConnectionDialog
from test_clients import ftp_server  # noqa: F401  (fixture)


def wait_for(condition, seconds=10):
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    assert condition()


@pytest.mark.parametrize("passive", [True, False])
def test_ftp_lists_in_both_modes(ftp_server, passive):
    port, _ = ftp_server
    client = FTPClient()
    try:
        client.connect(Connection("127.0.0.1", "test", "FTP", port, "secret", passive=passive))
        assert client.ftp.passiveserver is passive
        assert "bonjour.txt" in {e.name for e in client.listdir("/")}
    finally:
        client.close()


def test_profiles_keep_the_mode_and_default_to_passive():
    assert normalize_profile(dict(host="h", user="u"))["passive"] is True
    saved = profile_for(Connection("h", "u", "FTP", 21, passive=False))
    assert saved["passive"] is False
    assert Connection(**saved).passive is False


def test_dialog_mode_selector_follows_protocol():
    QApplication.instance() or QApplication([])
    form = ConnectionDialog(site=dict(host="h", user="u", protocol="FTP", port=21, passive=False))
    assert form.transfer_mode.isEnabled() and form.connection().passive is False
    form.set_protocol("SFTP")
    assert not form.transfer_mode.isEnabled() and form.connection().passive is True


def fill(form, port, password):
    form.host.setText("127.0.0.1")
    form.port.setValue(port)
    form.user.setText("test")
    form.password.setText(password)


def test_test_button_reports_success(ftp_server):
    QApplication.instance() or QApplication([])
    port, _ = ftp_server
    form = ConnectionDialog()
    assert not form.test_button.isEnabled()  # Nothing to test yet.
    fill(form, port, "secret")
    form.transfer_mode.setCurrentIndex(1)  # Active mode.
    assert form.test_button.isEnabled()
    form.test_button.click()
    wait_for(lambda: form.test_result.objectName() in ("success", "error") and form.test_button.isEnabled())
    assert form.test_result.objectName() == "success", form.test_result.text()
    assert "élément" in form.test_result.text() and "actif" in form.test_result.text()
    assert not form.save_only and form.result() == 0  # Testing neither saves nor closes.


def test_test_button_reports_failure(ftp_server):
    QApplication.instance() or QApplication([])
    port, _ = ftp_server
    form = ConnectionDialog()
    fill(form, port, "wrong")
    form.test_connection()
    wait_for(lambda: form.test_result.objectName() == "error" and form.test_button.isEnabled())
    assert form.test_result.text().startswith("✕")
    assert "wrong" not in form.test_result.text() + form.test_result.toolTip()


def test_closing_during_a_test_does_not_wait(ftp_server):
    QApplication.instance() or QApplication([])
    port, _ = ftp_server
    form = ConnectionDialog()
    fill(form, port, "secret")
    form.test_connection()
    probe = form.probe
    started = time.monotonic()
    form.reject()
    assert time.monotonic() - started < 1
    wait_for(lambda: probe.isFinished())
