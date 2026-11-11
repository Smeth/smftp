$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onefile --name SMFTP --icon 'resources\sf-icon.ico' --add-data 'resources;resources' --collect-submodules keyring.backends main.py
exit $LASTEXITCODE
