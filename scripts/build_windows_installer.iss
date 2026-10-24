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
  #ifdef MyIsMediaTek
    #define MyAppName "MediaTek Installer"
  #else
    #define MyAppName "Innioasis Updater CE"
  #endif
#endif
; Install folder under Program Files (or x86) / the per-user Programs folder.
; Updater CE lives in UpdaterCE; the generic MediaTek Installer keeps its own.
#ifndef MyAppDirName
  #ifdef MyIsMediaTek
    #define MyAppDirName "MediaTek Installer"
  #else
    #define MyAppDirName "UpdaterCE"
  #endif
#endif
#ifndef MyAppGroup
  #define MyAppGroup MyAppName
#endif
#ifndef MyAppVersion
  #define MyAppVersion "2.5.0"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "Ryan Specter"
#endif
#ifndef MyAppURL
  #define MyAppURL "https://github.com/y1-community/updater-ce-neo"
#endif
#ifndef MyAppExeName
  #ifdef MyIsMediaTek
    #define MyAppExeName "MediaTekInstaller.exe"
  #else
    #define MyAppExeName "InnioasisUpdater.exe"
  #endif
#endif
#ifndef MyDistDir
  #ifdef MyIsMediaTek
    #define MyDistDir "..\dist\windows\MediaTekInstaller"
  #else
    #define MyDistDir "..\dist\windows\InnioasisUpdater"
  #endif
#endif
#ifndef MyOutputBase
  #ifdef MyIsMediaTek
    #define MyOutputBase "MediaTekInstaller-Setup"
  #else
    #define MyOutputBase "UpdaterCE-Setup"
  #endif
#endif

; The SP Flash Tool and MediaTek Download Agents are the device flashing payload.
; Refuse to compile an installer that would ship without them.
#if !FileExists(AddBackslash(MyDistDir) + "SP_Flash_Tool\flash_tool.exe")
  #error "SP Flash Tool (flash_tool.exe) is missing from the payload. Run build_windows.ps1 again."
#endif
#if !FileExists(AddBackslash(MyDistDir) + "SP_Flash_Tool\MTK_AllInOne_DA.bin")
  #error "MediaTek Download Agent MTK_AllInOne_DA.bin is missing from the payload."
#endif
#if !FileExists(AddBackslash(MyDistDir) + "SP_Flash_Tool\DA_PL.bin")
  #error "MediaTek Download Agent DA_PL.bin is missing from the payload."
#endif
#if !FileExists(AddBackslash(MyDistDir) + "SP_Flash_Tool\DA_SWSEC.bin")
  #error "MediaTek Download Agent DA_SWSEC.bin is missing from the payload."
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
; The install is always all users, so {autopf} is Program Files.
DefaultDirName={autopf}\{#MyAppDirName}
DefaultGroupName={#MyAppGroup}
DisableProgramGroupPage=yes
; Same AppId updates an existing install in place. A previous directory is
; kept, except the retired LocalAppData\Innioasis Updater folder, which the
; [Code] section sends to {autopf} instead.
UsePreviousAppDir=yes
UsePreviousGroup=yes
UsePreviousTasks=yes
; All users (admin) is the only install mode. Running processes are stopped
; explicitly in CurStepChanged(ssInstall), so the Restart Manager prompt is off.
PrivilegesRequired=admin
CloseApplications=no
RestartApplications=no
LicenseFile=..\LICENSE
OutputDir=..\dist\windows
OutputBaseFilename={#MyOutputBase}-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Target Windows 8.1 and later (NT 6.3+). Blocks unsupported Windows 7, Vista, and XP.
MinVersion=6.3
; The application is 64-bit only (PySide6 ships no 32-bit Windows wheels).
; Installs are allowed on x64 and arm64 Windows only, and go to the native
; 64-bit Program Files. 32-bit Windows is refused with a clear message.
ArchitecturesAllowed=x64compatible arm64
ArchitecturesInstallIn64BitMode=x64compatible arm64
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Installer
VersionInfoCopyright=Copyright (C) 2024-2026 Innioasis Community

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Dirs]
; The tool writes history.ini and logs beside flash_tool.exe. A standard
; account cannot modify Program Files, so the install grants Users modify
; on this folder only.
Name: "{app}\SP_Flash_Tool"; Permissions: users-modify
Name: "{commonappdata}\SP_FT_Logs"; Permissions: users-modify

[Messages]
FinishedRestartLabel=Setup has finished installing MediaTek SP Driver on your computer. You will need to restart your computer before you can install software on your device.%n%nWould you like to restart now?
FinishedRestartMessage=Setup has finished installing MediaTek SP Driver on your computer. You will need to restart your computer before you can install software on your device.%n%nThe driver requires a reboot to become active. The updater application will launch automatically after the restart.%n%nWould you like to restart now?

[CustomMessages]
english.StepStopping=Stopping running Updater CE, Python, SP Flash Tool and ADB processes...
english.StepRemovingOld=Removing previous versions...
english.StepShortcuts=Removing old shortcuts...
english.StepLegacy=Cleaning up old data and install folders...
english.StepDriverInstall=Installing MediaTek SP Driver...
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
chinesesimplified.StepStopping=正在停止正在运行的 Updater CE、Python、SP Flash Tool 和 ADB 进程...
chinesesimplified.StepRemovingOld=正在删除旧版本...
chinesesimplified.StepShortcuts=正在删除旧快捷方式...
chinesesimplified.StepLegacy=正在清理旧数据和安装文件夹...
chinesesimplified.StepDriverInstall=正在安装联发科 (MediaTek) SP 驱动程序...
#endif

[Files]
; Main application binaries, _internal runtime, and bundled SP Flash Tool 5.1904
; ignoreversion replaces older files, so this same installer updates in place.
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Bundled MediaTek Driver Installer executed during post-install
Source: "..\tools\DriverInstall.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; When no driver was installed, launch the app normally after install.
; When the MediaTek USB driver was installed, do NOT launch the app here —
; the Finish page handles the reboot prompt instead (see CurPageChanged).
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent; Check: Not DriverInstalled

[UninstallDelete]
Type: filesandordirs; Name: "{app}\SP_Flash_Tool\SP_FT_Logs"
Type: filesandordirs; Name: "{app}\SP_Flash_Tool\history.ini"
Type: files; Name: "{app}\updater.log"

[Code]
const
  UninstallRoot = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\';
  CurrentUninstallKey = UninstallRoot + '{#MyAppId}_is1';
  LegacyUninstallKey = UninstallRoot + 'Innioasis Updater_is1';
  { The first Updater CE installer shipped under its own AppId and installed
    into "{autopf}\Ryan Specter". A machine that ran it has a second uninstall
    entry and a second set of shortcuts; both are removed on upgrade. }
  LegacyUpdaterCeKey = UninstallRoot + '{8A1C6F5B-9A3C-4E6D-8F9A-7B3D2E1C0A9F}_is1';
  { Start Menu folders are a few levels deep at most. The bound stops a
    runaway walk from ever hanging the installer. }
  MaxShortcutPurgeDepth = 6;

var
  PriorDesktopShortcut: Boolean;
  DriverInstalled: Boolean;

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

function IsLegacyInstallDir(const Dir: String): Boolean;
var
  L: String;
begin
  { The pre-3.x LocalAppData tree, any 3.x copy still living there, and the
    Program Files "Innioasis Community\Updater CE" (and " 2.5") folders. }
  L := Lowercase(Dir);
  Result :=
    (Pos('\appdata\local\innioasis updater', L) > 0) or
    (Pos('\innioasis community\updater ce', L) > 0) or
    (Pos('\ryan specter', L) > 0) or
    (Pos('\innioasis updater ce', L) > 0);
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

function IsRetiredFolder(const Name: String): Boolean;
begin
  { Every menu/desktop folder that carries our name goes, whatever version
    wrote it: "Innioasis Updater", "Innioasis Updater CE", "Innioasis
    Community" and the SP Flash Tool group. The generic MediaTek Installer
    folder is not named here, so that brand is left alone. }
  Result :=
    NameContains(Name, 'Innioasis') or
    NameContains(Name, 'SP Flash Tool') or
    NameContains(Name, 'Updater CE');
end;

{ Removes a retired Start Menu folder's contents without following junctions
  or symlinks: a reparse point is unlinked itself, never walked, so a link
  into a large tree (for example Documents) cannot make the step run for
  minutes or delete data outside the menu. Depth is bounded as well. }
procedure RemoveRetiredFolderAt(const Path: String; Depth: Integer);
var
  FindRec: TFindRec;
  Child: String;
begin
  if (Depth > MaxShortcutPurgeDepth) or (not DirExists(Path)) then
    Exit;
  if FindFirst(Path + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name = '.') or (FindRec.Name = '..') then
          Continue;
        Child := Path + '\' + FindRec.Name;
        if (FindRec.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then
          RemoveDir(Child)
        else if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          RemoveRetiredFolderAt(Child, Depth + 1)
        else
          DeleteFile(Child);
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  RemoveDir(Path);
end;

{ Drop Start Menu folders named *Innioasis*, and shortcuts for the old
  app or SP Flash Tool, on this user's menus and desktops and on the
  system-wide ones. The new shortcuts are created afterwards from the
  tasks the person just confirmed. }
procedure PurgeShortcutTreeAt(const Root: String; Depth: Integer);
var
  FindRec: TFindRec;
  Path: String;
begin
  if (Root = '') or (Depth > MaxShortcutPurgeDepth) or (not DirExists(Root)) then
    Exit;
  if not FindFirst(Root + '\*', FindRec) then
    Exit;
  try
    repeat
      if (FindRec.Name = '.') or (FindRec.Name = '..') then
        Continue;
      { A junction or symlink (for example a profile alias of another
        account) is skipped. Its target is neither walked nor deleted. }
      if (FindRec.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then
        Continue;
      Path := Root + '\' + FindRec.Name;
      if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
      begin
        if IsRetiredFolder(FindRec.Name) then
          RemoveRetiredFolderAt(Path, Depth + 1)
        else
          PurgeShortcutTreeAt(Path, Depth + 1);
      end
      else if IsRetiredShortcut(FindRec.Name) then
        DeleteFile(Path);
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

procedure PurgeShortcutTree(const Root: String);
begin
  PurgeShortcutTreeAt(Root, 0);
end;

procedure PurgeRetiredShortcuts;
begin
  { Not gated on the brand: a MediaTek Installer install still has to clear the
    "Innioasis" shortcuts an older Updater CE left behind, so only one set of
    menu entries is ever present. Nothing named MediaTek matches below. }
  PurgeShortcutTree(ExpandConstant('{userprograms}'));
  PurgeShortcutTree(ExpandConstant('{userdesktop}'));
  PurgeShortcutTree(ExpandConstant('{commonprograms}'));
  PurgeShortcutTree(ExpandConstant('{commondesktop}'));
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
  if (Dir <> '') and DirExists(Dir) and IsLegacyInstallDir(Dir) then
    DelTree(Dir, True, True, True);
end;

{ A retired Program Files install: run its own uninstaller quietly when it
  has one, then remove the folder. Never touches the folder being installed to. }
procedure RemoveLegacyProgramDir(const Dir: String);
var
  ResultCode: Integer;
begin
  if (not DirExists(Dir)) or
     (CompareText(RemoveBackslashUnlessRoot(Dir), RemoveBackslashUnlessRoot(ExpandConstant('{app}'))) = 0) then
    Exit;
  if FileExists(Dir + '\unins000.exe') then
    Exec(Dir + '\unins000.exe', '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  DelTree(Dir, True, True, True);
end;

procedure RemoveLegacyProgramFilesUnder(const Root: String);
begin
  if Root = '' then
    Exit;
  RemoveLegacyProgramDir(Root + '\Innioasis Community\Updater CE');
  RemoveLegacyProgramDir(Root + '\Innioasis Community\Updater CE 2.5');
  { Only removed if nothing else is left in it. }
  RemoveDir(Root + '\Innioasis Community');
end;

{ Program Files and Program Files (x86) variants of the retired folders. }
procedure RemoveLegacyProgramFiles;
begin
  RemoveLegacyProgramFilesUnder(ExpandConstant('{commonpf64}'));
  RemoveLegacyProgramFilesUnder(ExpandConstant('{commonpf32}'));
end;

{ %LocalAppData%\Innioasis Updater for this account, and for every
  profile when the install is allowed to touch the machine. }
(* Every account profile under {sd}\Users. Only the folder names are needed. *)
function ProfileRoots: TStringList;
var
  UsersRoot: String;
  FindRec: TFindRec;
begin
  Result := TStringList.Create;
  UsersRoot := ExpandConstant('{sd}\Users');
  if FindFirst(UsersRoot + '\*', FindRec) then
  begin
    try
      repeat
        if ((FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and
           (FindRec.Name <> '.') and (FindRec.Name <> '..') then
          Result.Add(UsersRoot + '\' + FindRec.Name);
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

{ A per-user copy from a "just for me" install of the old Inno build. Its own
  uninstaller runs quietly first when present. Skipped if it is the target. }
procedure RemovePerUserInstallDir(const Dir: String);
var
  ResultCode: Integer;
begin
  if (not DirExists(Dir)) or
     (CompareText(RemoveBackslashUnlessRoot(Dir), RemoveBackslashUnlessRoot(ExpandConstant('{app}'))) = 0) then
    Exit;
  if FileExists(Dir + '\unins000.exe') then
    Exec(Dir + '\unins000.exe', '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  DelTree(Dir, True, True, True);
end;

{ Runs the old version's own uninstall.exe from %LocalAppData%\Innioasis Updater,
  if present, and tells the person what is being cleaned up first. }
procedure RunOldLocalUninstaller(const Dir: String);
var
  ResultCode: Integer;
begin
  if not FileExists(Dir + '\uninstall.exe') then
    Exit;
  MsgBox('An older version of Updater CE was found in ' + Dir + '. ' +
    'It will be uninstalled before this version is installed.', mbInformation, MB_OK);
  Exec(Dir + '\uninstall.exe', '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ Every profile's leftovers: the retired LocalAppData data folder, a per-user
  Programs copy, its Start Menu, Desktop and taskbar shortcuts. A locked or
  missing folder is skipped rather than stopping the install. }
procedure RemoveLegacyLocalAppData;
var
  Profiles: TStringList;
  I: Integer;
  P: String;
begin
  if not IsUpdaterCeBrand then
    Exit;
  Profiles := ProfileRoots;
  try
    for I := 0 to Profiles.Count - 1 do
    begin
      P := Profiles[I];
      RunOldLocalUninstaller(P + '\AppData\Local\Innioasis Updater');
      RemoveLegacyDataDir(P + '\AppData\Local\Innioasis Updater');
      RemovePerUserInstallDir(P + '\AppData\Local\Programs\UpdaterCE');
      PurgeShortcutTree(P + '\AppData\Roaming\Microsoft\Windows\Start Menu\Programs');
      PurgeShortcutTree(P + '\Desktop');
      PurgeShortcutTree(P + '\AppData\Roaming\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar');
    end;
  finally
    Profiles.Free;
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
  DriverInstalled := False;
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
    if IsLegacyInstallDir(Prev) then
      WizardForm.DirEdit.Text := ExpandConstant('{autopf}\{#MyAppDirName}');
  end;
  if IsUpdaterCeBrand and (not TasksWereRecorded) and PriorDesktopShortcut then
  begin
    WizardSelectTasks('desktopicon');
  end;
end;

{ Force-stops a process and its children. A process that is not running is
  not an error, so the result is ignored and the install carries on. }
procedure StopProcess(const ImageName: String);
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM ' + ImageName, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ Shows what the installer is doing on the Installing page, so the wait is
  never a blank window. }
procedure ShowStep(const Key: String);
begin
  WizardForm.StatusLabel.Caption := ExpandConstant('{cm:' + Key + '}');
  WizardForm.Update;
end;

procedure RemoveOldInstalls;
var
  Prev, Dest: String;
begin
  { Pre-3.x registered itself under a different uninstall key. }
  RunSilentUninstall(LegacyUninstallKey);
  { The first Updater CE installer (2.5.x) had its own AppId. }
  RunSilentUninstall(LegacyUpdaterCeKey);

  { A copy that is registered, but lives anywhere other than the folder this
    run will install into, is removed first. That covers the old LocalAppData
    tree as well as a 3.1/3.2 that the user pointed at a custom folder, so an
    upgrade never leaves two installed copies or two uninstall entries. An
    install already in the destination folder is updated in place instead. }
  if QueryUninstallValue(CurrentUninstallKey, 'InstallLocation', Prev) then
  begin
    Dest := ExpandConstant('{app}');
    if (Prev <> '') and
       (CompareText(RemoveBackslashUnlessRoot(Prev), RemoveBackslashUnlessRoot(Dest)) <> 0) then
      RunSilentUninstall(CurrentUninstallKey);
  end;
end;

procedure RegisterRunOnceStartup;
var
  ExePath: String;
begin
  ExePath := '"' + ExpandConstant('{app}\{#MyAppExeName}') + '"';
  { Register one-time launch on next reboot in both 64-bit and 32-bit registry views }
  RegWriteStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\RunOnce', '{#MyAppName}', ExePath);
  if IsWin64 then
    RegWriteStringValue(HKLM64, 'Software\Microsoft\Windows\CurrentVersion\RunOnce', '{#MyAppName}', ExePath);
end;

procedure InstallBundledDriver;
var
  DriverExe: String;
  ResultCode: Integer;
begin
  DriverExe := ExpandConstant('{tmp}\DriverInstall.exe');
  if not FileExists(DriverExe) then
    Exit;

  ShowStep('StepDriverInstall');
  { Execute DriverInstall.exe silently or with standard installer arguments without rebooting immediately }
  if Exec(DriverExe, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    DriverInstalled := True;
  end;
end;

function NeedRestart(): Boolean;
begin
  { Inno Setup uses this to decide whether to offer a restart after setup.
    When the MediaTek driver was installed, a reboot is required for the
    drivers to become operational. }
  Result := DriverInstalled;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { When we reach the Finish page and drivers were installed, prompt for reboot
    instead of offering to launch the application (which won't work until reboot). }
  if CurPageID = wpFinished and DriverInstalled then
  begin
    WizardForm.NextButton.Enabled := True;
    WizardForm.NextButton.Caption := 'Restart Now';
    WizardForm.NextButton.OnClick := @RestartComputer;
  end;
end;

procedure RestartComputer;
var
  ResultCode: Integer;
begin
  { Register the app to launch on next login, then restart }
  if DriverInstalled then
    RegisterRunOnceStartup;
  ShellExec('', 'shutdown.exe', '/r /t 0 /c "MediaTek drivers require a restart. The updater will launch after restart."', '', SW_SHOW, ewNoWait, ResultCode);
end;

{ Runs at various stages of setup execution. }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    { Stray Python and Updater processes are stopped for every brand and
      whether or not an old uninstall.exe exists. }
    ShowStep('StepStopping');
    StopProcess('InnioasisUpdater.exe');
    StopProcess('flash_tool.exe');
    StopProcess('adb.exe');
    StopProcess('python.exe');
    StopProcess('pythonw.exe');

    if not IsUpdaterCeBrand then
      Exit;

    ShowStep('StepRemovingOld');
    RemoveOldInstalls;

    ShowStep('StepShortcuts');
    PurgeRetiredShortcuts;

    ShowStep('StepLegacy');
    RemoveLegacyLocalAppData;
    RemoveLegacyProgramFiles;
  end
  else if CurStep = ssPostInstall then
  begin
    InstallBundledDriver;
  end
  else if CurStep = ssDone then
  begin
    { If driver was installed, register RunOnce so the app launches on the next reboot.
      The reboot prompt is shown on the Finish page via CurPageChanged. }
    if DriverInstalled then
      RegisterRunOnceStartup;
  end;
end;

