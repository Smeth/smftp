"""Updates: GitHub release check, verified download and installer launch.

Security rules (the downloaded installer is run on the user's computer):
- HTTPS only; requests go to api.github.com and to this project's release files
  (https://github.com/Smeth/smftp/releases/download/...), whose redirections may
  only lead to GitHub's file servers;
- every response is read with a size limit;
- the installer runs only if its SHA-256 matches SHA256SUMS.txt of the same
  release, checked again just before it is started; no shell is involved.

The Microsoft Store build never contacts GitHub: the Store updates it.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QThread, Signal
from smftp.i18n import tr


CURRENT_VERSION = "1.2.0"
RELEASES_API = "https://api.github.com/repos/Smeth/smftp/releases/latest"
RELEASES_PAGE = "https://github.com/Smeth/smftp/releases/latest"
DISTRIBUTIONS = ("github", "store", "uptodown")
ASSET_PREFIX = "https://github.com/Smeth/smftp/releases/download/"
REDIRECT_HOSTS = ("objects.githubusercontent.com", "release-assets.githubusercontent.com")
SUMS_NAME = "SHA256SUMS.txt"
API_LIMIT = 1_000_000
SUMS_LIMIT = 64 * 1024
INSTALLER_LIMIT = 400 * 1024 * 1024
NOTES_LIMIT = 6000
USER_AGENT = "SMFTP-update-checker"

# QSettings keys.
AUTO_CHECK_KEY = "updates/auto_check"
LAST_CHECK_KEY = "updates/last_check"
IGNORED_KEY = "updates/ignored_version"


class UpdateError(Exception):
    """Refused or failed update step; the message is shown to the user."""


def distribution() -> str:
    """Channel this copy was built for, written by build.ps1 -Distribution.

    The Microsoft Store updates the applications it installs: that build must not
    offer a second update channel.
    """
    value = os.environ.get("SMFTP_DISTRIBUTION", "")
    if not value:
        try:
            marker = Path(__file__).resolve().parents[2] / "resources" / "distribution.txt"
            value = marker.read_text(encoding="utf-8")
        except OSError:
            value = ""
    value = value.strip().lower()
    return value if value in DISTRIBUTIONS else "github"


def checks_for_updates() -> bool:
    return distribution() != "store"


def parse_version(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value).strip())
    return tuple(map(int, match.groups())) if match else None


def plain_version(value) -> str:
    """"v1.2.0" -> "1.2.0" (file names of the release use the bare version)."""
    parsed = parse_version(value)
    return ".".join(map(str, parsed)) if parsed else ""


# ---------- automatic check: once a day, never for the Store build ----------

def auto_check_enabled(settings) -> bool:
    return checks_for_updates() and settings.value(AUTO_CHECK_KEY, True, type=bool)


def automatic_check_due(settings, today: date | None = None) -> bool:
    """True when the automatic check should run now (at most once a day)."""
    if not auto_check_enabled(settings):
        return False
    return settings.value(LAST_CHECK_KEY, "") != (today or date.today()).isoformat()


def record_check(settings, today: date | None = None) -> None:
    settings.setValue(LAST_CHECK_KEY, (today or date.today()).isoformat())


def ignored_version(settings) -> str:
    return str(settings.value(IGNORED_KEY, "") or "")


def ignore_version(settings, version: str) -> None:
    settings.setValue(IGNORED_KEY, plain_version(version))


def is_ignored(settings, version: str) -> bool:
    return bool(plain_version(version)) and plain_version(version) == ignored_version(settings)


# ---------- network: HTTPS, limited hosts, limited sizes ----------

def is_release_asset_url(url) -> bool:
    """A file of this project's GitHub releases, nothing else."""
    text = str(url or "")
    parts = urlsplit(text)
    return (parts.scheme == "https" and parts.netloc == "github.com" and text.startswith(ASSET_PREFIX)
            and ".." not in parts.path and "\\" not in text)


def is_allowed_redirect(url) -> bool:
    """GitHub's file servers, where release downloads are redirected."""
    parts = urlsplit(str(url or ""))
    return (parts.scheme == "https" and parts.hostname in REDIRECT_HOSTS and parts.port is None
            and not parts.username and not parts.password)


