"""Remove Qt libraries that SMFTP never loads from a PyInstaller folder.

SMFTP uses QtCore, QtGui and QtWidgets only. The PySide6 hooks also collect
QML, Quick, PDF, the virtual keyboard and the software OpenGL renderer
(about 40 MB). build.ps1 runs the packaged smoke test afterwards, so a library
that turns out to be needed fails the build instead of shipping.

Usage: python tools/prune_bundle.py dist/SMFTP
"""
from pathlib import Path
import sys

UNUSED = [
    # Software OpenGL fallback: Qt Widgets draw with the raster engine.
    "opengl32sw.dll",
    "Qt6Pdf.dll", "Qt6Qml.dll", "Qt6QmlMeta.dll", "Qt6QmlModels.dll", "Qt6QmlWorkerScript.dll",
    "Qt6Quick.dll", "Qt6VirtualKeyboard.dll",
    "plugins/imageformats/qpdf.dll",
    "plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
    # Translations of Qt modules that are not shipped.
    "translations/qtwebengine_*.qm", "translations/qtdeclarative_*.qm", "translations/qtquick*.qm",
    "translations/qtmultimedia_*.qm", "translations/qtserialport_*.qm", "translations/qtwebsockets_*.qm",
    "translations/qtconnectivity_*.qm", "translations/qtlocation_*.qm", "translations/qtscxml_*.qm",
]


def prune(bundle: Path) -> int:
    qt = bundle / "_internal" / "PySide6"
    if not qt.is_dir():
        raise SystemExit(f"Dossier PySide6 introuvable : {qt}")
    freed = 0
    for pattern in UNUSED:
        for path in qt.glob(pattern):
            if path.is_file():
                freed += path.stat().st_size
                path.unlink()
                print(f"retiré : {path.relative_to(bundle)}")
    return freed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    freed = prune(Path(sys.argv[1]))
    print(f"{freed / 1_048_576:.1f} Mo libérés")
