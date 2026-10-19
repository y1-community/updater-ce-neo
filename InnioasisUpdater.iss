; Inno Setup script for Updater CE
; Run from the project root after building the portable exe.
; Compassionate usage only — see README.md.

#define MyAppName "Innioasis Updater CE"
#define MyAppVersion "2.5.0"
#define MyAppPublisher "Ryan Specter"
#define MyAppURL "https://innioasis.app"
#define MyAppExeName "InnioasisUpdater.exe"

[Setup]
AppId={{8A1C6F5B-9A3C-4E6D-8F9A-7B3D2E1C0A9F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppPublisher}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=assets/LICENSE.txt
OutputDir=dist\windows
OutputBaseFilename=UpdaterCE-Setup-2.5.0
SetupIconFile=assets/icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
VersionInfoVersion=2.5.0
VersionInfoCompany=Innioasis Updater CE
VersionInfoDescription=Updater CE installer
VersionInfoProductName=Updater CE
VersionInfoProductVersion=2.5.0

[Tasks]
Name: desktopicon; Description: Create a &desktop shortcut; Flags: unchecked

[Files]
Source: "dist\windows\InnioasisUpdater\InnioasisUpdater.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\windows\InnioasisUpdater\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; SP Flash Tool payload sits next to the exe in the portable folder.
Source: "dist\windows\InnioasisUpdater\SP_Flash_Tool\*"; DestDir: "{app}\SP_Flash_Tool"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: {group}\{#MyAppName}; Filename: "{app}\{#MyAppExeName}"
Name: {group}\Uninstall {#MyAppName}; Filename: "{uninstallexe}"
Name: {autodesktop}\{#MyAppName}; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: Launch {#MyAppName}; Flags: nowait postinstall skipifsilent
