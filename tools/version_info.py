"""Write the Windows version resource of SMFTP.exe from smftp.workers.updates.

Windows shows these fields in the file properties; SmartScreen, antivirus
scanners and the stores read them too.

Usage: python tools/version_info.py build/version_info.txt
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def current_version() -> str:
    text = (ROOT / "smftp" / "workers" / "updates.py").read_text(encoding="utf-8")
    return re.search(r'CURRENT_VERSION = "(\d+\.\d+\.\d+)"', text).group(1)


def render(version: str) -> str:
    numbers = tuple(int(part) for part in version.split(".")) + (0,)
    fields = {
        "CompanyName": "Smeth",
        "FileDescription": "SMFTP - client FTP, FTPS, SFTP et SSH",
        "FileVersion": version,
        "InternalName": "SMFTP",
        "LegalCopyright": "Copyright (c) 2026 Smeth. PolyForm Noncommercial 1.0.0",
        "OriginalFilename": "SMFTP.exe",
        "ProductName": "SMFTP",
        "ProductVersion": version,
    }
    strings = ",\n            ".join(f"StringStruct('{key}', '{value}')" for key, value in fields.items())
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
            {strings}])]),
    VarFileInfo([VarStruct('Translation', [0x040C, 1200])])
  ]
)
"""


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(current_version()), encoding="utf-8")
    print(f"{target} : version {current_version()}")
