"""Per-user configuration. Credentials are stored exclusively in the OS vault."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from PySide6.QtCore import QStandardPaths


def data_dir() -> Path:
    import os
    override = os.environ.get("SMFTP_DATA_DIR")
    path = Path(override) if override else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)) / "SMFTP"
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def load_sites() -> list[dict]:
    from smftp.utils.profiles import normalize_profile, merge_profiles
    try:
        raw = json.loads((data_dir() / "sites.json").read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        clean = []
        for site in raw:
            try:
                clean.append(normalize_profile(site))
            except (ValueError, TypeError):
                continue
        return merge_profiles([], clean)
    except (OSError, ValueError, TypeError):
        return []


def save_sites(sites: list[dict]) -> None:
    from smftp.utils.profiles import normalize_profile
    import os
    import tempfile
    clean = [normalize_profile(site) for site in sites]
    path = data_dir() / "sites.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="sites-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(clean, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
