<#
Build a release for one channel.

  github / uptodown : dist\SMFTP-<version>-setup-x64.exe (Inno Setup 6), a portable zip
                      and dist\SHA256SUMS.txt (checked by SMFTP before installing an update;
                      publish them with tools\publish_release.ps1).
  store             : dist\SMFTP-<version>.0-x64.msix for Partner Center (signed by the Store).

Signing (github / uptodown): pass the thumbprint of a code-signing certificate
installed in the user's certificate store. Without it the files are not signed
and Windows SmartScreen warns on first launch.

Examples:
  .\package.ps1 -Target uptodown -CertificateThumbprint 0123ABCD...
  .\package.ps1 -Target store -IdentityName 12345Smeth.SMFTP -Publisher "CN=..." -PublisherDisplayName Smeth
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('github', 'store', 'uptodown')]
    [string]$Target,
    [string]$CertificateThumbprint = '',
    [string]$TimestampUrl = 'http://timestamp.digicert.com',
    [string]$IdentityName = '',
    [string]$Publisher = '',
    [string]$PublisherDisplayName = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if ($Target -eq 'store' -and (-not $IdentityName -or -not $Publisher -or -not $PublisherDisplayName)) {
    throw 'Pour le Store : -IdentityName, -Publisher et -PublisherDisplayName (Partner Center > Identité du produit).'
}

& (Join-Path $PSScriptRoot 'build.ps1') -Distribution $Target
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$version = (& $python -c "import sys; sys.path.insert(0, 'tools'); from version_info import current_version; print(current_version())").Trim()

function Find-SdkTool([string]$name) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $found = Get-ChildItem -Path $kits -Filter $name -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like '*\x64\*' } | Sort-Object FullName | Select-Object -Last 1
    if (-not $found) { throw "$name introuvable : installez le SDK Windows." }
    return $found.FullName
}

function Sign-Files([string[]]$paths) {
    if (-not $CertificateThumbprint) { return }
    $signtool = Find-SdkTool 'signtool.exe'
    & $signtool sign /sha1 $CertificateThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 /d SMFTP $paths
    if ($LASTEXITCODE -ne 0) { throw 'La signature a échoué.' }
}

function Write-Checksums([string[]]$paths) {
    # SHA256SUMS.txt of the release: SMFTP refuses to run a downloaded installer that does not match it.
    $lines = foreach ($path in $paths) {
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
        "$hash  $([System.IO.Path]::GetFileName($path))"
    }
    $sums = Join-Path $PSScriptRoot 'dist\SHA256SUMS.txt'
    [System.IO.File]::WriteAllText($sums, (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Empreintes : $sums"
}

if ($Target -eq 'store') {
    & $python tools\make_msix.py dist\SMFTP dist --name $IdentityName --publisher $Publisher --publisher-display-name $PublisherDisplayName
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    exit 0
}

Sign-Files @((Join-Path $PSScriptRoot 'dist\SMFTP\SMFTP.exe'))

$zip = Join-Path $PSScriptRoot "dist\SMFTP-$version-windows-x64.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip }
Compress-Archive -Path (Join-Path $PSScriptRoot 'dist\SMFTP') -DestinationPath $zip
Write-Host "Archive portable : $zip"

$iscc = (Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    foreach ($candidate in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                             "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
        if (Test-Path -LiteralPath $candidate) { $iscc = $candidate; break }
    }
}
if (-not $iscc) {
    Write-Warning 'Inno Setup 6 introuvable : installeur non créé (winget install JRSoftware.InnoSetup).'
    Write-Checksums @($zip)
    exit 0
}
& $iscc "/DAppVersion=$version" "/DSourceDir=$(Join-Path $PSScriptRoot 'dist\SMFTP')" (Join-Path $PSScriptRoot 'installer\smftp.iss')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$setup = Join-Path $PSScriptRoot "dist\SMFTP-$version-setup-x64.exe"
Sign-Files @($setup)
Write-Host "Installeur : $setup"
Write-Checksums @($setup, $zip)
exit 0
