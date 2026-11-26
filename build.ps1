param(
    # store: Microsoft Store build, updated by the Store (no GitHub update check).
    # uptodown / github: the application checks GitHub releases itself.
    [ValidateSet('github', 'store', 'uptodown')]
    [string]$Distribution = 'github'
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

& $python tools\version_info.py build\version_info.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# numpy and PIL are pulled in by over-broad analysis only; SMFTP and the Claude SDK do not use them.
# --onedir keeps Qt, PySide6 and paramiko as separate, replaceable files, as the LGPL requires.
# --noupx: UPX-packed executables are often flagged by antivirus scanners (VirusTotal, SmartScreen).
& $python -m PyInstaller --noconfirm --clean --windowed --onedir --noupx --name SMFTP `
    --icon 'resources\sf-icon.ico' --version-file 'build\version_info.txt' `
    --add-data 'resources;resources' --collect-submodules keyring.backends `
    --exclude-module numpy --exclude-module PIL main.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python tools\prune_bundle.py dist\SMFTP
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Read at startup by smftp.workers.updates.distribution().
$marker = Join-Path $PSScriptRoot 'dist\SMFTP\_internal\resources\distribution.txt'
[System.IO.File]::WriteAllText($marker, $Distribution)
Write-Host "Distribution : $Distribution"

& $python tools\collect_licenses.py dist\SMFTP
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Start the packaged application once: a missing library fails the build here.
$smoke = Join-Path ([System.IO.Path]::GetTempPath()) ("smftp-smoke-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $smoke | Out-Null
$env:SMFTP_DATA_DIR = Join-Path $smoke 'data'
try {
    $process = Start-Process -FilePath (Join-Path $PSScriptRoot 'dist\SMFTP\SMFTP.exe') -ArgumentList '--smoke-test' `
        -WorkingDirectory $smoke -PassThru
    # A startup error opens a traceback window: do not wait for it forever.
    $process | Wait-Process -Timeout 90 -ErrorAction SilentlyContinue
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        Write-Error "L'exécutable ne s'est pas fermé après le test de démarrage (erreur au lancement ?)."
    }
    if ($process.ExitCode -ne 0) {
        Write-Error "Le test de démarrage de l'exécutable a échoué (code $($process.ExitCode))."
    }
    Write-Host 'Test de démarrage de l''exécutable réussi.'
} finally {
    Remove-Item Env:\SMFTP_DATA_DIR
    Remove-Item -Recurse -Force -LiteralPath $smoke
}
exit 0
