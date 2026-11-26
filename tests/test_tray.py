"""Notification area: SMFTP icon and menu, keep running on close, start with Windows."""
import sys
import types
import pytest
from PySide6.QtWidgets import QApplication
from smftp.ui.main_window import MainWindow
from test_audit_known_issues import close_window


@pytest.fixture
def window():
    QApplication.instance() or QApplication([])
    win = MainWindow()
    yield win
    win.quitting = True
    close_window(win)


def test_tray_uses_the_smftp_icon_and_offers_a_menu(window):
    assert not window.tray.icon().isNull()
    labels = [action.text() for action in window.tray.contextMenu().actions() if action.text()]
    assert labels == ["Afficher SMFTP", "Tâches planifiées…", "Quitter SMFTP"]


def test_closing_keeps_smftp_in_the_tray_when_chosen(window, monkeypatch):
    window.show()
    monkeypatch.setattr(window, "keeps_running_in_tray", lambda: True)
    messages = []
    monkeypatch.setattr(window.tray, "showMessage", lambda *args: messages.append(args))
    assert not window.close()
    assert window.isHidden() and window.thread.isRunning() and messages
    window.reveal()
    assert window.isVisible()


def test_quit_really_quits_even_with_the_tray_option(window, monkeypatch):
    window.show()
    monkeypatch.setattr(window, "keeps_running_in_tray", lambda: True)
    window.quit_app()
    assert window.closing or not window.isVisible()


class FakeRegistry:
    """Stands in for winreg: the tests never write to the real Run key."""

    def __init__(self):
        self.values = {}
        self.HKEY_CURRENT_USER = object()
        self.KEY_SET_VALUE = 2
        self.REG_SZ = 1

    def OpenKey(self, *args):
        registry = self

        class Key:
            def __enter__(self):
                return registry

            def __exit__(self, *exc):
                return False
        return Key()

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_start_with_windows_registers_a_minimized_launch(monkeypatch):
    from smftp.utils import autostart
    registry = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\SMFTP\SMFTP.exe")
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "uptodown")
    assert autostart.supported() == (sys.platform == "win32")
    autostart.set_enabled(True)
    assert registry.values["SMFTP"] == '"C:\\Program Files\\SMFTP\\SMFTP.exe" --minimized'
    assert autostart.is_enabled()
    autostart.set_enabled(False)
    assert "SMFTP" not in registry.values and not autostart.is_enabled()


def test_store_build_leaves_startup_to_windows_settings(monkeypatch):
    from smftp.utils import autostart
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "store")
    assert not autostart.supported()


def test_store_package_declares_a_startup_task():
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "tools"))
    import make_msix
    import xml.etree.ElementTree as ET
    root = ET.fromstring(make_msix.manifest("1Smeth.SMFTP", "CN=X", "Smeth", "1.1.0.0").encode("utf-8"))
    task = root.find(".//{http://schemas.microsoft.com/appx/manifest/desktop/windows10}StartupTask")
    assert task is not None and task.get("Enabled") == "false"


def test_preferences_offer_both_options(window):
    from smftp.ui.settings import SettingsDialog
    dialog = SettingsDialog(window.settings, window)
    assert "zone de notification" in dialog.tray.text() and "démarrage de Windows" in dialog.autostart.text()
    dialog.tray.setChecked(True)
    dialog.persist(window.settings)
    assert window.settings.value("tray_on_close", type=bool)
