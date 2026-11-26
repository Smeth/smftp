"""Check GitHub releases without blocking the desktop interface."""
import json
import re
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal


CURRENT_VERSION = "1.1.0"
RELEASES_API = "https://api.github.com/repos/Smeth/smftp/releases/latest"
RELEASES_PAGE = "https://github.com/Smeth/smftp/releases/latest"
DISTRIBUTIONS = ("github", "store", "uptodown")


def distribution() -> str:
    """Channel this copy was built for, written by build.ps1 -Distribution.

    The Microsoft Store updates the applications it installs: that build must not
    offer a second update channel.
    """
    import os
    from pathlib import Path
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


def fetch_latest_release():
    request = urllib.request.Request(
        RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "SMFTP-update-checker"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            release = json.loads(response.read(1_000_000).decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {"status": "none"}
        raise

    version_text = release.get("tag_name", "")
    latest = parse_version(version_text)
    current = parse_version(CURRENT_VERSION)
    if latest is None or current is None:
        raise ValueError("Version de release GitHub invalide")
    if latest > current:
        return {
            "status": "update",
            "version": version_text,
            "url": safe_release_url(release.get("html_url")),
        }
    return {"status": "current", "version": CURRENT_VERSION}


def safe_release_url(value) -> str:
    """Only open this project's GitHub pages, whatever the API answered."""
    text = str(value or "")
    return text if text.startswith("https://github.com/Smeth/smftp/") else RELEASES_PAGE


class UpdateWorker(QThread):
    completed = Signal(object)

    def run(self):
        try:
            result = fetch_latest_release()
        except Exception as error:
            result = {"status": "error", "detail": str(error)}
        self.completed.emit(result)
