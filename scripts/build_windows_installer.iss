; ==============================================================================
; Updater CE — Windows Installer (Inno Setup 6)
; Repository: https://github.com/y1-community/updater-ce-neo
; ==============================================================================

; Every name below can be overridden from the command line, which is how the
; generic MediaTek Installer build (BUILD_BRAND=mediatek_installer) installs
; under its own name and from its own dist folder:
;   ISCC.exe /DMyIsMediaTek /DMyAppName="MediaTek Installer" /DMyDistDir="..\dist\MediaTekInstaller" ...
; /DMyIsMediaTek also gives the two apps separate [Setup] identities, so both
; can be installed side by side instead of replacing each other.
#ifndef MyAppId
  #ifdef MyIsMediaTek
    #define MyAppId "{{5B1D7E4C-9A2F-4C57-8F16-2E7B6A4D9C31}"
  #else
    #define MyAppId "{{C78F982D-7B5F-48A1-8E23-DF3A0210A2B9}"
  #endif
#endif
#ifndef MyAppName
  #define MyAppName "Updater CE"
#endif
; Where the app lives / which Start Menu folder it lands in. The generic
; MediaTek Installer is not an Innioasis product, so it stands alone.
#ifndef MyAppDirParent
  #ifdef MyIsMediaTek
    #define MyAppDirParent ""
  #else
    #define MyAppDirParent "Innioasis Community"
  #endif
#endif
#ifndef MyAppGroup
  #ifdef MyIsMediaTek
    #define MyAppGroup MyAppName
  #else
    #define MyAppGroup "Innioasis Community"
  #endif
#endif
#ifndef MyAppVersion
  #define MyAppVersion "3.0.0"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "Innioasis Community"
#endif
#ifndef MyAppURL
  #define MyAppURL "https://github.com/y1-community/updater-ce-neo"
#endif
#ifndef MyAppExeName
  #define MyAppExeName "InnioasisUpdater.exe"
#endif
#ifndef MyDistDir
  #define MyDistDir "..\dist\InnioasisUpdater"
#endif
#ifndef MyOutputBase
  #define MyOutputBase "UpdaterCE-Setup"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppDirParent}\{#MyAppName}
DefaultGroupName={#MyAppGroup}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename={#MyOutputBase}-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible arm64
ArchitecturesInstallIn64BitMode=x64compatible arm64
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Installer
VersionInfoCopyright=Copyright (C) 2024-2026 Innioasis Community

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Main application binaries, _internal runtime, and bundled SP Flash Tool 5.1904
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\SP_Flash_Tool\SP_FT_Logs"
Type: filesandordirs; Name: "{app}\SP_Flash_Tool\history.ini"
Type: files; Name: "{app}\updater.log"
