"""Independent transfer connections with cooperative pause and cancellation."""
from dataclasses import dataclass
from pathlib import Path
import os
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


def valid_remote_name(name: str) -> bool:
    return name not in (".", "..") and not any(c in name for c in "/\\:")


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
        self.total_bytes = None  # Set for directory tasks.
        self.done_bytes = 0
        self.listings = {}
        self.remote_entries = {}

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
            if self.total_bytes is not None:
                self.updated.emit(self.done_bytes + done, self.total_bytes, transferred / elapsed)
            else:
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

    def measure(self, client, local: Path, remote: str) -> int:
        """Total bytes of a directory task; remote listings are cached for the copy."""
        self.checkpoint()
        total = 0
        if self.task.upload:
            for child in local.iterdir():
                if child.is_symlink():
                    continue
                if child.is_dir():
                    total += self.measure(client, child, posixpath.join(remote, child.name))
                else:
                    total += child.stat().st_size
            return total
        entries = client.listdir(remote)
        self.listings[remote] = entries
        for entry in entries:
            if not valid_remote_name(entry.name):
                continue  # Rejected during the copy itself.
            path = posixpath.join(remote, entry.name)
            if entry.is_dir:
                total += self.measure(client, local / entry.name, path)
            else:
                self.remote_entries[path] = entry
                total += entry.size
        return total

    def copy(self, client, local: Path, remote: str, directory: bool):
        self.checkpoint()
        if local.is_symlink():
            raise ValueError("Symbolic links are not followed")
        if not self.task.upload and any(parent.is_symlink() for parent in local.parents):
            raise ValueError("Destination symlink is not followed")
        if directory:
            if self.total_bytes is None:
                # Progress covers the whole task, not only the current file.
                self.total_bytes = self.measure(client, local, remote)
                self.done_bytes = 0
            if self.task.upload:
                existing = client.exists(remote)
                if existing and not existing.is_dir:
                    raise FileExistsError
                if not existing:
                    client.mkdir(remote)
                for child in local.iterdir():
                    self.copy(client, child, posixpath.join(remote, child.name), child.is_dir())
            else:
                local.mkdir(parents=True, exist_ok=True)
                entries = self.listings.pop(remote, None)
                if entries is None:
                    entries = client.listdir(remote)
                for entry in entries:
                    # Never allow server-supplied names to escape the destination.
                    if not valid_remote_name(entry.name):
                        raise ValueError("Invalid remote filename")
                    from smftp.utils.paths import local_child
                    self.copy(client, local_child(local, entry.name), posixpath.join(remote, entry.name), entry.is_dir)
            return
        size = self.copy_file(client, local, remote)
        if self.total_bytes is not None:
            self.done_bytes += size

    def copy_file(self, client, local: Path, remote: str) -> int:
        """Copy one file and return its source size, also counted when skipped."""
        if self.task.upload:
            client.makedirs(posixpath.dirname(remote))
            source = None
            source_size = local.stat().st_size
        else:
            source = self.remote_entries.pop(remote, None) or client.exists(remote)
            if source is None or source.is_dir:
                raise FileNotFoundError(remote)
            source_size = source.size
        existing = client.exists(remote) if self.task.upload else (local.stat() if local.exists() else None)
        offset = 0
        resume = bool(self.task.resume)
        if existing:
            choice = ("overwrite" if self.task.upload and self.task.safe_upload else "resume") if self.task.resume else self.conflict(str(local.name))
            if choice == "skip":
                return source_size
            if choice in ("newer", "different"):
                source_date = local.stat().st_mtime if self.task.upload else source.modified
                target_size = existing.size if self.task.upload else existing.st_size
                target_date = existing.modified if self.task.upload else existing.st_mtime
                if choice == "different" and source_size == target_size:
                    return source_size
                if choice == "newer" and source_date <= target_date + 2:
                    return source_size
            resume = choice == "resume"
            if resume and self.task.upload and not self.task.safe_upload:
                offset = existing.size
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
            self.download(client, local, remote, source, resume)
        return source_size

    def download(self, client, local: Path, remote: str, source, resume: bool):
        """Download into a hidden partial file tied to the remote size and date.

        An existing destination is never trusted as a partial download: only a
        partial created for the same remote path, size and date is resumed. The
        destination is replaced only after a complete download of the right size.
        """
        local.parent.mkdir(parents=True, exist_ok=True)
        stamp = f"{remote}:{source.size}:{source.modified}"
        digest = hashlib.sha256(stamp.encode()).hexdigest()[:16]
        temporary = local.with_name(f".{local.name}.smftp-{digest}.part")
        offset = 0
        if resume and source.modified and temporary.exists() and not temporary.is_symlink():
            partial = temporary.stat().st_size
            if partial <= source.size:
                offset = partial
        self.initial_offset = offset
        client.download(remote, str(temporary), self.progress, offset)
        self.checkpoint()
        if temporary.stat().st_size != source.size:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("La taille téléchargée ne correspond pas au fichier distant, qui a peut-être changé. Le fichier local d’origine est conservé.")
        os.replace(temporary, local)

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
