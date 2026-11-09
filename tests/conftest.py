"""Keep all desktop tests away from real profiles, queues and preferences."""
import pytest


@pytest.fixture(autouse=True)
def isolated_desktop_state(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from PySide6.QtCore import QSettings
    import smftp.ui.main_window as module
    monkeypatch.setenv("SMFTP_DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setattr(module, "QSettings", lambda: QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
