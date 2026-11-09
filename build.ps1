$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir --name SMFTP --add-data 'resources;resources' --collect-submodules keyring.backends main.py
exit $LASTEXITCODE
