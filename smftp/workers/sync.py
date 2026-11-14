"""Read-only recursive synchronization planning."""
from pathlib import Path
import posixpath
from PySide6.QtCore import QThread, Signal
from smftp.core.clients import FTPClient, FTPSClient, SFTPClient
from smftp.utils.storage import data_dir


class SyncPlanner(QThread):
    planned = Signal(object)
    failed = Signal(str)

    def __init__(self, connection, local, remote, upload, parent=None):
        super().__init__(parent)
        self.connection, self.local, self.remote, self.upload = connection, Path(local), remote, upload

    def run(self):
        client = None
        try:
            p = self.connection.protocol
            client = SFTPClient(str(data_dir() / "known_hosts")) if p == "SFTP" else (FTPSClient() if p == "FTPS" else FTPClient())
            client.connect(self.connection)
            local_files = {}
            for path in self.local.rglob("*"):
                if self.isInterruptionRequested():
                    return
                if path.is_file() and not path.is_symlink() and not any(p.is_symlink() for p in path.parents):
                    info = path.stat()
                    local_files[path.relative_to(self.local).as_posix()] = (info.st_size, info.st_mtime)
            remote_files = {}
            def visit(path, relative=""):
                if self.isInterruptionRequested():
                    return
                for item in client.listdir(path):
                    if item.name in (".", "..") or any(c in item.name for c in "/\\:"):
                        raise ValueError
                    key = posixpath.join(relative, item.name)
                    if item.is_dir:
                        visit(posixpath.join(path, item.name), key)
                    elif not item.permissions.startswith("l"):
                        remote_files[key] = (item.size, item.modified)
            visit(self.remote)
            source, destination = (local_files, remote_files) if self.upload else (remote_files, local_files)
            plan = []
            for path, metadata in source.items():
                other = destination.get(path)
                if other is None or metadata[0] != other[0] or metadata[1] > other[1] + 2:
                    # The destination metadata lets the copy detect later changes.
                    plan.append((path, "Nouveau" if other is None else "Modifié", metadata[0], other))
            self.planned.emit(plan)
        except Exception:
            self.failed.emit("Impossible de comparer les dossiers. Vérifiez la connexion et les droits d’accès.")
        finally:
            if client:
                client.close()
