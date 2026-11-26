"""Update system: daily check, ignored version, verified download, installer launch.

The network is simulated (updates._urlopen): no test contacts GitHub.
"""
import hashlib
import io
import json
import urllib.error
import urllib.request
from datetime import date, timedelta

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from smftp.workers import updates
from tests.test_audit_known_issues import close_window

TAG = "v9.1.0"
FOLDER = f"https://github.com/Smeth/smftp/releases/download/{TAG}/"
SETUP = "SMFTP-9.1.0-setup-x64.exe"
INSTALLER = b"MZ" + b"installer-bytes" * 5000


class FakeResponse(io.BytesIO):
    def __init__(self, data, url, final=None):
        super().__init__(data)
        self.url, self.final = url, final or url
        self.headers = {"Content-Length": str(len(data))}

    def geturl(self):
        return self.final


class FakeNetwork:
    """URL -> bytes (or (bytes, final URL after redirection)); records every request."""

    def __init__(self, routes):
        self.routes, self.requested = routes, []

    def __call__(self, request, timeout):
        url = request.full_url if isinstance(request, urllib.request.Request) else request
        self.requested.append(url)
        if url not in self.routes:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        value = self.routes[url]
        data, final = value if isinstance(value, tuple) else (value, None)
        return FakeResponse(data, url, final)


def release_json(tag=TAG, assets=None, body="## Nouveautés\n\n**Proxy** et [signets](https://example.org)."):
    if assets is None:
        assets = [{"name": SETUP, "browser_download_url": FOLDER + SETUP, "size": len(INSTALLER)},
                  {"name": "SHA256SUMS.txt", "browser_download_url": FOLDER + "SHA256SUMS.txt", "size": 100},
                  {"name": "evil.exe", "browser_download_url": "https://evil.example/evil.exe", "size": 1}]
    return json.dumps({"tag_name": tag, "html_url": f"https://github.com/Smeth/smftp/releases/tag/{tag}",
                       "body": body, "assets": assets}).encode("utf-8")


def sums_for(data, name=SETUP):
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n{'0' * 64}  SMFTP-9.1.0-windows-x64.zip\n".encode()


@pytest.fixture
def network(monkeypatch):
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "github")
    fake = FakeNetwork({updates.RELEASES_API: release_json(), FOLDER + "SHA256SUMS.txt": sums_for(INSTALLER),
                        FOLDER + SETUP: (INSTALLER, "https://release-assets.githubusercontent.com/x/setup.exe?sig=1")})
    monkeypatch.setattr(updates, "_urlopen", fake)
    return fake


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "updates.ini"), QSettings.Format.IniFormat)


# ---------- release check ----------

def test_new_version_is_found_with_plain_notes_and_github_assets_only(network):
    result = updates.fetch_latest_release()
    assert result["status"] == "update" and result["version"] == "9.1.0" and result["tag"] == TAG
    assert set(result["assets"]) == {SETUP, "SHA256SUMS.txt"}  # the evil.example asset is dropped
    assert "##" not in result["notes"] and "**" not in result["notes"]
    assert "Proxy" in result["notes"] and "signets (https://example.org)" in result["notes"]
    assert network.requested == [updates.RELEASES_API]


def test_same_version_is_current_and_notes_are_limited(network):
    network.routes[updates.RELEASES_API] = release_json(tag="v" + updates.CURRENT_VERSION)
    assert updates.fetch_latest_release()["status"] == "current"
    assert len(updates.release_notes_text("x" * 50_000)) <= updates.NOTES_LIMIT + 2


def test_oversized_api_answer_is_refused(network):
    network.routes[updates.RELEASES_API] = b"{" + b" " * (updates.API_LIMIT + 10) + b"}"
    with pytest.raises(updates.UpdateError):
        updates.fetch_latest_release()


def test_store_build_sends_no_request(monkeypatch, settings):
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "store")
    monkeypatch.setattr(updates, "_urlopen", lambda *a, **k: pytest.fail("requête envoyée par la version Store"))
    assert updates.fetch_latest_release() == {"status": "disabled"}
    assert not updates.automatic_check_due(settings)
    window = new_window()
    try:
        assert window.auto_check_for_updates() is False
        assert getattr(window, "update_worker", None) is None
    finally:
        close_window(window)


def test_automatic_check_runs_once_a_day(settings, monkeypatch):
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "github")
    today = date(2026, 11, 26)
    assert updates.automatic_check_due(settings, today)
    updates.record_check(settings, today)
    assert not updates.automatic_check_due(settings, today)
    assert updates.automatic_check_due(settings, today + timedelta(days=1))
    settings.setValue(updates.AUTO_CHECK_KEY, False)  # Preference unticked.
    assert not updates.automatic_check_due(settings, today + timedelta(days=1))


