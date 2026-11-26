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


# Types that Windows runs, installs or interprets when they are "opened".
EXECUTABLE_SUFFIXES = frozenset("""
.exe .com .bat .cmd .pif .scr .cpl .msi .msp .mst .msc .msix .msixbundle .appx .appxbundle
.appinstaller .application .appref-ms .gadget .hta .jar .js .jse .vbs .vbe .wsf .wsh .ws .wsc
.ps1 .ps1xml .ps2 .psc1 .psd1 .psm1 .lnk .url .website .scf .inf .reg .rgs .chm .hlp .sct .shb
.shs .settingcontent-ms .library-ms .search-ms .searchconnector-ms .diagcab .iso .img .vhd .vhdx
.xbap .xll .py .pyw .pyz .pl .rb .sh
""".split())


def runs_when_opened(name: str) -> bool:
    """True when opening the file with its Windows association may run code."""
    suffix = Path(name).suffix.lower()
    if suffix in EXECUTABLE_SUFFIXES:
        return True
    extra = os.environ.get("PATHEXT", "") if os.name == "nt" else ""
    return bool(suffix) and suffix in {item.lower() for item in extra.split(";") if item}


def mark_from_internet(path: str | Path) -> None:
    """Add the Windows "Mark of the Web" so that SmartScreen and Office Protected
    View treat a downloaded copy like a file from the Internet. Best effort."""
    if os.name != "nt":
        return
    try:
        with open(f"{path}:Zone.Identifier", "w", encoding="ascii") as stream:
            stream.write("[ZoneTransfer]\r\nZoneId=3\r\n")
    except OSError:
        pass


def is_network_path(path: str) -> bool:
    r"""UNC and device paths (\\server\share, //server/share, \\?\..., \\.\...).

    Merely checking whether such a path exists makes Windows contact the server
    and send the user's NTLM credentials: never touch one that came from an
    untrusted source (an AI model, an imported file).
    """
    text = str(path or "").strip()
    return len(text) >= 2 and text[0] in "\\/" and text[1] in "\\/"
