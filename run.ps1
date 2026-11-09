$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host 'Préparez le projet : python -m venv .venv puis .\.venv\Scripts\python -m pip install -r requirements.txt'
    exit 1
}
& $pythonPath main.py
exit $LASTEXITCODE
