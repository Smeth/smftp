; Windows installer for SMFTP (Uptodown, GitHub releases).
; Built by package.ps1: ISCC.exe /DAppVersion=1.1.0 /DSourceDir=..\dist\SMFTP installer\smftp.iss
; The Microsoft Store build uses the MSIX package instead (see package.ps1 -Target store).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\SMFTP"
#endif

[Setup]
; Never change AppId: Windows uses it to find the installed copy for updates and removal.
AppId={{6F1C9E52-8B0D-4C47-A3E5-5D2B7A9C1E04}
AppName=SMFTP
AppVersion={#AppVersion}
AppVerName=SMFTP {#AppVersion}
AppPublisher=Smeth
AppPublisherURL=https://github.com/Smeth/smftp
AppSupportURL=https://github.com/Smeth/smftp/issues
AppUpdatesURL=https://github.com/Smeth/smftp/releases
VersionInfoVersion={#AppVersion}.0
VersionInfoDescription=Installation de SMFTP
DefaultDirName={autopf}\SMFTP
DefaultGroupName=SMFTP
DisableProgramGroupPage=yes
; Per-user installation by default (no administrator rights); "for all users" stays available.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
LicenseFile=..\LICENSE.md
SetupIconFile=..\resources\sf-icon.ico
UninstallDisplayIcon={app}\SMFTP.exe
UninstallDisplayName=SMFTP
OutputDir=..\dist
OutputBaseFilename=SMFTP-{#AppVersion}-setup-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
; The installer speaks the Windows display language (English otherwise).
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SMFTP"; Filename: "{app}\SMFTP.exe"
Name: "{group}\{cm:UninstallProgram,SMFTP}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\SMFTP"; Filename: "{app}\SMFTP.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SMFTP.exe"; Description: "{cm:LaunchProgram,SMFTP}"; Flags: nowait postinstall skipifsilent

; Profiles, known_hosts and the transfer queue stay in %APPDATA%\SMFTP and the
; passwords in the Windows Credential Manager: uninstalling keeps them.
