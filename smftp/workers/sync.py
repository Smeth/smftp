"""Recursive synchronization: read-only planning, then optional mirror deletions."""
from pathlib import Path
import os
import posixpath
import unicodedata
from PySide6.QtCore import QThread, Signal
from smftp.core.clients import make_client
from smftp.core.checksum import local_digest
from smftp.utils.storage import data_dir

MAX_DEPTH = 100


def is_junction(path) -> bool:
    """Windows directory junction (Python 3.12+); other systems have none."""
    try:
        return Path(path).is_junction()
    except (AttributeError, OSError):
        return False


# Plan row states. Copies keep (path, state, size, destination metadata).
NEW, MODIFIED, CONTENT, DELETE_FILE, DELETE_DIR = "Nouveau", "Modifié", "Contenu différent", "À supprimer", "Dossier à supprimer"
DELETIONS = (DELETE_FILE, DELETE_DIR)


class SyncPlanner(QThread):
    planned = Signal(object)
    failed = Signal(str)
    note = Signal(str)
    progress = Signal(int)  # Entries examined so far (local, then remote).

    def __init__(self, connection, local, remote, upload, parent=None, *, compare_content=False, mirror=False):
        super().__init__(parent)
        self.connection, self.local, self.remote, self.upload = connection, Path(local), remote, upload
        self.compare_content, self.mirror = compare_content, mirror

    def run(self):
        client = None
        try:
            client = make_client(self.connection.protocol, str(data_dir() / "known_hosts"))
            client.connect(self.connection)
            local_files, local_dirs, local_links = {}, set(), set()

            def walk(folder, relative=""):
                # Neither symbolic links nor Windows junctions are followed: their
                # target may lie outside the synchronized folder.
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if self.isInterruptionRequested():
                            return
                        key = f"{relative}/{entry.name}" if relative else entry.name
                        if entry.is_symlink() or is_junction(entry.path):
                            local_links.add(key)
                        elif entry.is_dir(follow_symlinks=False):
                            local_dirs.add(key)
                            walk(entry.path, key)
                        elif entry.is_file(follow_symlinks=False):
                            info = entry.stat(follow_symlinks=False)
                            local_files[key] = (info.st_size, info.st_mtime)
                        self.count_seen()
            walk(self.local)
            remote_files, remote_dirs, remote_links = {}, set(), set()

            def visit(path, relative="", depth=0):
                if self.isInterruptionRequested():
                    return
                if depth > MAX_DEPTH:
                    raise ValueError("Arborescence distante trop profonde (boucle de liens ?).")
                for item in client.listdir(path):
                    if item.name in (".", "..") or any(c in item.name for c in "/\\:"):
                        raise ValueError
                    key = posixpath.join(relative, item.name)
                    if item.permissions.startswith("l"):
                        remote_links.add(key)
                    elif item.is_dir:
                        remote_dirs.add(key)
                        visit(posixpath.join(path, item.name), key, depth + 1)
                    else:
                        remote_files[key] = (item.size, item.modified)
                    self.count_seen()
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
            if self.mirror and getattr(client, "incomplete_listing", False):
                self.note.emit("Synchronisation miroir : la liste du serveur contient des lignes illisibles ; "
                               "aucune suppression n’est proposée.")
            elif self.mirror:
                plan.extend(self.mirror_deletions(
                    source, destination, source_dirs, destination_dirs, local_links | remote_links,
                    hides_dotfiles=not self.upload and getattr(client, "hides_dotfiles", False)))
            self.planned.emit(plan)
        except Exception:
            self.failed.emit("Impossible de comparer les dossiers. Vérifiez la connexion et les droits d’accès.")
        finally:
            if client:
                client.close()

    seen = 0

    def count_seen(self):
        self.seen += 1
        if self.seen % 100 == 0:
            self.progress.emit(self.seen)

    def mirror_deletions(self, source, destination, source_dirs, destination_dirs, links, hides_dotfiles=False):
        """Destination extras, minus anything that may still exist in the source.

        Kept: paths at or under a link on either side (never followed), names that
        differ from a source name only by case or Unicode form (Windows and many
        servers treat them as the same file), and, when the server listing hides
        names starting with ".", local dotfiles that the server may well have.
        Files come first, then folders deepest first: a folder is only removed once empty.
        """
        def fold(path):
            return unicodedata.normalize("NFC", path).casefold()

        def under_link(path):
            return any(path == link or path.startswith(link + "/") for link in links)
        present = {fold(path) for path in source} | {fold(path) for path in source_dirs}
        rows, kept = [], 0
        candidates = [(path, DELETE_FILE, metadata[0], metadata)
                      for path, metadata in sorted(destination.items()) if path not in source]
        candidates += [(path, DELETE_DIR, 0, None)
                       for path in sorted(destination_dirs - source_dirs, key=lambda p: (-p.count("/"), p))
                       if path not in source]
        for row in candidates:
            path = row[0]
            if (under_link(path) or fold(path) in present
                    or (hides_dotfiles and any(part.startswith(".") for part in path.split("/")))):
                kept += 1
                continue
            rows.append(row)
        if kept:
            self.note.emit(f"Synchronisation miroir : {kept} élément(s) de la destination conservé(s) par prudence "
                           "(liens symboliques ou jonctions, noms ne différant que par les majuscules, "
                           "ou fichiers cachés que le serveur ne liste pas).")
        return rows

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
        inside = [p for p in target.parents if self.local in (p, *p.parents)]
        if target.is_symlink() or is_junction(target) or any(p.is_symlink() or is_junction(p) for p in inside):
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