def test_window_starts_the_automatic_check_only_once_a_day(monkeypatch):
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "github")
    started = []
    monkeypatch.setattr(updates.UpdateWorker, "start", lambda self: started.append(self.automatic))
    monkeypatch.setattr(updates.UpdateWorker, "isRunning", lambda self: False)
    window = new_window()
    try:
        assert window.auto_check_for_updates() is True
        assert window.auto_check_for_updates() is False
        assert started == [True]
    finally:
        close_window(window)


# ---------- notification and ignored version ----------

def update_result(version="9.1.0", automatic=True):
    return {"status": "update", "version": version, "tag": "v" + version, "automatic": automatic,
            "url": "https://github.com/Smeth/smftp/releases/tag/v" + version, "notes": "Notes", "assets": {}}


def test_automatic_result_is_a_non_blocking_notice_unless_ignored(monkeypatch):
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "github")
    from PySide6.QtWidgets import QMessageBox, QDialog
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: pytest.fail("boîte modale au démarrage"))
    monkeypatch.setattr(QDialog, "exec", lambda self: pytest.fail("boîte modale au démarrage"))
    window = new_window()
    try:
        window._show_update_result(update_result())
        assert window.pending_update["version"] == "9.1.0"
        assert not window.update_notice_button.isHidden() and window.update_notice_action.isVisible()
        assert "9.1.0" in window.update_notice_button.text()
        window.apply_update_choice(window.pending_update, "ignore")
        assert window.pending_update is None and window.update_notice_button.isHidden()
        assert updates.is_ignored(window.settings, "v9.1.0")
        window._show_update_result(update_result())  # The same version is no longer offered.
        assert window.pending_update is None and window.update_notice_button.isHidden()
        window._show_update_result(update_result("9.2.0"))  # A newer one is.
        assert window.pending_update["version"] == "9.2.0"
    finally:
        close_window(window)


def test_update_dialog_offers_install_only_to_installed_copies(monkeypatch, tmp_path):
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "github")
    window = new_window()
    try:
        monkeypatch.setattr(updates, "can_install_update", lambda: False)
        dialog = window.build_update_dialog(update_result(automatic=False))
        assert set(dialog.buttons) == {"page", "ignore", "later"}
        assert dialog.notes.toPlainText() == "Notes"
        monkeypatch.setattr(updates, "can_install_update", lambda: True)
        dialog = window.build_update_dialog(update_result(automatic=False))
        assert set(dialog.buttons) == {"install", "page", "ignore", "later"}
        dialog.buttons["later"].click()
        assert dialog.choice == "later"
    finally:
        close_window(window)
    exe = tmp_path / "SMFTP.exe"
    exe.write_bytes(b"")
    assert not updates.installed_by_installer(str(exe))
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert updates.installed_by_installer(str(exe))


def test_update_preference_is_on_by_default_and_saved(monkeypatch, settings):
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "github")
    QApplication.instance() or QApplication([])
    from smftp.ui.settings import SettingsDialog
    dialog = SettingsDialog(settings)
    assert dialog.auto_updates.isChecked() and dialog.auto_updates.isEnabled()
    dialog.auto_updates.setChecked(False)
    dialog.persist(settings)
    assert settings.value(updates.AUTO_CHECK_KEY, True, type=bool) is False
    monkeypatch.setenv("SMFTP_DISTRIBUTION", "store")
    assert not SettingsDialog(settings).auto_updates.isEnabled()


# ---------- download and verification ----------

def test_verified_download_follows_github_redirect(network, tmp_path):
    result = updates.fetch_latest_release()
    seen = []
    path, sha = updates.download_update(result, tmp_path, lambda done, total: seen.append((done, total)))
    assert open(path, "rb").read() == INSTALLER and sha == hashlib.sha256(INSTALLER).hexdigest()
    assert seen[-1] == (len(INSTALLER), len(INSTALLER))


def test_wrong_fingerprint_is_refused_and_file_deleted(network, tmp_path):
    network.routes[FOLDER + "SHA256SUMS.txt"] = sums_for(b"another file")
    with pytest.raises(updates.UpdateError, match="SHA-256"):
        updates.download_update(updates.fetch_latest_release(), tmp_path)
    assert not (tmp_path / SETUP).exists()


def test_missing_fingerprints_are_refused(network, tmp_path):
    result = updates.fetch_latest_release()
    del result["assets"]["SHA256SUMS.txt"]
    with pytest.raises(updates.UpdateError, match="SHA256SUMS"):
        updates.download_update(result, tmp_path)
    network.routes[FOLDER + "SHA256SUMS.txt"] = sums_for(INSTALLER, name="autre.exe")
    with pytest.raises(updates.UpdateError):
        updates.download_update(updates.fetch_latest_release(), tmp_path)
    assert FOLDER + SETUP not in network.requested  # Nothing downloaded without a fingerprint.


