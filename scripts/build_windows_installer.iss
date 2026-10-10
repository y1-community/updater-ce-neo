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
; {autopf} is Program Files for an all-users install and the per-user
; Programs folder when the person chooses "just for me". Either way it is
; not the old %LocalAppData%\Innioasis Updater directory.
DefaultDirName={autopf}\{#MyAppDirParent}\{#MyAppName}
DefaultGroupName={#MyAppGroup}
DisableProgramGroupPage=yes
; Same AppId updates an existing install in place. A previous directory is
; kept, except the retired LocalAppData\Innioasis Updater folder, which the
; [Code] section sends to {autopf} instead.
UsePreviousAppDir=yes
UsePreviousTasks=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
RestartApplications=no
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

[Dirs]
; The tool writes history.ini and logs beside flash_tool.exe. A standard
; account cannot modify Program Files, so an all-users install grants Users
; modify on this folder only. A per-user install is already writable.
Name: "{app}\SP_Flash_Tool"; Permissions: users-modify; Check: IsAdminInstallMode
Name: "{commonappdata}\SP_FT_Logs"; Permissions: users-modify; Check: IsAdminInstallMode

[Files]
; Main application binaries, _internal runtime, and bundled SP Flash Tool 5.1904
; ignoreversion replaces older files, so this same installer updates in place.
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
const
  UninstallRoot = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\';
  CurrentUninstallKey = UninstallRoot + '{#MyAppId}_is1';
  LegacyUninstallKey = UninstallRoot + 'Innioasis Updater_is1';

var
  PriorDesktopShortcut: Boolean;

function IsUpdaterCeBrand: Boolean;
begin
#ifdef MyIsMediaTek
  Result := False;
#else
  Result := True;
#endif
end;

function QueryUninstallValue(const SubKey, ValueName: String; var Value: String): Boolean;
begin
  Result :=
    RegQueryStringValue(HKLM64, SubKey, ValueName, Value) or
    RegQueryStringValue(HKLM32, SubKey, ValueName, Value) or
    RegQueryStringValue(HKCU, SubKey, ValueName, Value);
end;

function IsLegacyLocalInstall(const Dir: String): Boolean;
begin
  { The pre-3.x tree, and any 3.x copy that was still living there. }
  Result := Pos('\appdata\local\innioasis updater', Lowercase(Dir)) > 0;
end;

function UninstallerExe(const Uninst: String): String;
var
  S: String;
  P: Integer;
begin
  S := Trim(Uninst);
  if (Length(S) > 1) and (S[1] = '"') then
  begin
    Delete(S, 1, 1);
    P := Pos('"', S);
    if P > 0 then
      S := Copy(S, 1, P - 1);
  end
  else
  begin
    P := Pos(' ', S);
    if P > 0 then
      S := Copy(S, 1, P - 1);
  end;
  Result := S;
end;

procedure RunSilentUninstall(const SubKey: String);
var
  Uninst, Exe: String;
  ResultCode: Integer;
begin
  if not QueryUninstallValue(SubKey, 'UninstallString', Uninst) then
    Exit;
  Exe := UninstallerExe(Uninst);
  if (Exe = '') or (not FileExists(Exe)) then
    Exit;
  Exec(Exe, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function NameContains(const Name, Needle: String): Boolean;
begin
  Result := Pos(Lowercase(Needle), Lowercase(Name)) > 0;
end;

function IsRetiredShortcut(const Name: String): Boolean;
begin
  Result :=
    NameContains(Name, 'Innioasis') or
    NameContains(Name, 'SP Flash Tool') or
    NameContains(Name, 'Updater CE');
end;

{ Drop Start Menu folders named *Innioasis*, and shortcuts for the old
  app or SP Flash Tool, on this user's menus and desktops and on the
  system-wide ones. The new shortcuts are created afterwards from the
  tasks the person just confirmed. }
procedure PurgeShortcutTree(const Root: String);
var
  FindRec: TFindRec;
  Path: String;
begin
  if (Root = '') or (not DirExists(Root)) then
    Exit;
  if not FindFirst(Root + '\*', FindRec) then
    Exit;
  try
    repeat
      if (FindRec.Name = '.') or (FindRec.Name = '..') then
        Continue;
      Path := Root + '\' + FindRec.Name;
      if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
      begin
        if NameContains(FindRec.Name, 'Innioasis') then
          DelTree(Path, True, True, True)
        else
          PurgeShortcutTree(Path);
      end
      else if IsRetiredShortcut(FindRec.Name) then
        DeleteFile(Path);
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

procedure PurgeRetiredShortcuts;
begin
  if not IsUpdaterCeBrand then
    Exit;
  PurgeShortcutTree(ExpandConstant('{userprograms}'));
  PurgeShortcutTree(ExpandConstant('{userdesktop}'));
  PurgeShortcutTree(ExpandConstant('{commonprograms}'));
  PurgeShortcutTree(ExpandConstant('{commondesktop}'));
end;

function DirHasRetiredShortcut(const Dir: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := False;
  if (Dir = '') or (not DirExists(Dir)) then
    Exit;
  if not FindFirst(Dir + '\*', FindRec) then
    Exit;
  try
    repeat
      if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0 then
      begin
        if IsRetiredShortcut(FindRec.Name) then
        begin
          Result := True;
          Exit;
        end;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

procedure RemoveLegacyDataDir(const Dir: String);
begin
  if (Dir <> '') and DirExists(Dir) and IsLegacyLocalInstall(Dir) then
    DelTree(Dir, True, True, True);
end;

{ %LocalAppData%\Innioasis Updater for this account, and for every
  profile when the install is allowed to touch the machine. }
procedure RemoveLegacyLocalAppData;
var
  UsersRoot, ProfileDir: String;
  FindRec: TFindRec;
begin
  if not IsUpdaterCeBrand then
    Exit;
  RemoveLegacyDataDir(ExpandConstant('{localappdata}\Innioasis Updater'));
  if not IsAdminInstallMode then
    Exit;
  UsersRoot := ExpandConstant('{sd}\Users');
  if not FindFirst(UsersRoot + '\*', FindRec) then
    Exit;
  try
    repeat
      if ((FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and
         (FindRec.Name <> '.') and (FindRec.Name <> '..') then
      begin
        ProfileDir := UsersRoot + '\' + FindRec.Name + '\AppData\Local\Innioasis Updater';
        RemoveLegacyDataDir(ProfileDir);
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

function TasksWereRecorded: Boolean;
var
  Tasks: String;
begin
  Result := QueryUninstallValue(CurrentUninstallKey, 'Inno Setup: Selected Tasks', Tasks);
end;

function InitializeSetup(): Boolean;
begin
  { Any earlier release of this AppId is updated by installing over it.
    There is no version floor: the same file is a clean install and an update. }
  Result := True;
  PriorDesktopShortcut := False;
  if IsUpdaterCeBrand then
  begin
    PriorDesktopShortcut :=
      DirHasRetiredShortcut(ExpandConstant('{userdesktop}')) or
      DirHasRetiredShortcut(ExpandConstant('{commondesktop}'));
  end;
end;

procedure InitializeWizard;
var
  Prev: String;
begin
  if IsUpdaterCeBrand then
  begin
    Prev := WizardForm.DirEdit.Text;
    if IsLegacyLocalInstall(Prev) then
      WizardForm.DirEdit.Text := ExpandConstant('{autopf}\{#MyAppDirParent}\{#MyAppName}');
  end;
  if IsUpdaterCeBrand and (not TasksWereRecorded) and PriorDesktopShortcut then
  begin
    WizardSelectTasks('desktopicon');
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Prev, Dest: String;
begin
  Result := '';
  NeedsRestart := False;
  if not IsUpdaterCeBrand then
    Exit;

  { Pre-3.x registered itself under a different uninstall key. }
  RunSilentUninstall(LegacyUninstallKey);

  { A 3.x copy that still lives in LocalAppData is removed so the new
    files and shortcuts are the Program Files (or per-user Programs) ones.
    An install that is already in the destination folder is updated in place. }
  if QueryUninstallValue(CurrentUninstallKey, 'InstallLocation', Prev) then
  begin
    Dest := ExpandConstant('{app}');
    if IsLegacyLocalInstall(Prev) and
       (CompareText(RemoveBackslashUnlessRoot(Prev), RemoveBackslashUnlessRoot(Dest)) <> 0) then
      RunSilentUninstall(CurrentUninstallKey);
  end;

  PurgeRetiredShortcuts;
  RemoveLegacyLocalAppData;
end;

