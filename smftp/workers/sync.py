"""Recursive synchronization: read-only planning, then optional mirror deletions."""
from pathlib import Path
import os
import posixpath
from PySide6.QtCore import QThread, Signal
from smftp.core.clients import make_client
from smftp.core.checksum import local_digest
from smftp.utils.storage import data_dir

# Plan row states. Copies keep (path, state, size, destination metadata).
NEW, MODIFIED, CONTENT, DELETE_FILE, DELETE_DIR = "Nouveau", "Modifié", "Contenu différent", "À supprimer", "Dossier à supprimer"
DELETIONS = (DELETE_FILE, DELETE_DIR)


class SyncPlanner(QThread):
    planned = Signal(object)
    failed = Signal(str)
    note = Signal(str)

    def __init__(self, connection, local, remote, upload, parent=None, *, compare_content=False, mirror=False):
        super().__init__(parent)
        self.connection, self.local, self.remote, self.upload = connection, Path(local), remote, upload
        self.compare_content, self.mirror = compare_content, mirror

    def run(self):
        client = None
        try:
            client = make_client(self.connection.protocol, str(data_dir() / "known_hosts"))
            client.connect(self.connection)
            local_files, local_dirs = {}, set()
            for path in self.local.rglob("*"):
                if self.isInterruptionRequested():
                    return
                if path.is_symlink() or any(p.is_symlink() for p in path.parents):
                    continue
                key = path.relative_to(self.local).as_posix()
                if path.is_file():
                    info = path.stat()
                    local_files[key] = (info.st_size, info.st_mtime)
                elif path.is_dir():
                    local_dirs.add(key)
            remote_files, remote_dirs = {}, set()

            def visit(path, relative=""):
                if self.isInterruptionRequested():
                    return
                for item in client.listdir(path):
                    if item.name in (".", "..") or any(c in item.name for c in "/\\:"):
                        raise ValueError
                    key = posixpath.join(relative, item.name)
                    if item.is_dir:
                        remote_dirs.add(key)
                        visit(posixpath.join(path, item.name), key)
                    elif not item.permissions.startswith("l"):
                        remote_files[key] = (item.size, item.modified)
            visit(self.remote)
            if self.isInterruptionRequested():
                return
            source, destination = (local_files, remote_files) if self.upload else (remote_files, local_files)
            source_dirs, destination_dirs = (local_dirs, remote_dirs) if self.upload else (remote_dirs, local_dirs)
            hashing = self.compare_content
            plan = []
            for path, metadata in sorted(source.items()):
                if self.isInterruptionRequested():
                    return
                other = destination.get(path)
                if other is None:
                    state = NEW
                elif metadata[0] != other[0]:
                    state = MODIFIED
                elif hashing:
                    same = self.same_content(client, path)
                    if same is None:
                        hashing = False
                        self.note.emit("Synchronisation : le serveur ne calcule pas d’empreinte ; comparaison par taille et date uniquement.")
                        state = MODIFIED if metadata[1] > other[1] + 2 else None
                    else:
                        state = None if same else CONTENT
                else:
                    state = MODIFIED if metadata[1] > other[1] + 2 else None
                if state:
                    # The destination metadata lets the copy detect later changes.
                    plan.append((path, state, metadata[0], other))
            if self.mirror:
                # Files first, then folders deepest first: a folder is only removed once empty.
                for path, metadata in sorted(destination.items()):
                    if path not in source:
                        plan.append((path, DELETE_FILE, metadata[0], metadata))
                for path in sorted(destination_dirs - source_dirs, key=lambda p: (-p.count("/"), p)):
                    if path not in source:
                        plan.append((path, DELETE_DIR, 0, None))
            self.planned.emit(plan)
        except Exception:
            self.failed.emit("Impossible de comparer les dossiers. Vérifiez la connexion et les droits d’accès.")
        finally:
            if client:
                client.close()

    def same_content(self, client, relative):
        remote = client.checksum(posixpath.join(self.remote, relative))
        if remote is None:
            return None
        algorithm, digest = remote
        local = self.local.joinpath(*relative.split("/"))
        return local_digest(local, algorithm) == digest


class SyncDeleter(QThread):
    """Deletes mirror extras, keeping anything changed since the comparison."""
    report = Signal(str)
    done = Signal(int, int)  # deleted, kept

    def __init__(self, connection, local, remote, remote_side, rows, parent=None):
        super().__init__(parent)
        self.connection, self.local, self.remote = connection, Path(local), remote
        self.remote_side, self.rows = remote_side, rows

    def run(self):
        deleted = kept = 0
        client = None
        try:
            if self.remote_side:
                client = make_client(self.connection.protocol, str(data_dir() / "known_hosts"))
                client.connect(self.connection)
            for path, state, _, expected in self.rows:
                if self.isInterruptionRequested():
                    break
                try:
                    removed = (self.delete_remote(client, path, state, expected) if self.remote_side
                               else self.delete_local(path, state, expected))
                except Exception as error:
                    removed = False
                    self.report.emit(f"Erreur : suppression miroir de {path} impossible ({error}). Élément conservé.")
                if removed:
                    deleted += 1
                else:
                    kept += 1
        except Exception:
            self.report.emit("Erreur : connexion impossible pour les suppressions miroir. Rien n’a été supprimé.")
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass
            self.done.emit(deleted, kept)

    def delete_remote(self, client, relative, state, expected):
        path = posixpath.join(self.remote, relative)
        entry = client.exists(path)
        if entry is None:
            return False
        if state == DELETE_DIR:
            if not entry.is_dir:
                return False
            if client.listdir(path):
                self.report.emit(f"Statut : {relative} n’est pas vide ; dossier distant conservé.")
                return False
            client.rmdir(path)
        else:
            if entry.is_dir or entry.permissions.startswith("l") or (entry.size, entry.modified) != tuple(expected):
                self.report.emit(f"Statut : {relative} a changé depuis la comparaison ; fichier distant conservé.")
                return False
            client.unlink(path)
        self.report.emit(f"Statut : supprimé (miroir) : {relative}")
        return True

    def delete_local(self, relative, state, expected):
        from smftp.utils.paths import local_child
        target = self.local
        for part in relative.split("/"):
            target = local_child(target, part)
        if target.is_symlink() or any(p.is_symlink() for p in target.parents if self.local in (p, *p.parents)):
            return False
        if not target.exists():
            return False
        if state == DELETE_DIR:
            if not target.is_dir() or any(target.iterdir()):
                self.report.emit(f"Statut : {relative} n’est pas vide ; dossier local conservé.")
                return False
            target.rmdir()
        else:
            info = target.stat()
            if not target.is_file() or info.st_size != expected[0] or abs(info.st_mtime - expected[1]) >= 0.001:
                self.report.emit(f"Statut : {relative} a changé depuis la comparaison ; fichier local conservé.")
                return False
            os.unlink(target)
        self.report.emit(f"Statut : supprimé (miroir) : {relative}")
        return True
