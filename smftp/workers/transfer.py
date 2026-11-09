"""Independent transfer connections with cooperative pause and cancellation."""
from dataclasses import dataclass
from pathlib import Path
import posixpath
from threading import Event
import time
import hashlib
from PySide6.QtCore import QThread, Signal
from smftp.core.clients import FTPClient, FTPSClient, SFTPClient
from smftp.models.entries import Connection
from smftp.utils.storage import data_dir


@dataclass
class Transfer:
    connection: Connection
    local: str
    remote: str
    upload: bool
    directory: bool = False
    resume: bool = False
    conflict: str = "ask"
    safe_upload: bool = True


class Cancelled(Exception):
    pass


class TransferWorker(QThread):
    activity = Signal(str)
    failed = Signal(object)
    updated = Signal(object, object, float)
    result = Signal(str)
    conflict_found = Signal(str)

    def __init__(self, task: Transfer, parent=None):
        super().__init__(parent)
        self.task = task
        self.cancelled = Event()
        self.running = Event()
        self.running.set()
        self.answer = Event()
        self.choice = "skip"
        self.speed_limit = 0
        self.started_at = 0
        self.last_update = 0
        self.initial_offset = 0

    def cancel(self):
        self.cancelled.set()
        self.running.set()
        self.answer.set()

    def report_activity(self, message):
        from smftp.core.errors import redact
        prefix = f"[Transfert {self.task.connection.protocol}] "
        self.activity.emit(redact(prefix + message, (self.task.connection.password,)))

    def checkpoint(self):
        while not self.running.wait(0.1):
            if self.cancelled.is_set():
                raise Cancelled
        if self.cancelled.is_set():
            raise Cancelled

    def progress(self, done, total):
        self.checkpoint()
        elapsed = max(time.monotonic() - self.started_at, 0.001)
        transferred = max(0, done - self.initial_offset)
        if self.speed_limit:
            delay = transferred / self.speed_limit - elapsed
            while delay > 0:
                if self.cancelled.wait(min(delay, 0.1)):
                    raise Cancelled
                self.checkpoint()
                delay = transferred / self.speed_limit - (time.monotonic() - self.started_at)
        now = time.monotonic()
        if now - self.last_update > 0.12 or done == total:
            self.updated.emit(done, total, transferred / elapsed)
            self.last_update = now

    def conflict(self, name):
        if self.task.conflict != "ask":
            return self.task.conflict
        self.answer.clear()
        self.conflict_found.emit(name)
        while not self.answer.wait(0.1):
            self.checkpoint()
        self.checkpoint()
        return self.choice

    def copy(self, client, local: Path, remote: str, directory: bool):
        self.checkpoint()
        if local.is_symlink():
            raise ValueError("Symbolic links are not followed")
        if not self.task.upload and any(parent.is_symlink() for parent in local.parents):
            raise ValueError("Destination symlink is not followed")
        if directory:
            if self.task.upload:
                if local.is_symlink():
                    raise ValueError("Symbolic links are not followed")
                existing = client.exists(remote)
                if existing and not existing.is_dir:
                    raise FileExistsError
                if not existing:
                    client.mkdir(remote)
                for child in local.iterdir():
                    self.copy(client, child, posixpath.join(remote, child.name), child.is_dir())
            else:
                local.mkdir(parents=True, exist_ok=True)
                for entry in client.listdir(remote):
                    # Never allow server-supplied names to escape the destination.
                    if entry.name in (".", "..") or "/" in entry.name or "\\" in entry.name or ":" in entry.name:
                        raise ValueError("Invalid remote filename")
                    from smftp.utils.paths import local_child
                    self.copy(client, local_child(local, entry.name), posixpath.join(remote, entry.name), entry.is_dir)
            return
        if local.is_symlink():
            raise ValueError("Symbolic links are not followed")
        if self.task.upload:
            client.makedirs(posixpath.dirname(remote))
        existing = client.exists(remote) if self.task.upload else (local.stat() if local.exists() else None)
        offset = 0
        if existing:
            choice = ("overwrite" if self.task.upload and self.task.safe_upload else "resume") if self.task.resume else self.conflict(str(local.name))
            if choice == "skip":
                return
            if choice in ("newer", "different"):
                source = local.stat() if self.task.upload else client.exists(remote)
                source_size = source.st_size if self.task.upload else source.size
                source_date = source.st_mtime if self.task.upload else source.modified
                target_size = existing.size if self.task.upload else existing.st_size
                target_date = existing.modified if self.task.upload else existing.st_mtime
                if choice == "different" and source_size == target_size:
                    return
                if choice == "newer" and source_date <= target_date + 2:
                    return
            if choice == "resume" and not (self.task.upload and self.task.safe_upload):
                offset = existing.size if self.task.upload else existing.st_size
                source_size = local.stat().st_size if self.task.upload else client.exists(remote).size
                if offset > source_size:
                    raise ValueError("Destination exceeds source size")
            if choice == "rename":
                stamp = time.time_ns()
                if self.task.upload:
                    remote += f".copie-{stamp}"
                else:
                    local = local.with_name(f"{local.stem}.copie-{stamp}{local.suffix}")
        self.started_at = time.monotonic()
        self.initial_offset = offset
        if self.task.upload:
            if self.task.safe_upload:
                info = local.stat()
                stamp = f"{local.resolve()}:{info.st_size}:{info.st_mtime_ns}"
                digest = hashlib.sha256(stamp.encode()).hexdigest()[:16]
                temporary = posixpath.join(posixpath.dirname(remote), f".{posixpath.basename(remote)}.smftp-{digest}.part")
                offset = 0
                partial = client.exists(temporary) if self.task.resume else None
                if partial and partial.size <= local.stat().st_size:
                    offset = partial.size
                self.initial_offset = offset
                client.upload(str(local), temporary, self.progress, offset)
                self.checkpoint()
                latest = local.stat()
                uploaded = client.exists(temporary)
                if (latest.st_size, latest.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                    raise RuntimeError("Le fichier local a changé pendant l’envoi. Le fichier distant d’origine est conservé.")
                if uploaded is None or uploaded.size != info.st_size:
                    raise RuntimeError("La taille du fichier temporaire distant ne correspond pas à la source. Publication annulée.")
                client.replace(temporary, remote)
            else:
                client.upload(str(local), remote, self.progress, offset)
        else:
            local.parent.mkdir(parents=True, exist_ok=True)
            client.download(remote, str(local), self.progress, offset)

    def run(self):
        client = None
        try:
            self.checkpoint()
            protocol = self.task.connection.protocol
            client = SFTPClient(str(data_dir() / "known_hosts"), self.report_activity) if protocol == "SFTP" else (FTPSClient(self.report_activity) if protocol == "FTPS" else FTPClient(self.report_activity))
            client.connect(self.task.connection)
            self.copy(client, Path(self.task.local), self.task.remote, self.task.directory)
            self.report_activity("Statut : transfert terminé.")
            self.result.emit("Terminé")
        except Cancelled:
            self.report_activity("Statut : transfert annulé.")
            self.result.emit("Annulé")
        except Exception as error:
            from smftp.core.errors import describe_error
            self.failed.emit(describe_error(error, self.task.connection, "Téléversement" if self.task.upload else "Téléchargement"))
            self.result.emit("Échoué")
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass
