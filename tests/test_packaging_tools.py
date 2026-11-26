"""Release tooling: pruned Qt libraries, version resource, MSIX manifest."""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import make_msix  # noqa: E402
import prune_bundle  # noqa: E402
import version_info  # noqa: E402


def test_prune_removes_only_unused_qt_files(tmp_path):
    qt = tmp_path / "_internal" / "PySide6"
    for name in ("Qt6Core.dll", "Qt6Widgets.dll", "Qt6Quick.dll", "opengl32sw.dll",
                 "plugins/imageformats/qpdf.dll", "plugins/imageformats/qsvg.dll",
                 "plugins/platforms/qwindows.dll", "translations/qtbase_fr.qm", "translations/qtwebengine_fr.qm"):
        path = qt / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
    assert prune_bundle.prune(tmp_path) == 40
    left = {p.relative_to(qt).as_posix() for p in qt.rglob("*") if p.is_file()}
    assert left == {"Qt6Core.dll", "Qt6Widgets.dll", "plugins/imageformats/qsvg.dll",
                    "plugins/platforms/qwindows.dll", "translations/qtbase_fr.qm"}


def test_version_resource_matches_the_application():
    from smftp.workers.updates import CURRENT_VERSION
    assert version_info.current_version() == CURRENT_VERSION
    text = version_info.render("1.2.3")
    assert "filevers=(1, 2, 3, 0)" in text and "StringStruct('ProductVersion', '1.2.3')" in text
    compile(text, "version_info", "eval")  # PyInstaller evaluates this file.


def test_msix_manifest_is_well_formed_and_escaped():
    text = make_msix.manifest("12345Smeth.SMFTP", "CN=ABC, O=\"Smeth & Co\"", "Smeth & Co", "1.1.0.0")
    root = ET.fromstring(text.encode("utf-8"))
    ns = {"m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"}
    identity = root.find("m:Identity", ns)
    assert identity.get("Publisher") == "CN=ABC, O=\"Smeth & Co\"" and identity.get("Version") == "1.1.0.0"
    assert root.find("m:Properties/m:PublisherDisplayName", ns).text == "Smeth & Co"
    assert root.find("m:Applications/m:Application", ns).get("EntryPoint") == "Windows.FullTrustApplication"


def test_msix_assets_have_the_required_sizes(tmp_path):
    from PySide6.QtGui import QImage
    make_msix.make_assets(tmp_path)
    for name, (width, height) in make_msix.ASSETS.items():
        image = QImage(str(tmp_path / name))
        assert (image.width(), image.height()) == (width, height), name
