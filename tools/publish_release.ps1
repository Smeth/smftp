<#
Publish the GitHub release v<version> built by package.ps1 -Target github.

Uploads the three files SMFTP's update system expects in the release:
  SMFTP-<version>-setup-x64.exe, SMFTP-<version>-windows-x64.zip, SHA256SUMS.txt
The installer offered by "Télécharger et installer" is refused by SMFTP unless its
SHA-256 matches SHA256SUMS.txt of the same release: this script checks the files
against SHA256SUMS.txt before uploading anything.

Requires the GitHub CLI (gh), authenticated (gh auth login), and a pushed main branch.

Examples:
  powershell -ExecutionPolicy Bypass -File .\tools\publish_release.ps1
  powershell -ExecutionPolicy Bypass -File .\tools\publish_release.ps1 -NotesFile docs\RELEASE_NOTES_1.2.0.md -Draft
#>
param(
    [string]$NotesFile = '',
    [switch]$Draft
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

# Version: the single source is CURRENT_VERSION in smftp\workers\updates.py.
$text = Get-Content -Raw -Encoding UTF8 (Join-Path $root 'smftp\workers\updates.py')
if ($text -notmatch 'CURRENT_VERSION = "(\d+\.\d+\.\d+)"') { throw 'CURRENT_VERSION introuvable dans smftp\workers\updates.py.' }
$version = $Matches[1]
$tag = "v$version"

if (-not $NotesFile) { $NotesFile = Join-Path $root "docs\RELEASE_NOTES_$version.md" }
if (-not (Test-Path -LiteralPath $NotesFile)) { throw "Notes de version introuvables : $NotesFile" }

$dist = Join-Path $root 'dist'
$setupName = "SMFTP-$version-setup-x64.exe"
$zipName = "SMFTP-$version-windows-x64.zip"
$files = @((Join-Path $dist $setupName), (Join-Path $dist $zipName), (Join-Path $dist 'SHA256SUMS.txt'))
foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Fichier manquant : $file (lancez d'abord .\package.ps1 -Target github)." }
}

# Every published file must match SHA256SUMS.txt, otherwise SMFTP would refuse the update.
$sums = @{}
foreach ($line in Get-Content -LiteralPath $files[2]) {
    if ($line -match '^([0-9a-f]{64})\s+\*?(\S.*)$') { $sums[$Matches[2].Trim()] = $Matches[1] }
}
foreach ($name in @($setupName, $zipName)) {
    if (-not $sums.ContainsKey($name)) { throw "SHA256SUMS.txt ne contient pas $name." }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $dist $name)).Hash.ToLowerInvariant()
    if ($actual -ne $sums[$name]) { throw "Empreinte différente pour $name : relancez package.ps1." }
}
Write-Host 'Empreintes vérifiées.'

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'GitHub CLI (gh) introuvable : winget install GitHub.cli' }
$arguments = @('release', 'create', $tag) + $files + @('--title', "SMFTP $version", '--notes-file', $NotesFile)
if ($Draft) { $arguments += '--draft' }
& gh @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Release $tag publiée."
exit 0
