"""Cancellable local/remote recursive filename search."""
import fnmatch
import os
from pathlib import Path
import posixpath
from PySide6.QtCore import QThread, Signal
from smftp.core.clients import FTPClient, FTPSClient, SFTPClient
from smftp.models.entries import Entry
from smftp.core.errors import describe_error
from smftp.utils.storage import data_dir


class SearchWorker(QThread):
    found = Signal(str, object)
    failed = Signal(object)

    def __init__(self, root, pattern, connection=None, parent=None):
        super().__init__(parent)
        self.root, self.pattern, self.connection = root, pattern.casefold(), connection
        self.count = 0
        self.limit_reached = False
        self.unreadable = 0  # Folders that could not be listed.

    def emit_match(self, path, entry):
        if fnmatch.fnmatch(entry.name.casefold(), self.pattern):
            self.found.emit(path, entry)
            self.count += 1
            if self.count >= 10000:
                self.limit_reached = True
                self.requestInterruption()

    def run(self):
        client = None
        try:
            if self.connection:
                p = self.connection.protocol
                client = SFTPClient(str(data_dir() / "known_hosts")) if p == "SFTP" else (FTPSClient() if p == "FTPS" else FTPClient())
                client.connect(self.connection)
                stack = [self.root]
                while stack and not self.isInterruptionRequested():
                    root = stack.pop()
                    try:
                        items = client.listdir(root)
                    except Exception:
                        if root == self.root:
                            raise
                        self.unreadable += 1
                        continue
                    for item in items:
                        if item.name in (".", "..") or any(c in item.name for c in "/\\:"):
                            continue
                        path = posixpath.join(root, item.name)
                        self.emit_match(path, item)
                        if item.is_dir and not item.permissions.startswith("l"):
                            stack.append(path)
            else:
                def denied(error):
                    self.unreadable += 1
                for root, directories, files in os.walk(self.root, onerror=denied, followlinks=False):
                    if self.isInterruptionRequested():
                        break
                    directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
                    for name in directories + files:
                        path = Path(root) / name
                        try:
                            info = path.lstat()
                            self.emit_match(str(path), Entry(name, path.is_dir(), info.st_size, info.st_mtime))
                        except OSError:
                            continue
        except Exception as error:
            self.failed.emit(describe_error(error, self.connection, "Recherche récursive"))
        finally:
            if client:
                client.close()
