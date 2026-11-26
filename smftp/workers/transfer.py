"""Independent transfer connections with cooperative pause and cancellation."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import posixpath
from threading import Event, Lock
import time
import hashlib
from PySide6.QtCore import QThread, Signal
from smftp.core.clients import make_client
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
    # Synchronization: destination metadata seen when the plan was built. A
    # destination that still matches is overwritten; any other one is asked.
    planned: bool = False
    expected: tuple | None = None
    # Directory tasks: remote path -> [source size, source date, destination size,
    # destination date or None] of each file already copied, so a resume skips it.
    completed: dict = field(default_factory=dict)


def valid_remote_name(name: str) -> bool:
    return name not in (".", "..") and not any(c in name for c in "/\\:")


class RateLimiter:
    """Bandwidth shared by every transfer of the queue (0 = unlimited)."""

    def __init__(self, rate: float = 0):
        self.lock = Lock()
        self.rate = rate
        self.next_free = 0.0

    def set_rate(self, rate: float) -> None:
        with self.lock:
            self.rate = max(0, rate)
            self.next_free = 0.0

    def reserve(self, amount: int) -> float:
        """Account for bytes already moved; return how long the caller must wait."""
        with self.lock:
            if self.rate <= 0 or amount <= 0:
                return 0.0
            now = time.monotonic()
            # Unused time is not banked beyond a short burst.
            start = max(self.next_free, now - 0.25)
            self.next_free = start + amount / self.rate
            return max(0.0, self.next_free - now)


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
        self.speed_limit = 0  # Per-transfer limit, kept for compatibility.
        self.limiter = None  # Shared RateLimiter of the queue.
        self.previous_done = None
        self.started_at = 0
        self.last_update = 0
        self.initial_offset = 0
        self.total_bytes = None  # Set for directory tasks.
        self.done_bytes = 0
        self.listings = {}
        self.remote_entries = {}
        self.verify = True  # Compare a content fingerprint after each file.
        self.hash_warned = False

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
        if self.limiter is not None:
            previous = self.initial_offset if self.previous_done is None else self.previous_done
            self.previous_done = done
            delay = self.limiter.reserve(done - previous)
            deadline = time.monotonic() + delay
            while delay > 0:
                if self.cancelled.wait(min(delay, 0.1)):
                    raise Cancelled
                self.checkpoint()
                delay = deadline - time.monotonic()
        elif self.speed_limit:
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
            from smftp.workers.sync import is_junction
            for child in local.iterdir():
                if child.is_symlink() or is_junction(child):
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
            raise ValueError("Les liens symboliques ne sont pas suivis : choisissez directement le fichier ou le dossier cible.")
        if not self.task.upload and any(parent.is_symlink() for parent in local.parents):
            raise ValueError("Le dossier de destination passe par un lien symbolique, qui n’est pas suivi par sécurité.")
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
                    from smftp.workers.sync import is_junction
                    if child.is_symlink() or is_junction(child):
                        # Their target may lie outside the folder: skipped, not fatal.
                        self.report_activity(f"Statut : lien symbolique ou jonction ignoré : {child}")
                        continue
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

    def matches_plan(self, existing) -> bool:
        size, modified = self.task.expected
        if self.task.upload:
            return (existing.size, existing.modified) == (size, modified)
        return existing.st_size == size and abs(existing.st_mtime - modified) < 0.001

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
        if self.already_completed(local, remote, source, existing):
            self.report_activity(f"Statut : déjà copié lors d’une tentative précédente, ignoré : {local.name}")
            return source_size
        offset = 0
        resume = bool(self.task.resume)
        if existing:
            if self.task.planned and self.task.expected is not None and self.matches_plan(existing):
                choice = "overwrite"
            elif self.task.planned:
                # Created or changed since the comparison: never overwrite silently.
                choice = self.conflict(str(local.name)) if self.task.conflict == "ask" else self.task.conflict
            else:
                # A resumed or restored task only resumes its own hidden partial file.
                # An existing destination is the user's data: the conflict rule applies.
                choice = self.conflict(str(local.name))
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
            # The download partial is tied to the remote identity: an approved
            # overwrite may still continue it.
            resume = choice == "resume" or (choice == "overwrite" and bool(self.task.resume))
            if choice == "resume" and self.task.upload and not self.task.safe_upload:
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
        self.previous_done = None
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
                if not self.verify_content(client, local, temporary):
                    try:
                        client.unlink(temporary)
                    except Exception:
                        pass
                    raise RuntimeError("L’empreinte du fichier envoyé ne correspond pas à la source. "
                                       "Le fichier distant d’origine est conservé ; le temporaire a été supprimé.")
                client.replace(temporary, remote)
            else:
                client.upload(str(local), remote, self.progress, offset)
                if not self.verify_content(client, local, remote):
                    raise RuntimeError("L’empreinte du fichier distant ne correspond pas à la source : renvoyez-le.")
        else:
            self.download(client, local, remote, source, resume)
        self.record_completed(local, remote, source)
        return source_size

    def verify_content(self, client, local: Path, remote: str, name: str = "") -> bool:
        """True when fingerprints match or cannot be computed by the server."""
        if not self.verify:
            return True
        self.checkpoint()
        result = client.checksum(remote)
        if result is None:
            if not self.hash_warned:
                self.hash_warned = True
                self.report_activity("Statut : le serveur ne calcule pas d’empreinte ; seule la taille est vérifiée.")
            return True
        from smftp.core.checksum import local_digest, LABELS
        algorithm, digest = result
        name = name or local.name
        if local_digest(local, algorithm, self.checkpoint) != digest:
            self.report_activity(f"Erreur : empreinte {LABELS[algorithm]} différente pour {name}.")
            return False
        self.report_activity(f"Statut : empreinte {LABELS[algorithm]} vérifiée : {name}")
        return True

    def source_signature(self, local: Path, source) -> list:
        if self.task.upload:
            info = local.stat()
            return [info.st_size, info.st_mtime]
        return [source.size, source.modified]

    def record_completed(self, local: Path, remote: str, source) -> None:
        if not self.task.directory:
            return
        try:
            signature = self.source_signature(local, source)
            if self.task.upload:
                signature += [signature[0], None]
            else:
                info = local.stat()
                signature += [info.st_size, info.st_mtime_ns]
            self.task.completed[remote] = signature
        except OSError:
            pass

    def already_completed(self, local: Path, remote: str, source, existing) -> bool:
        """A resumed directory skips files finished earlier that are still unchanged on both sides."""
        record = self.task.completed.get(remote) if self.task.resume and self.task.directory else None
        if not record or existing is None:
            return False
        try:
            if self.source_signature(local, source) != record[:2]:
                return False
        except OSError:
            return False
        if self.task.upload:
            return not existing.is_dir and existing.size == record[2] and (record[3] is None or existing.modified == record[3])
        return local.is_file() and existing.st_size == record[2] and existing.st_mtime_ns == record[3]

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
        if not self.verify_content(client, temporary, remote, local.name):
            temporary.unlink(missing_ok=True)
            raise RuntimeError("L’empreinte du fichier téléchargé ne correspond pas au fichier distant. "
                               "Le fichier local d’origine est conservé ; relancez le téléchargement.")
        os.replace(temporary, local)

    def run(self):
        client = None
        try:
            self.checkpoint()
            protocol = self.task.connection.protocol
            client = make_client(protocol, str(data_dir() / "known_hosts"), self.report_activity)
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
