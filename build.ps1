$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# numpy and PIL are pulled in by over-broad analysis only; SMFTP and the Claude SDK do not use them.
# --onedir keeps Qt, PySide6 and paramiko as separate, replaceable files, as the LGPL requires.
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir --name SMFTP --icon 'resources\sf-icon.ico' --add-data 'resources;resources' --collect-submodules keyring.backends --exclude-module numpy --exclude-module PIL main.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe tools\collect_licenses.py dist\SMFTP
exit $LASTEXITCODE
