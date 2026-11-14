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


def unique_copy_name(path, taken, directory=None):
    """First free name among "nom (copie).ext", "nom (copie 2).ext", ...

    ``taken`` receives a candidate Path and reports whether it is already used.
    Folders keep their whole name: "dossier (copie)".
    """
    from pathlib import Path
    path = Path(path)
    if directory is None:
        directory = path.is_dir()
    stem, suffix = (path.stem, path.suffix) if path.suffix and not directory else (path.name, "")
    for number in range(1, 10_000):
        label = "copie" if number == 1 else f"copie {number}"
        candidate = path.with_name(f"{stem} ({label}){suffix}")
        if not taken(candidate):
            return candidate
    raise FileExistsError("Aucun nom de copie disponible.")