def test_urls_outside_github_are_refused(network, tmp_path):
    assert updates.is_release_asset_url(FOLDER + SETUP)
    for url in ("http://github.com/Smeth/smftp/releases/download/v9.1.0/x.exe",
                "https://github.com/Other/smftp/releases/download/v9.1.0/x.exe",
                "https://github.com.evil.example/Smeth/smftp/releases/download/x.exe",
                "https://evil.example/Smeth/smftp/releases/download/x.exe"):
        assert not updates.is_release_asset_url(url), url
    assert updates.is_allowed_redirect("https://objects.githubusercontent.com/a")
    assert not updates.is_allowed_redirect("http://objects.githubusercontent.com/a")
    assert not updates.is_allowed_redirect("https://objects.githubusercontent.com.evil.example/a")
    # Redirection handler: GitHub's file servers only.
    handler = updates._RestrictedRedirect()
    request = urllib.request.Request(FOLDER + SETUP)
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/setup.exe")
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/s")
    # A response that ended up elsewhere is refused.
    network.routes[FOLDER + SETUP] = (INSTALLER, "https://evil.example/setup.exe")
    with pytest.raises(updates.UpdateError):
        updates.download_update(updates.fetch_latest_release(), tmp_path)
    # Assets of the API answer that point outside the project are ignored.
    result = updates.fetch_latest_release()
    result["assets"][SETUP]["url"] = "https://evil.example/" + SETUP
    with pytest.raises(updates.UpdateError):
        updates.release_files(result)


def test_cancelled_download_leaves_no_file(network, tmp_path):
    with pytest.raises(updates.UpdateError):
        updates.download_update(updates.fetch_latest_release(), tmp_path, cancelled=lambda: True)
    assert not (tmp_path / SETUP).exists()


# ---------- installer launch ----------

def test_installer_is_launched_only_when_verified(monkeypatch, tmp_path):
    launched = []
    monkeypatch.setattr(updates, "_spawn", launched.append)
    setup = tmp_path / SETUP
    setup.write_bytes(INSTALLER)
    good = hashlib.sha256(INSTALLER).hexdigest()
    with pytest.raises(updates.UpdateError):
        updates.launch_installer(setup, "0" * 64)
    with pytest.raises(updates.UpdateError):
        updates.launch_installer(setup, "")
    with pytest.raises(updates.UpdateError):
        updates.launch_installer(tmp_path / "absent.exe", good)
    assert launched == []
    updates.launch_installer(setup, good)
    assert launched == [[str(setup), "/SP-"]]
    # Replaced after the check of the download: refused.
    setup.write_bytes(INSTALLER + b"!")
    with pytest.raises(updates.UpdateError):
        updates.launch_installer(setup, good)
    assert len(launched) == 1


def test_window_launches_the_pending_installer_once(monkeypatch, tmp_path):
    launched = []
    monkeypatch.setattr(updates, "_spawn", launched.append)
    setup = tmp_path / SETUP
    setup.write_bytes(INSTALLER)
    window = new_window()
    try:
        window.pending_installer = (str(setup), "f" * 64)
        assert window.launch_pending_installer() is False and launched == []
        window.pending_installer = (str(setup), hashlib.sha256(INSTALLER).hexdigest())
        assert window.launch_pending_installer() is True
        assert window.launch_pending_installer() is False
        assert launched == [[str(setup), "/SP-"]]
    finally:
        close_window(window)


def test_sha256sums_parsing():
    text = f"{'A' * 64}  SMFTP-1.2.0-setup-x64.exe\r\n{'b' * 64} *SMFTP-1.2.0-windows-x64.zip\nbad line\n"
    assert updates.parse_sha256sums(text) == {"SMFTP-1.2.0-setup-x64.exe": "a" * 64,
                                              "SMFTP-1.2.0-windows-x64.zip": "b" * 64}


def test_instance_mutex_and_installer_settings():
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    iss = (root / "installer" / "smftp.iss").read_text(encoding="utf-8")
    assert "AppMutex=SMFTP-Instance" in iss and "{{6F1C9E52-8B0D-4C47-A3E5-5D2B7A9C1E04}" in iss
    assert "SHA256SUMS.txt" in (root / "package.ps1").read_text(encoding="utf-8")
    import main
    handle = main.create_instance_mutex()
    if sys.platform == "win32":
        assert handle
        import ctypes
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
    else:
        assert handle is None


def new_window():
    from smftp.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    return MainWindow()
