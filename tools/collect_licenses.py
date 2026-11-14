"""Copy SMFTP's licence and every bundled dependency licence next to the executable.

Usage: python tools/collect_licenses.py dist/SMFTP
"""
from importlib.metadata import distribution, PackageNotFoundError
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ["PySide6", "paramiko", "keyring"]
LICENSE_FILE = re.compile(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)


def runtime_distributions():
    """Runtime packages and their dependencies, without optional extras."""
    seen, pending, found = set(), list(RUNTIME), []
    while pending:
        name = pending.pop()
        key = name.lower().replace("_", "-").replace(".", "-")
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue
        found.append(dist)
        for requirement in dist.requires or []:
            if "extra ==" not in requirement:
                pending.append(re.split(r"[ ;<>=!~\[(]", requirement, maxsplit=1)[0])
    return found


def main(target: str) -> None:
    output = Path(target) / "licenses"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for name in ("LICENSE.md", "THIRD-PARTY-LICENSES.md"):
        shutil.copy2(ROOT / name, output / name)
    for text in (ROOT / "licenses").glob("*.txt"):
        shutil.copy2(text, output / text.name)
    missing = []
    for dist in runtime_distributions():
        name = f"{dist.metadata['Name']}-{dist.version}"
        files = [f for f in dist.files or [] if ".dist-info" in str(f) and LICENSE_FILE.search(f.name)]
        if not files:
            missing.append(name)
            continue
        folder = output / name
        folder.mkdir()
        for file in files:
            shutil.copy2(file.locate(), folder / file.name)
    if missing:
        print("Sans fichier de licence dans le paquet (voir THIRD-PARTY-LICENSES.md) : " + ", ".join(missing))
    print(f"Licences copiées dans {output}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dist/SMFTP")
