"""Build the Microsoft Store MSIX package from a PyInstaller folder.

The identity values come from Partner Center (Produit > Gestion des produits >
Identité du produit): Package/Identity/Name, Package/Identity/Publisher and
Package/Properties/PublisherDisplayName. The Store signs the package itself.

Usage:
  python tools/make_msix.py dist/SMFTP dist --name 12345Smeth.SMFTP \
      --publisher "CN=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX" --publisher-display-name Smeth
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from xml.sax.saxutils import quoteattr, escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from version_info import current_version  # noqa: E402

DESCRIPTION = "Client FTP, FTPS, SFTP et SSH : transferts vérifiés, synchronisation et terminal."
# Asset file -> (width, height). Plain names (no scale qualifier): no resources.pri needed.
ASSETS = {
    "StoreLogo.png": (50, 50),
    "Square44x44Logo.png": (44, 44),
    "Square150x150Logo.png": (150, 150),
    "Wide310x150Logo.png": (310, 150),
}


def make_assets(target: Path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    QGuiApplication.instance() or QGuiApplication(["make_msix", "-platform", "offscreen"])
    source = QImage(str(ROOT / "resources" / "sf-icon.png"))
    if source.isNull():
        raise SystemExit("resources/sf-icon.png illisible")
    target.mkdir(parents=True, exist_ok=True)
    for name, (width, height) in ASSETS.items():
        canvas = QImage(width, height, QImage.Format.Format_ARGB32)
        canvas.fill(Qt.GlobalColor.transparent)
        side = int(min(width, height) * (0.8 if width != height or width >= 150 else 1.0))
        icon = source.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter = QPainter(canvas)
        painter.drawImage((width - icon.width()) // 2, (height - icon.height()) // 2, icon)
        painter.end()
        if not canvas.save(str(target / name)):
            raise SystemExit(f"Impossible d’écrire {name}")


def manifest(name: str, publisher: str, display: str, version: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap rescap">
  <Identity Name={quoteattr(name)} Publisher={quoteattr(publisher)} Version="{version}" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>SMFTP</DisplayName>
    <PublisherDisplayName>{escape(display)}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Resources>
    <Resource Language="fr-FR" />
  </Resources>
  <Applications>
    <Application Id="SMFTP" Executable="SMFTP.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="SMFTP" Description={quoteattr(DESCRIPTION)} BackgroundColor="transparent"
                          Square150x150Logo="Assets\\Square150x150Logo.png" Square44x44Logo="Assets\\Square44x44Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\\Wide310x150Logo.png" />
      </uap:VisualElements>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
</Package>
"""


def find_makeappx() -> str:
    found = shutil.which("makeappx")
    if found:
        return found
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    candidates = sorted(kits.glob("10.*/x64/makeappx.exe"))
    if not candidates:
        raise SystemExit("makeappx.exe introuvable : installez le SDK Windows 10/11.")
    return str(candidates[-1])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--name", required=True, help="Package/Identity/Name (Partner Center)")
    parser.add_argument("--publisher", required=True, help="Package/Identity/Publisher, « CN=… » (Partner Center)")
    parser.add_argument("--publisher-display-name", required=True)
    args = parser.parse_args()
    if not (args.bundle / "SMFTP.exe").is_file():
        raise SystemExit(f"{args.bundle / 'SMFTP.exe'} introuvable : lancez d’abord build.ps1 -Distribution store")
    marker = args.bundle / "_internal" / "resources" / "distribution.txt"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != "store":
        raise SystemExit("Ce dossier n’a pas été construit avec build.ps1 -Distribution store.")
    if not args.publisher.startswith("CN="):
        raise SystemExit("--publisher doit commencer par « CN= » (valeur Partner Center).")
    version = current_version() + ".0"
    staging = args.output / "msix-staging"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(args.bundle, staging)
    make_assets(staging / "Assets")
    (staging / "AppxManifest.xml").write_text(
        manifest(args.name, args.publisher, args.publisher_display_name, version), encoding="utf-8")
    package = args.output / f"SMFTP-{version}-x64.msix"
    subprocess.run([find_makeappx(), "pack", "/o", "/d", str(staging), "/p", str(package)], check=True)
    shutil.rmtree(staging)
    print(f"Paquet MSIX : {package}")


if __name__ == "__main__":
    main()
