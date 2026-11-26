"""Per-user configuration. Credentials are stored exclusively in the OS vault."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from PySide6.QtCore import QStandardPaths
from smftp.i18n import tr


def data_dir() -> Path:
    import os
    override = os.environ.get("SMFTP_DATA_DIR")
    path = Path(override) if override else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)) / "SMFTP"
    path.mkdir(parents=True, exist_ok=True)
    return path


def acquire_instance_lock():
    """Return (lock, True) for the only running instance, (None, False) otherwise.

    Two instances would overwrite each other's profiles and transfer queue. If the
    data folder is unusable the application still starts; that error is reported
    by the features that need the folder.
    """
    from PySide6.QtCore import QLockFile
    try:
        lock = QLockFile(str(data_dir() / "smftp.lock"))
    except OSError:
        return None, True
    lock.setStaleLockTime(0)  # Only a dead owner process makes the lock stale.
    if lock.tryLock(200):
        return lock, True
    if lock.error() == QLockFile.LockError.LockFailedError:
        return None, False
    return None, True  # Permission or unknown error: do not block startup.


def clean_stale_temporary(directories=None, older_than: float = 86400) -> int:
    """Remove SMFTP's temporary folders (edit copies, drag copies) older than a day.

    They are deleted when SMFTP closes normally; a crash left them behind. Only
    folders with SMFTP's prefixes are touched. Returns the number removed.
    """
    import re
    import shutil
    import tempfile
    import time
    # Exactly the names tempfile.TemporaryDirectory gives SMFTP's folders: a user's
    # own "smftp-backup" folder in a chosen temporary directory is never touched.
    pattern = re.compile(r"smftp-(?:copy-|drag-|update-)?[a-z0-9_]{8}")
    removed = 0
    limit = time.time() - older_than
    for base in directories or [tempfile.gettempdir()]:
        try:
            candidates = [p for p in Path(base).glob("smftp-*") if pattern.fullmatch(p.name)]
        except OSError:
            continue
        for path in candidates:
            try:
                if path.is_dir() and not path.is_symlink() and path.stat().st_mtime < limit:
                    shutil.rmtree(path)
                    removed += 1
            except OSError:
                continue
    return removed


def instance_channel() -> str:
    """Local socket name of the running SMFTP for this user and data folder."""
    import hashlib
    return "smftp-" + hashlib.sha256(str(data_dir()).encode("utf-8")).hexdigest()[:20]


def ask_running_instance_to_show() -> bool:
    """A second launch brings the open window to the front instead of starting again."""
    from PySide6.QtNetwork import QLocalSocket
    socket = QLocalSocket()
    socket.connectToServer(instance_channel())
    if not socket.waitForConnected(1000):
        return False
    socket.write(b"show\n")
    socket.flush()
    # Wait for the acknowledgement: disconnecting at once could drop the request.
    socket.waitForReadyRead(2000)
    socket.disconnectFromServer()
    return True


def listen_for_other_instances(window):
    """Running instance: show the window when another launch asks for it."""
    from PySide6.QtNetwork import QLocalServer
    server = QLocalServer(window)
    name = instance_channel()
    QLocalServer.removeServer(name)  # A crashed instance may have left the name behind.
    if not server.listen(name):
        return None

    def accept():
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            connection.readyRead.connect(lambda c=connection: reveal(c))
            connection.disconnected.connect(connection.deleteLater)
            if connection.bytesAvailable():
                reveal(connection)  # Already arrived before readyRead was connected.

    def reveal(connection):
        if bytes(connection.readAll()).strip() == b"show":
            window.reveal()
            connection.write(b"ok\n")
            connection.flush()
    server.newConnection.connect(accept)
    return server


def configure_logging() -> bool:
    try:
        handler = RotatingFileHandler(data_dir() / "smftp.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        logging.getLogger("smftp").addHandler(logging.NullHandler())
        return False
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("smftp").addHandler(handler)
    logging.getLogger("smftp").setLevel(logging.INFO)
    return True


def load_sites(problems: list | None = None) -> list[dict]:
    """Read the saved profiles. A file that cannot be read completely is kept aside
    under another name, so that the next save cannot erase the profiles it holds;
    ``problems`` then receives a message for the user."""
    from smftp.utils.profiles import normalize_profile, merge_profiles
    import shutil
    import time
    path = data_dir() / "sites.json"
    if problems is None:
        problems = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raw = None
    clean = []
    dropped = 0
    if isinstance(raw, list):
        for site in raw:
            try:
                clean.append(normalize_profile(site))
            except (ValueError, TypeError, AttributeError, KeyError):
                dropped += 1
    if isinstance(raw, list) and not dropped:
        return merge_profiles([], clean)
    backup = path.with_name(f"sites.illisible-{time.strftime('%Y%m%d-%H%M%S')}.json")
    try:
        shutil.copy2(path, backup)
        kept = " " + tr("Une copie du fichier d’origine a été conservée sous {path}.").format(path=backup)
    except OSError:
        kept = ""
    if raw is None or not isinstance(raw, list):
        problems.append(tr("Le fichier des profils (sites.json) est illisible : aucun profil n’a pu être chargé.") + kept)
    else:
        problems.append(tr("{count} profil(s) illisible(s) dans sites.json ont été ignorés.").format(count=dropped) + kept)
    return merge_profiles([], clean)


def write_json_atomically(path: Path, data, prefix: str) -> None:
    """Write to a temporary file of the same folder, then replace: never a half-written file."""
    import os
    import tempfile
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=prefix, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def save_sites(sites: list[dict]) -> None:
    from smftp.utils.profiles import normalize_profile
    clean = [normalize_profile(site) for site in sites]
    write_json_atomically(data_dir() / "sites.json", clean, "sites-")


def normalize_bookmark(item) -> dict | None:
    """{name, local, remote} with non-empty text values, or None."""
    if not isinstance(item, dict):
        return None
    values = {key: item.get(key) for key in ("name", "local", "remote")}
    if not all(isinstance(value, str) and value.strip() for value in values.values()):
        return None
    return {"name": values["name"].strip(), "local": values["local"], "remote": values["remote"]}


def load_bookmarks(problems: list | None = None) -> dict[str, list[dict]]:
    """Folder bookmarks by profile identity: {identity: [{name, local, remote}]}.

    A file that cannot be read completely is kept aside under another name, so that
    the next save cannot erase the bookmarks it holds."""
    import shutil
    import time
    path = data_dir() / "bookmarks.json"
    if problems is None:
        problems = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raw = None
    clean = {}
    dropped = 0
    if isinstance(raw, dict):
        for key, items in raw.items():
            if not isinstance(key, str) or not isinstance(items, list):
                dropped += 1
                continue
            for item in items:
                bookmark = normalize_bookmark(item)
                if bookmark is None:
                    dropped += 1
                else:
                    clean.setdefault(key, []).append(bookmark)
        if not dropped:
            return clean
    backup = path.with_name(f"bookmarks.illisible-{time.strftime('%Y%m%d-%H%M%S')}.json")
    try:
        shutil.copy2(path, backup)
        kept = " " + tr("Une copie du fichier d’origine a été conservée sous {path}.").format(path=backup)
    except OSError:
        kept = ""
    if not isinstance(raw, dict):
        problems.append(tr("Le fichier des signets (bookmarks.json) est illisible : aucun signet n’a pu être chargé.") + kept)
    else:
        problems.append(tr("{count} signet(s) illisible(s) dans bookmarks.json ont été ignorés.").format(count=dropped) + kept)
    return clean


def save_bookmarks(bookmarks: dict[str, list[dict]]) -> None:
    clean = {}
    for key, items in bookmarks.items():
        kept = [bookmark for bookmark in map(normalize_bookmark, items) if bookmark]
        if kept:
            clean[key] = kept
    write_json_atomically(data_dir() / "bookmarks.json", clean, "bookmarks-")
