"""Join server-provided names without allowing local path escapes or aliases."""
import os
import re
from pathlib import Path


def local_child(directory: str | Path, name: str) -> Path:
    if not name or name in (".", "..") or any(c in name for c in "/\\:\x00"):
        raise ValueError("Nom distant incompatible avec un téléchargement local sûr.")
    if os.name == "nt" and (any(c in name for c in '<>"|?*') or name.endswith((".", " ")) or
        re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name, re.I)):
        raise ValueError("Ce nom distant n’est pas autorisé par Windows. Renommez-le avant téléchargement.")
    return Path(directory) / name
