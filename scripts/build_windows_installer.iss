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

[Code]
// Helper to compare two semantic version strings (e.g. '3.0.2' and '3.0.7')
function CompareVersion(V1, V2: String): Integer;
var
  P1, P2, N1, N2: Integer;
begin
  Result := 0;
  while (Length(V1) > 0) or (Length(V2) > 0) do
  begin
    P1 := Pos('.', V1);
    if P1 > 0 then
    begin
      N1 := StrToIntDef(Copy(V1, 1, P1 - 1), 0);
      Delete(V1, 1, P1);
    end
    else
    begin
      N1 := StrToIntDef(V1, 0);
      V1 := '';
    end;

    P2 := Pos('.', V2);
    if P2 > 0 then
    begin
      N2 := StrToIntDef(Copy(V2, 1, P2 - 1), 0);
      Delete(V2, 1, P2);
    end
    else
    begin
      N2 := StrToIntDef(V2, 0);
      V2 := '';
    end;

    if N1 > N2 then
    begin
      Result := 1;
      Exit;
    end
    else if N1 < N2 then
    begin
      Result := -1;
      Exit;
    end;
  end;
end;

// Detect and automatically remove legacy pre-3.0 software, and update existing 3.0 releases if newer than 3.0.2
function InitializeSetup(): Boolean;
var
  UninstPath: String;
  InstalledVer: String;
  ResultCode: Integer;
begin
  Result := True;

  // 1. Check for legacy pre-3.0 Innioasis Updater uninstall registry entry in 64-bit and 32-bit registry
  if RegQueryStringValue(HKLM64, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\Innioasis Updater_is1', 'UninstallString', UninstPath) or
     RegQueryStringValue(HKLM32, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\Innioasis Updater_is1', 'UninstallString', UninstPath) or
     RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\Innioasis Updater_is1', 'UninstallString', UninstPath) then
  begin
    // Run legacy uninstaller silently
    Exec(RemoveQuotes(UninstPath), '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  // 2. Check existing version of Updater CE
  if RegQueryStringValue(HKLM64, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1', 'DisplayVersion', InstalledVer) or
     RegQueryStringValue(HKLM32, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1', 'DisplayVersion', InstalledVer) or
     RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1', 'DisplayVersion', InstalledVer) then
  begin
    // Update existing 3.0 releases if incoming version is newer than 3.0.2
    if (CompareVersion(InstalledVer, '3.0.0') >= 0) and (CompareVersion(InstalledVer, '3.0.2') <= 0) then
    begin
      if CompareVersion('{#MyAppVersion}', '3.0.2') <= 0 then
      begin
        MsgBox('An existing release of Updater CE (' + InstalledVer + ') is installed. Version must be newer than 3.0.2 to update.', mbInformation, MB_OK);
        Result := False;
        Exit;
      end;
    end;
  end;
end;