class _RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        api = urlsplit(newurl)
        if not (is_release_asset_url(newurl) or is_allowed_redirect(newurl)
                or (api.scheme == "https" and api.netloc == "api.github.com")):
            raise urllib.error.URLError(tr("Redirection refusée vers une adresse hors de GitHub : {url}").format(url=newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_RestrictedRedirect())


def _urlopen(request, timeout):
    """Replaced by the tests."""
    return _opener.open(request, timeout=timeout)


def _request(url, accept="application/octet-stream"):
    return urllib.request.Request(url, headers={"Accept": accept, "User-Agent": USER_AGENT})


def _check_final_url(response, url):
    final = response.geturl() if hasattr(response, "geturl") else url
    if final != url and not (is_release_asset_url(final) or is_allowed_redirect(final)):
        raise UpdateError(tr("Téléchargement refusé : adresse hors de GitHub ({url}).").format(url=final))


def _read_limited(response, limit: int) -> bytes:
    data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError(tr("Réponse trop volumineuse, téléchargement refusé."))
    return data


def release_notes_text(body, limit: int = NOTES_LIMIT) -> str:
    """Release body (Markdown) as short plain text: shown as text, never as HTML."""
    text = str(body or "").replace("\r\n", "\n")
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)                # images
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)    # links
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)     # headings
    text = re.sub(r"(\*\*|__|`)", "", text)
    text = re.sub(r"<[^>]{1,200}>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n…"
    return text


def fetch_latest_release():
    """Latest GitHub release compared with this version (no request for the Store build)."""
    if not checks_for_updates():
        return {"status": "disabled"}
    try:
        with _urlopen(_request(RELEASES_API, "application/vnd.github+json"), timeout=8) as response:
            release = json.loads(_read_limited(response, API_LIMIT).decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {"status": "none"}
        raise

    version_text = release.get("tag_name", "")
    latest = parse_version(version_text)
    current = parse_version(CURRENT_VERSION)
    if latest is None or current is None:
        raise ValueError(tr("Version de release GitHub invalide"))
    if latest > current:
        assets = {}
        for asset in release.get("assets") or []:
            name, url = asset.get("name"), asset.get("browser_download_url")
            if isinstance(name, str) and is_release_asset_url(url):
                assets[name] = {"url": url, "size": asset.get("size") or 0}
        return {
            "status": "update",
            "version": plain_version(version_text),
            "tag": version_text,
            "url": safe_release_url(release.get("html_url")),
            "notes": release_notes_text(release.get("body")),
            "assets": assets,
        }
    return {"status": "current", "version": CURRENT_VERSION}


def safe_release_url(value) -> str:
    """Only open this project's GitHub pages, whatever the API answered."""
    text = str(value or "")
    return text if text.startswith("https://github.com/Smeth/smftp/") else RELEASES_PAGE


# ---------- installation ----------

def installer_name(version: str) -> str:
    return f"SMFTP-{plain_version(version)}-setup-x64.exe"


def installed_by_installer(executable: str | None = None) -> bool:
    """SMFTP was installed by its Inno Setup installer (uninstaller next to the executable).

    A portable copy (zip) or a source checkout is updated by hand: only the page is offered.
    """
    if executable is None:
        if not getattr(sys, "frozen", False):
            return False
        executable = sys.executable
    return (Path(executable).resolve().parent / "unins000.exe").is_file()


def can_install_update() -> bool:
    """"Télécharger et installer" is offered to installed copies of the GitHub and Uptodown builds.

    Uptodown distributes the same Inno Setup installer (same AppId): its copies are
    updated with the GitHub release file, verified the same way.
    """
    return sys.platform == "win32" and checks_for_updates() and installed_by_installer()


def parse_sha256sums(text: str) -> dict:
    """Lines "<sha256>  <name>" (or "<sha256> *<name>") -> {name: sha256}."""
    sums = {}
    for line in text.splitlines():
        match = re.fullmatch(r"\s*([0-9a-fA-F]{64})\s+\*?(\S.*?)\s*", line)
        if match:
            sums[match.group(2)] = match.group(1).lower()
    return sums


def release_files(result) -> tuple[str, str]:
    """URLs of the installer and of SHA256SUMS.txt, both from the same release."""
    name = installer_name(result.get("version", ""))
    assets = result.get("assets") or {}
    installer, sums = assets.get(name), assets.get(SUMS_NAME)
    if not installer:
        raise UpdateError(tr("Cette version ne contient pas d’installeur ({name}).").format(name=name))
    if not sums:
        raise UpdateError(tr("Cette version ne publie pas d’empreintes (SHA256SUMS.txt) : installation refusée."))
    for item in (installer, sums):
        if not is_release_asset_url(item["url"]):
            raise UpdateError(tr("Téléchargement refusé : adresse hors de GitHub ({url}).").format(url=item["url"]))
    folder = ASSET_PREFIX + (result.get("tag") or "") + "/"
    if not (installer["url"].startswith(folder) and sums["url"].startswith(folder)):
        raise UpdateError(tr("L’installeur et ses empreintes ne viennent pas de la même version."))
    return installer["url"], sums["url"]


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_update(result, folder, progress=None, cancelled=lambda: False) -> tuple[str, str]:
    """Download the installer of ``result`` into ``folder`` and check its SHA-256.

    Returns (installer path, sha256). Raises UpdateError when anything is refused;
    a file that fails the check is deleted.
    """
    installer_url, sums_url = release_files(result)
    name = installer_name(result["version"])
    if not is_release_asset_url(sums_url):
        raise UpdateError(tr("Téléchargement refusé : adresse hors de GitHub ({url}).").format(url=sums_url))
    with _urlopen(_request(sums_url), timeout=15) as response:
        _check_final_url(response, sums_url)
        sums = parse_sha256sums(_read_limited(response, SUMS_LIMIT).decode("utf-8", "replace"))
    expected = sums.get(name)
    if not expected:
        raise UpdateError(tr("Aucune empreinte publiée pour {name} : installation refusée.").format(name=name))

    target = Path(folder) / name
    digest = hashlib.sha256()
    done = 0
    try:
        with _urlopen(_request(installer_url), timeout=30) as response:
            _check_final_url(response, installer_url)
            headers = getattr(response, "headers", None)
            try:
                total = int(headers.get("Content-Length") or 0) if headers is not None else 0
            except (TypeError, ValueError):
                total = 0
            if total > INSTALLER_LIMIT:
                raise UpdateError(tr("Réponse trop volumineuse, téléchargement refusé."))
            with open(target, "wb") as stream:
                while True:
                    if cancelled():
                        raise UpdateError(tr("Téléchargement annulé."))
                    block = response.read(256 * 1024)
                    if not block:
                        break
                    done += len(block)
                    if done > INSTALLER_LIMIT:
                        raise UpdateError(tr("Réponse trop volumineuse, téléchargement refusé."))
                    digest.update(block)
                    stream.write(block)
                    if progress:
                        progress(done, total)
        if digest.hexdigest() != expected:
            raise UpdateError(tr("L’empreinte SHA-256 du fichier téléchargé ne correspond pas à celle publiée : "
                                 "fichier supprimé, installation refusée."))
    except BaseException:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return str(target), expected


def _spawn(arguments):
    """Start the installer, detached from SMFTP (replaced by the tests)."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(arguments, close_fds=True, creationflags=flags)


def launch_installer(path, expected_sha256: str) -> None:
    """Run the downloaded installer (normal wizard), only if its SHA-256 still matches."""
    if not expected_sha256 or not Path(path).is_file() or file_sha256(path) != expected_sha256.lower():
        raise UpdateError(tr("L’installeur n’a pas pu être vérifié : il n’est pas lancé."))
    _spawn([str(path), "/SP-"])


# ---------- workers ----------

class UpdateWorker(QThread):
    completed = Signal(object)

    def __init__(self, parent=None, automatic=False):
        super().__init__(parent)
        self.automatic = automatic

    def run(self):
        try:
            result = fetch_latest_release()
        except Exception as error:
            result = {"status": "error", "detail": str(error)}
        result["automatic"] = self.automatic
        self.completed.emit(result)


class UpdateDownloadWorker(QThread):
    progress = Signal(object, object)  # bytes done, total (0 if unknown)
    downloaded = Signal(str, str)      # installer path, sha256
    failed = Signal(str)

    def __init__(self, result, folder, parent=None):
        super().__init__(parent)
        self.result, self.folder = result, folder

    def run(self):
        try:
            path, sha = download_update(self.result, self.folder, self.progress.emit, self.isInterruptionRequested)
        except UpdateError as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(tr("Téléchargement impossible : {detail}").format(detail=error))
        else:
            self.downloaded.emit(path, sha)
