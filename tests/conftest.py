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


@pytest.fixture(autouse=True)
def collect_qt_leftovers():
    """Free the windows of each test right after it, at a quiet moment.

    Tests build many main windows without an event loop to finish destroying them;
    their Python wrappers stay in reference cycles. Left to a later, random garbage
    collection (for example in the middle of a socket call of another test), freeing
    them can abort the interpreter inside shiboken. The application itself has one window.
    """
    yield
    import gc
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
    gc.collect()
