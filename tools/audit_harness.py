"""Disposable native UI audit sandbox. Never reads user profiles or OS secrets.

Run with the project Python environment. The FTP server listens on loopback only.
All files and settings live under .audit-runtime (excluded from version control).
"""
from pathlib import Path
import os
import sys
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATE = ROOT / ".audit-runtime"
STATE.mkdir(exist_ok=True)
os.environ["SMFTP_DATA_DIR"] = str(STATE / "state")

import keyring
from keyring.backend import KeyringBackend
from PySide6.QtCore import QSettings, QTranslator, QLibraryInfo
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
import smftp.ui.main_window as module
from smftp.models.entries import Connection
from smftp.utils.profiles import profile_for
from smftp.utils.storage import save_sites


class AuditVault(KeyringBackend):
    priority = 1
    secrets = {}

    def get_password(self, service, username):
        return self.secrets.get((service, username))

    def set_password(self, service, username, password):
        self.secrets[service, username] = password

    def delete_password(self, service, username):
        self.secrets.pop((service, username), None)


def main():
    local, remote = STATE / "local", STATE / "remote"
    for path in (local, remote):
        (path / "assets").mkdir(parents=True, exist_ok=True)
        (path / "index.html").write_text("<!doctype html>\n<h1>Audit SMFTP</h1>\n", encoding="utf-8")
        (path / "notes.txt").write_text("Disposable audit data.\n", encoding="utf-8")
        (path / ".hidden").write_text("hidden audit fixture", encoding="utf-8")
    authorizer = DummyAuthorizer()
    authorizer.add_user("audit", "audit-only", str(remote), perm="elradfmwMT")

    class Handler(FTPHandler):
        pass

    Handler.authorizer = authorizer
    server = FTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"timeout": 0.05}, daemon=True)
    thread.start()
    keyring.set_keyring(AuditVault())
    profile = Connection("127.0.0.1", "audit", "FTP", server.socket.getsockname()[1],
                         "audit-only", remember=True, name="Serveur local d’audit", group="Audit",
                         local_directory=str(local), remote_directory="/")
    keyring.set_password("SMFTP", profile.identity, profile.password)
    save_sites([profile_for(profile)])
    module.QSettings = lambda: QSettings(str(STATE / "settings.ini"), QSettings.Format.IniFormat)
    app = QApplication([])
    app.setApplicationName("SMFTP Audit")
    translator = QTranslator(app)
    if translator.load("qtbase_fr", str(ROOT / "resources" / "translations")):
        app.installTranslator(translator)
    image = QImage(160, 100, QImage.Format.Format_RGB32)
    image.fill(QColor("#5788f3"))
    image.save(str(remote / "image.png"))
    window = module.MainWindow()
    window.setWindowTitle("SMFTP — Audit isolé")
    window.local.navigate(str(local))
    window.show()
    print("Isolated audit app started; no real profiles or credentials are used.", flush=True)
    try:
        return app.exec()
    finally:
        server.close_all()
        thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
