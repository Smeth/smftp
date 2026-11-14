$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# --onedir keeps Qt, PySide6 and paramiko as separate, replaceable files, as the LGPL requires.
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir --name SMFTP --icon 'resources\sf-icon.ico' --add-data 'resources;resources' --collect-submodules keyring.backends main.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe tools\collect_licenses.py dist\SMFTP
exit $LASTEXITCODE
