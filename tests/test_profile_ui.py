from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog
from PySide6.QtCore import QEventLoop, QTimer
from smftp.models.entries import Connection
from smftp.ui.connection_dialog import ConnectionDialog
from smftp.ui.main_window import MainWindow
from smftp.core.errors import ErrorInfo
import pytest


def test_connection_dialog_loads_vault_and_saves_without_connecting(monkeypatch):
    import keyring
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(keyring, "get_password", lambda *args: "remembered-secret")
    dialog = ConnectionDialog(site=dict(host="test.local", user="alice", name="Production", protocol="FTP", port=2121))
    assert dialog.name.text() == "Production"
    assert dialog.password.text() == "remembered-secret"
    assert dialog.remember.isChecked()
    assert dialog.save_button.isEnabled()
    dialog.accept_save_only()
    assert dialog.save_only
    assert dialog.connection().port == 2121


def test_offline_profile_save_update_and_detailed_error(monkeypatch):
    app = QApplication.instance() or QApplication([])
    import smftp.ui.main_window as module
    written, vault_calls, boxes = [], [], []
    monkeypatch.setattr(module, "load_sites", lambda: [])
    monkeypatch.setattr(module, "save_sites", lambda profiles: written.append(profiles))
    monkeypatch.setattr(module, "store_credential", lambda connection: vault_calls.append(connection))
    monkeypatch.setattr(QMessageBox, "exec", lambda box: boxes.append((box.text(), box.detailedText())))
    window = MainWindow()
    try:
        profile = Connection("test.local", "alice", password="private", remember=True, name="Production")
        assert window.persist_profile(profile)
        assert not window.online
        assert window.connection is None
        assert len(window.sites) == 1
        assert "password" not in written[-1][0]
        assert vault_calls[-1].password == "private"
        assert "Production" in window.site_list.item(0).text()
        profile.name = "Updated"
        profile.remember = False
        assert window.persist_profile(profile, window.sites[0])
        assert len(window.sites) == 1
        assert not window.sites[0]["remember"]
        window.on_error(ErrorInfo("Authentification refusée", "530 rejected private"))
        assert "530" in boxes[-1][1]
        assert "private" not in boxes[-1][1]
        def vault_unavailable(connection):
            raise RuntimeError("vault unavailable")
        monkeypatch.setattr(module, "store_credential", vault_unavailable)
        profile.remember = True
        assert window.persist_profile(profile, window.sites[0])
        assert "coffre-fort" in boxes[-1][0]

        # Import a real XML through the UI, explicitly opting into the vault.
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            xml = Path(directory) / "filezilla.xml"
            xml.write_text('<FileZilla3><Servers><Server><Host>imported.test</Host><User>bob</User><Name>Imported</Name><Pass encoding="base64">aW1wb3J0LXNlY3JldA==</Pass></Server></Servers></FileZilla3>')
            monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(xml), ""))
            monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
            monkeypatch.setattr(module, "store_credential", lambda connection: vault_calls.append(connection))
            window.import_sites()
        imported = next(s for s in window.sites if s["host"] == "imported.test")
        assert imported["remember"]
        assert vault_calls[-1].password == "import-secret"
        assert "import-secret" not in str(written[-1])
    finally:
        window.close()
        loop = QEventLoop()
        window.thread.finished.connect(loop.quit)
        QTimer.singleShot(2000, loop.quit)
        loop.exec()


@pytest.mark.parametrize("approve", [False, True])
def test_xml_header_repair_confirmation(tmp_path, monkeypatch, approve):
    app = QApplication.instance() or QApplication([])
    import smftp.ui.main_window as module
    path = tmp_path / "sites.xml"
    original = b'<?xml version="1.0" standalone="true"?><FileZilla3><Servers><Server><Host>test.local</Host><User>alice</User></Server></Servers></FileZilla3>'
    path.write_bytes(original)
    written, questions, errors = [], [], []
    monkeypatch.setattr(module, "load_sites", lambda: [])
    monkeypatch.setattr(module, "save_sites", lambda sites: written.append(sites))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    def answer(*args):
        questions.append(args)
        return QMessageBox.StandardButton.Yes if approve else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", answer)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)
    window = MainWindow()
    monkeypatch.setattr(window, "on_error", errors.append)
    try:
        window.import_sites()
        assert len(questions) == 1
        assert 'standalone="true"' in questions[0][2]
        assert questions[0][-1] == QMessageBox.StandardButton.No
        assert bool(written) is approve
        assert bool(window.sites) is approve
        assert not errors
        assert path.read_bytes() == original
    finally:
        window.close()
        loop = QEventLoop()
        window.thread.finished.connect(loop.quit)
        QTimer.singleShot(2000, loop.quit)
        loop.exec()
