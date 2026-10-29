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
  #define MyAppVersion "2.5.1"
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
#ifndef MyRepoRoot
  #if FileExists("..\LICENSE")
    #define MyRepoRoot "..\"
  #else
    #define MyRepoRoot ""
  #endif
#endif

#ifndef MyDistDir
  #ifdef MyIsMediaTek
    #define MyDistDir MyRepoRoot + "dist\windows\MediaTekInstaller"
  #else
    #define MyDistDir MyRepoRoot + "dist\windows\InnioasisUpdater"
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
LicenseFile={#MyRepoRoot}LICENSE
OutputDir={#MyRepoRoot}dist\windows
OutputBaseFilename={#MyOutputBase}-{#MyAppVersion}
SetupIconFile={#MyRepoRoot}assets\icon.ico
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
#ifdef MyIsMediaTek
UninstallDisplayIcon={app}\SP_Flash_Tool\flash_tool.exe
#else
UninstallDisplayIcon={app}\{#MyAppExeName}
#endif
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Installer
VersionInfoCopyright=Copyright (C) 2024-2026 Innioasis Community

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#if FileExists(AddBackslash(CompilerPath) + "Languages\Arabic.isl")
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Armenian.isl")
Name: "armenian"; MessagesFile: "compiler:Languages\Armenian.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\BrazilianPortuguese.isl")
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Bulgarian.isl")
Name: "bulgarian"; MessagesFile: "compiler:Languages\Bulgarian.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Catalan.isl")
Name: "catalan"; MessagesFile: "compiler:Languages\Catalan.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseTraditional.isl")
Name: "chinesetraditional"; MessagesFile: "compiler:Languages\ChineseTraditional.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Corsican.isl")
Name: "corsican"; MessagesFile: "compiler:Languages\Corsican.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Czech.isl")
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Danish.isl")
Name: "danish"; MessagesFile: "compiler:Languages\Danish.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Dutch.isl")
Name: "dutch"; MessagesFile: "compiler:Languages\Dutch.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Finnish.isl")
Name: "finnish"; MessagesFile: "compiler:Languages\Finnish.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\French.isl")
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\German.isl")
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Hebrew.isl")
Name: "hebrew"; MessagesFile: "compiler:Languages\Hebrew.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Hungarian.isl")
Name: "hungarian"; MessagesFile: "compiler:Languages\Hungarian.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Icelandic.isl")
Name: "icelandic"; MessagesFile: "compiler:Languages\Icelandic.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Italian.isl")
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Japanese.isl")
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Norwegian.isl")
Name: "norwegian"; MessagesFile: "compiler:Languages\Norwegian.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Polish.isl")
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Portuguese.isl")
Name: "portuguese"; MessagesFile: "compiler:Languages\Portuguese.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Russian.isl")
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Slovak.isl")
Name: "slovak"; MessagesFile: "compiler:Languages\Slovak.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Slovenian.isl")
Name: "slovenian"; MessagesFile: "compiler:Languages\Slovenian.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Spanish.isl")
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Swedish.isl")
Name: "swedish"; MessagesFile: "compiler:Languages\Swedish.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Tamil.isl")
Name: "tamil"; MessagesFile: "compiler:Languages\Tamil.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Turkish.isl")
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Ukrainian.isl")
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
#endif

#ifdef MyIsMediaTek
[Types]
Name: "full"; Description: "{cm:TypeFull}"
Name: "custom"; Description: "{cm:TypeCustom}"; Flags: iscustom

[Components]
Name: "mediatek_installer"; Description: "{cm:CompMediaTekInstaller}"; Types: full custom
Name: "sp_flash_tool"; Description: "{cm:CompSpFlashTool}"; Types: full custom; Flags: fixed
#endif

[Tasks]
Name: "startmenuicon"; Description: "{cm:CreateStartMenuIcon}"; GroupDescription: "{cm:AdditionalIcons}"
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
CreateStartMenuIcon=Create Start Menu shortcuts
SpFlashToolShortcut=SP Flash Tool
CompMediaTekInstaller=MediaTek Installer (Guided Firmware & Scatter Installer)
CompSpFlashTool=SP Flash Tool (Required)
TypeFull=Full installation
TypeCustom=Custom installation
StepStopping=Stopping running Updater CE, Python, SP Flash Tool and ADB processes...
StepRemovingOld=Removing previous versions...
StepShortcuts=Removing old shortcuts...
StepLegacy=Cleaning up old data and install folders...
StepDriverInstall=Installing MediaTek SP Driver...
RestartPcToFinish=Restart PC to finish setup
#if FileExists(AddBackslash(CompilerPath) + "Languages\Arabic.isl")
arabic.CreateStartMenuIcon=إنشاء اختصارات في قائمة ابدأ
arabic.SpFlashToolShortcut=SP Flash Tool
arabic.CompMediaTekInstaller=MediaTek Installer (مثبت البرامج الثابتة الموجّه)
arabic.CompSpFlashTool=SP Flash Tool (مطلوب)
arabic.TypeFull=تثبيت كامل
arabic.TypeCustom=تثبيت مخصص
arabic.StepStopping=جارٍ إيقاف عمليات Updater CE وPython وSP Flash Tool وADB...
arabic.StepRemovingOld=جارٍ إزالة الإصدارات السابقة...
arabic.StepShortcuts=جارٍ إزالة الاختصارات القديمة...
arabic.StepLegacy=جارٍ تنظيف البيانات ومجلدات التثبيت القديمة...
arabic.StepDriverInstall=جارٍ تثبيت برنامج تشغيل MediaTek SP...
arabic.RestartPcToFinish=إعادة تشغيل الكمبيوتر لإكمال الإعداد
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Armenian.isl")
armenian.CreateStartMenuIcon=Ստեղծել պիտակներ «Սկիզբ» ընտրացանկում
armenian.SpFlashToolShortcut=SP Flash Tool
armenian.CompMediaTekInstaller=MediaTek Installer
armenian.CompSpFlashTool=SP Flash Tool (Պարտադիր)
armenian.TypeFull=Լրիվ տեղադրում
armenian.TypeCustom=Ընտրովի տեղադրում
armenian.StepStopping=Կանգնեցվում են գործող գործընթացները...
armenian.StepRemovingOld=Հեռացվում են նախորդ տարբերակները...
armenian.StepShortcuts=Հեռացվում են հին պիտակները...
armenian.StepLegacy=Մաքրվում են հին տվյալները և պանակները...
armenian.StepDriverInstall=Տեղադրվում է MediaTek SP դրայվերը...
armenian.RestartPcToFinish=Վերագործարկել համակարգիչը՝ տեղադրումն ավարտելու համար
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\BrazilianPortuguese.isl")
brazilianportuguese.CreateStartMenuIcon=Criar atalhos no Menu Iniciar
brazilianportuguese.SpFlashToolShortcut=SP Flash Tool
brazilianportuguese.CompMediaTekInstaller=MediaTek Installer (Instalador guiado de firmware)
brazilianportuguese.CompSpFlashTool=SP Flash Tool (Obrigatório)
brazilianportuguese.TypeFull=Instalação completa
brazilianportuguese.TypeCustom=Instalação personalizada
brazilianportuguese.StepStopping=Encerrando processos em execução do Updater CE, Python, SP Flash Tool e ADB...
brazilianportuguese.StepRemovingOld=Removendo versões anteriores...
brazilianportuguese.StepShortcuts=Removendo atalhos antigos...
brazilianportuguese.StepLegacy=Limpando dados e pastas de instalação antigas...
brazilianportuguese.StepDriverInstall=Instalando o driver MediaTek SP...
brazilianportuguese.RestartPcToFinish=Reiniciar o PC para concluir a instalação
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Bulgarian.isl")
bulgarian.CreateStartMenuIcon=Създаване на преки пътища в менюто „Старт“
bulgarian.SpFlashToolShortcut=SP Flash Tool
bulgarian.CompMediaTekInstaller=MediaTek Installer
bulgarian.CompSpFlashTool=SP Flash Tool (Задължително)
bulgarian.TypeFull=Пълна инсталация
bulgarian.TypeCustom=Персонализирана инсталация
bulgarian.StepStopping=Спиране на работещите процеси на Updater CE, Python, SP Flash Tool и ADB...
bulgarian.StepRemovingOld=Премахване на предишни версии...
bulgarian.StepShortcuts=Премахване на стари преки пътища...
bulgarian.StepLegacy=Почистване на стари данни и инсталационни папки...
bulgarian.StepDriverInstall=Инсталиране на драйвера MediaTek SP...
bulgarian.RestartPcToFinish=Рестартирайте компютъра, за да завършите инсталацията
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Catalan.isl")
catalan.CreateStartMenuIcon=Crea dreceres al menú Inici
catalan.SpFlashToolShortcut=SP Flash Tool
catalan.CompMediaTekInstaller=MediaTek Installer
catalan.CompSpFlashTool=SP Flash Tool (Obligatori)
catalan.TypeFull=Instal·lació completa
catalan.TypeCustom=Instal·lació personalitzada
catalan.StepStopping=S'estan aturant els processos d'Updater CE, Python, SP Flash Tool i ADB...
catalan.StepRemovingOld=S'estan eliminant les versions anteriors...
catalan.StepShortcuts=S'estan eliminant les dreceres antigues...
catalan.StepLegacy=S'estan netejant les dades i carpetes d'instal·lació antigues...
catalan.StepDriverInstall=S'està instal·lant el controlador MediaTek SP...
catalan.RestartPcToFinish=Reinicia l'ordinador per finalitzar la instal·lació
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
chinesesimplified.CreateStartMenuIcon=创建开始菜单快捷方式
chinesesimplified.SpFlashToolShortcut=SP Flash Tool
chinesesimplified.CompMediaTekInstaller=MediaTek Installer（引导式固件安装器）
chinesesimplified.CompSpFlashTool=SP Flash Tool（必选）
chinesesimplified.TypeFull=完整安装
chinesesimplified.TypeCustom=自定义安装
chinesesimplified.StepStopping=正在停止正在运行的 Updater CE、Python、SP Flash Tool 和 ADB 进程...
chinesesimplified.StepRemovingOld=正在删除旧版本...
chinesesimplified.StepShortcuts=正在删除旧快捷方式...
chinesesimplified.StepLegacy=正在清理旧数据和安装文件夹...
chinesesimplified.StepDriverInstall=正在安装联发科 (MediaTek) SP 驱动程序...
chinesesimplified.RestartPcToFinish=重启电脑以完成安装
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseTraditional.isl")
chinesetraditional.CreateStartMenuIcon=建立開始功能表捷徑
chinesetraditional.SpFlashToolShortcut=SP Flash Tool
chinesetraditional.CompMediaTekInstaller=MediaTek Installer（引導式韌體安裝程式）
chinesetraditional.CompSpFlashTool=SP Flash Tool（必選）
chinesetraditional.TypeFull=完整安裝
chinesetraditional.TypeCustom=自訂安裝
chinesetraditional.StepStopping=正在停止執行中的 Updater CE、Python、SP Flash Tool 與 ADB 程序...
chinesetraditional.StepRemovingOld=正在移除舊版本...
chinesetraditional.StepShortcuts=正在移除舊捷徑...
chinesetraditional.StepLegacy=正在清理舊資料與安裝資料夾...
chinesetraditional.StepDriverInstall=正在安裝聯發科 (MediaTek) SP 驅動程式...
chinesetraditional.RestartPcToFinish=重新啟動電腦以完成安裝
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Corsican.isl")
corsican.CreateStartMenuIcon=Creà l'accurtatoghji in u menù Principiu
corsican.SpFlashToolShortcut=SP Flash Tool
corsican.CompMediaTekInstaller=MediaTek Installer
corsican.CompSpFlashTool=SP Flash Tool (Richiestu)
corsican.TypeFull=Installazione cumpleta
corsican.TypeCustom=Installazione persunalizata
corsican.StepStopping=Arrestu di i prucessi in corsu...
corsican.StepRemovingOld=Eliminazione di e versioni precedenti...
corsican.StepShortcuts=Eliminazione di l'anziani accurtatoghji...
corsican.StepLegacy=Pulizia di i vechji cartulari...
corsican.StepDriverInstall=Installazione di u pilotu MediaTek SP...
corsican.RestartPcToFinish=Riavvià l'urdinatore per compie l'installazione
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Czech.isl")
czech.CreateStartMenuIcon=Vytvořit zástupce v nabídce Start
czech.SpFlashToolShortcut=SP Flash Tool
czech.CompMediaTekInstaller=MediaTek Installer
czech.CompSpFlashTool=SP Flash Tool (Povinné)
czech.TypeFull=Úplná instalace
czech.TypeCustom=Vlastní instalace
czech.StepStopping=Zastavování běžících procesů Updater CE, Python, SP Flash Tool a ADB...
czech.StepRemovingOld=Odstraňování předchozích verzí...
czech.StepShortcuts=Odstraňování starých zástupců...
czech.StepLegacy=Čištění starých dat a instalačních složek...
czech.StepDriverInstall=Instalace ovladače MediaTek SP...
czech.RestartPcToFinish=Restartovat počítač pro dokončení instalace
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Danish.isl")
danish.CreateStartMenuIcon=Opret genveje i menuen Start
danish.SpFlashToolShortcut=SP Flash Tool
danish.CompMediaTekInstaller=MediaTek Installer
danish.CompSpFlashTool=SP Flash Tool (Påkrævet)
danish.TypeFull=Fuld installation
danish.TypeCustom=Brugerdefineret installation
danish.StepStopping=Stopper kørende Updater CE-, Python-, SP Flash Tool- og ADB-processer...
danish.StepRemovingOld=Fjerner tidligere versioner...
danish.StepShortcuts=Fjerner gamle genveje...
danish.StepLegacy=Rydder op i gamle data og installationsmapper...
danish.StepDriverInstall=Installerer MediaTek SP-driver...
danish.RestartPcToFinish=Genstart pc'en for at fuldføre installationen
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Dutch.isl")
dutch.CreateStartMenuIcon=Snelkoppelingen in het menu Start maken
dutch.SpFlashToolShortcut=SP Flash Tool
dutch.CompMediaTekInstaller=MediaTek Installer (Begeleide firmware-installatie)
dutch.CompSpFlashTool=SP Flash Tool (Vereist)
dutch.TypeFull=Volledige installatie
dutch.TypeCustom=Aangepaste installatie
dutch.StepStopping=Actieve processen van Updater CE, Python, SP Flash Tool en ADB stoppen...
dutch.StepRemovingOld=Vorige versies verwijderen...
dutch.StepShortcuts=Oude snelkoppelingen verwijderen...
dutch.StepLegacy=Oude gegevens en installatiemappen opruimen...
dutch.StepDriverInstall=MediaTek SP-stuurprogramma installeren...
dutch.RestartPcToFinish=Pc opnieuw opstarten om de installatie te voltooien
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Finnish.isl")
finnish.CreateStartMenuIcon=Luo Käynnistä-valikon pikakuvakkeet
finnish.SpFlashToolShortcut=SP Flash Tool
finnish.CompMediaTekInstaller=MediaTek Installer
finnish.CompSpFlashTool=SP Flash Tool (Pakollinen)
finnish.TypeFull=Täysi asennus
finnish.TypeCustom=Mukautettu asennus
finnish.StepStopping=Pysäytetään käynnissä olevat Updater CE-, Python-, SP Flash Tool- ja ADB-prosessit...
finnish.StepRemovingOld=Poistetaan aiempia versioita...
finnish.StepShortcuts=Poistetaan vanhoja pikakuvakkeita...
finnish.StepLegacy=Siivotaan vanhoja tietoja ja asennuskansioita...
finnish.StepDriverInstall=Asennetaan MediaTek SP -ajuria...
finnish.RestartPcToFinish=Käynnistä tietokone uudelleen asennuksen viimeistelemiseksi
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\French.isl")
french.CreateStartMenuIcon=Créer des raccourcis dans le menu Démarrer
french.SpFlashToolShortcut=SP Flash Tool
french.CompMediaTekInstaller=MediaTek Installer (Installateur guidé de micrologiciel)
french.CompSpFlashTool=SP Flash Tool (Requis)
french.TypeFull=Installation complète
french.TypeCustom=Installation personnalisée
french.StepStopping=Arrêt des processus Updater CE, Python, SP Flash Tool et ADB en cours...
french.StepRemovingOld=Suppression des versions précédentes...
french.StepShortcuts=Suppression des anciens raccourcis...
french.StepLegacy=Nettoyage des anciennes données et dossiers d'installation...
french.StepDriverInstall=Installation du pilote MediaTek SP...
french.RestartPcToFinish=Redémarrer le PC pour terminer l'installation
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\German.isl")
german.CreateStartMenuIcon=Startmenü-Verknüpfungen erstellen
german.SpFlashToolShortcut=SP Flash Tool
german.CompMediaTekInstaller=MediaTek Installer (Geführtes Firmware-Installationsprogramm)
german.CompSpFlashTool=SP Flash Tool (Erforderlich)
german.TypeFull=Vollständige Installation
german.TypeCustom=Benutzerdefinierte Installation
german.StepStopping=Laufende Prozesse von Updater CE, Python, SP Flash Tool und ADB werden beendet...
german.StepRemovingOld=Vorherige Versionen werden entfernt...
german.StepShortcuts=Alte Verknüpfungen werden entfernt...
german.StepLegacy=Alte Daten und Installationsordner werden bereinigt...
german.StepDriverInstall=MediaTek SP-Treiber wird installiert...
german.RestartPcToFinish=PC neu starten, um die Einrichtung abzuschließen
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Hebrew.isl")
hebrew.CreateStartMenuIcon=צור קיצורי דרך בתפריט התחל
hebrew.SpFlashToolShortcut=SP Flash Tool
hebrew.CompMediaTekInstaller=MediaTek Installer
hebrew.CompSpFlashTool=SP Flash Tool (נדרש)
hebrew.TypeFull=התקנה מלאה
hebrew.TypeCustom=התקנה מותאמת אישית
hebrew.StepStopping=עוצר תהליכים פעילים של Updater CE, Python, SP Flash Tool ו-ADB...
hebrew.StepRemovingOld=מסיר גרסאות קודמות...
hebrew.StepShortcuts=מסיר קיצורי דרך ישנים...
hebrew.StepLegacy=מנקה נתונים ותיקיות התקנה ישנות...
hebrew.StepDriverInstall=מתקין את מנהל ההתקן MediaTek SP...
hebrew.RestartPcToFinish=הפעל מחדש את המחשב כדי לסיים את ההתקנה
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Hungarian.isl")
hungarian.CreateStartMenuIcon=Parancsikonok létrehozása a Start menüben
hungarian.SpFlashToolShortcut=SP Flash Tool
hungarian.CompMediaTekInstaller=MediaTek Installer
hungarian.CompSpFlashTool=SP Flash Tool (Kötelező)
hungarian.TypeFull=Teljes telepítés
hungarian.TypeCustom=Egyéni telepítés
hungarian.StepStopping=Futó Updater CE, Python, SP Flash Tool és ADB folyamatok leállítása...
hungarian.StepRemovingOld=Előző verziók eltávolítása...
hungarian.StepShortcuts=Régi parancsikonok eltávolítása...
hungarian.StepLegacy=Régi adatok és telepítési mappák tisztítása...
hungarian.StepDriverInstall=MediaTek SP illesztőprogram telepítése...
hungarian.RestartPcToFinish=A számítógép újraindítása a telepítés befejezéséhez
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Icelandic.isl")
icelandic.CreateStartMenuIcon=Búa til flýtivísa í Start-valmynd
icelandic.SpFlashToolShortcut=SP Flash Tool
icelandic.CompMediaTekInstaller=MediaTek Installer
icelandic.CompSpFlashTool=SP Flash Tool (Nauðsynlegt)
icelandic.TypeFull=Full uppsetning
icelandic.TypeCustom=Sérsniðin uppsetning
icelandic.StepStopping=Stöðva keyrandi ferla...
icelandic.StepRemovingOld=Fjarlægi eldri útgáfur...
icelandic.StepShortcuts=Fjarlægi gamla flýtivísa...
icelandic.StepLegacy=Hreinsa gömul gögn og möppur...
icelandic.StepDriverInstall=Set upp MediaTek SP rekla...
icelandic.RestartPcToFinish=Endurræsa tölvuna til að ljúka uppsetningu
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Italian.isl")
italian.CreateStartMenuIcon=Crea collegamenti nel menu Start
italian.SpFlashToolShortcut=SP Flash Tool
italian.CompMediaTekInstaller=MediaTek Installer (Installatore guidato firmware)
italian.CompSpFlashTool=SP Flash Tool (Obbligatorio)
italian.TypeFull=Installazione completa
italian.TypeCustom=Installazione personalizzata
italian.StepStopping=Arresto dei processi Updater CE, Python, SP Flash Tool e ADB in esecuzione...
italian.StepRemovingOld=Rimozione delle versioni precedenti...
italian.StepShortcuts=Rimozione dei vecchi collegamenti...
italian.StepLegacy=Pulizia dei vecchi dati e delle cartelle di installazione...
italian.StepDriverInstall=Installazione del driver MediaTek SP...
italian.RestartPcToFinish=Riavvia il PC per completare l'installazione
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Japanese.isl")
japanese.CreateStartMenuIcon=スタート メニューにショートカットを作成する
japanese.SpFlashToolShortcut=SP Flash Tool
japanese.CompMediaTekInstaller=MediaTek Installer（ガイド付きファームウェア インストーラー）
japanese.CompSpFlashTool=SP Flash Tool（必須）
japanese.TypeFull=フル インストール
japanese.TypeCustom=カスタム インストール
japanese.StepStopping=実行中の Updater CE、Python、SP Flash Tool、ADB プロセスを停止しています...
japanese.StepRemovingOld=以前のバージョンを削除しています...
japanese.StepShortcuts=古いショートカットを削除しています...
japanese.StepLegacy=古いデータとインストール フォルダーをクリーンアップしています...
japanese.StepDriverInstall=MediaTek SP ドライバーをインストールしています...
japanese.RestartPcToFinish=PC を再起動してセットアップを完了する
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
korean.CreateStartMenuIcon=시작 메뉴 바로가기 만들기
korean.SpFlashToolShortcut=SP Flash Tool
korean.CompMediaTekInstaller=MediaTek Installer (가이드형 펌웨어 설치 관리자)
korean.CompSpFlashTool=SP Flash Tool (필수)
korean.TypeFull=전체 설치
korean.TypeCustom=사용자 지정 설치
korean.StepStopping=실행 중인 Updater CE, Python, SP Flash Tool 및 ADB 프로세스를 중지하는 중...
korean.StepRemovingOld=이전 버전을 제거하는 중...
korean.StepShortcuts=이전 바로가기를 제거하는 중...
korean.StepLegacy=이전 데이터 및 설치 폴더를 정리하는 중...
korean.StepDriverInstall=MediaTek SP 드라이버를 설치하는 중...
korean.RestartPcToFinish=설치를 완료하기 위해 PC 다시 시작
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Norwegian.isl")
norwegian.CreateStartMenuIcon=Opprett snarveier på Start-menyen
norwegian.SpFlashToolShortcut=SP Flash Tool
norwegian.CompMediaTekInstaller=MediaTek Installer
norwegian.CompSpFlashTool=SP Flash Tool (Påkrevd)
norwegian.TypeFull=Full installasjon
norwegian.TypeCustom=Tilpasset installasjon
norwegian.StepStopping=Stopper kjørende Updater CE-, Python-, SP Flash Tool- og ADB-prosesser...
norwegian.StepRemovingOld=Fjerner tidligere versjoner...
norwegian.StepShortcuts=Fjerner gamle snarveier...
norwegian.StepLegacy=Rydder opp i gamle data og installasjonsmapper...
norwegian.StepDriverInstall=Installerer MediaTek SP-driver...
norwegian.RestartPcToFinish=Start PC-en på nytt for å fullføre oppsettet
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Polish.isl")
polish.CreateStartMenuIcon=Utwórz skróty w menu Start
polish.SpFlashToolShortcut=SP Flash Tool
polish.CompMediaTekInstaller=MediaTek Installer (Kreator instalacji oprogramowania)
polish.CompSpFlashTool=SP Flash Tool (Wymagane)
polish.TypeFull=Pełna instalacja
polish.TypeCustom=Instalacja dostosowana
polish.StepStopping=Zatrzymywanie uruchomionych procesów Updater CE, Python, SP Flash Tool i ADB...
polish.StepRemovingOld=Usuwanie poprzednich wersji...
polish.StepShortcuts=Usuwanie starych skrótów...
polish.StepLegacy=Czyszczenie starych danych i folderów instalacyjnych...
polish.StepDriverInstall=Instalowanie sterownika MediaTek SP...
polish.RestartPcToFinish=Uruchom ponownie komputer, aby zakończyć instalację
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Portuguese.isl")
portuguese.CreateStartMenuIcon=Criar atalhos no Menu Iniciar
portuguese.SpFlashToolShortcut=SP Flash Tool
portuguese.CompMediaTekInstaller=MediaTek Installer (Instalador guiado de firmware)
portuguese.CompSpFlashTool=SP Flash Tool (Obrigatório)
portuguese.TypeFull=Instalação completa
portuguese.TypeCustom=Instalação personalizada
portuguese.StepStopping=A terminar processos em execução do Updater CE, Python, SP Flash Tool e ADB...
portuguese.StepRemovingOld=A remover versões anteriores...
portuguese.StepShortcuts=A remover atalhos antigos...
portuguese.StepLegacy=A limpar dados e pastas de instalação antigas...
portuguese.StepDriverInstall=A instalar o controlador MediaTek SP...
portuguese.RestartPcToFinish=Reiniciar o PC para concluir a instalação
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Russian.isl")
russian.CreateStartMenuIcon=Создать ярлыки в меню «Пуск»
russian.SpFlashToolShortcut=SP Flash Tool
russian.CompMediaTekInstaller=MediaTek Installer (Пошаговый установщик прошивок)
russian.CompSpFlashTool=SP Flash Tool (Обязательно)
russian.TypeFull=Полная установка
russian.TypeCustom=Выборочная установка
russian.StepStopping=Остановка запущенных процессов Updater CE, Python, SP Flash Tool и ADB...
russian.StepRemovingOld=Удаление предыдущих версий...
russian.StepShortcuts=Удаление старых ярлыков...
russian.StepLegacy=Очистка старых данных и папок установки...
russian.StepDriverInstall=Установка драйвера MediaTek SP...
russian.RestartPcToFinish=Перезагрузить компьютер для завершения установки
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Slovak.isl")
slovak.CreateStartMenuIcon=Vytvoriť odkazy v ponuke Štart
slovak.SpFlashToolShortcut=SP Flash Tool
slovak.CompMediaTekInstaller=MediaTek Installer
slovak.CompSpFlashTool=SP Flash Tool (Povinné)
slovak.TypeFull=Úplná inštalácia
slovak.TypeCustom=Vlastná inštalácia
slovak.StepStopping=Zastavovanie spustených procesov Updater CE, Python, SP Flash Tool a ADB...
slovak.StepRemovingOld=Odstraňovanie predchádzajúcich verzií...
slovak.StepShortcuts=Odstraňovanie starých odkazov...
slovak.StepLegacy=Čistenie starých údajov a inštalačných priečinkov...
slovak.StepDriverInstall=Inštalácia ovládača MediaTek SP...
slovak.RestartPcToFinish=Reštartovať počítač na dokončenie inštalácie
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Slovenian.isl")
slovenian.CreateStartMenuIcon=Ustvari bližnjice v meniju Začetek
slovenian.SpFlashToolShortcut=SP Flash Tool
slovenian.CompMediaTekInstaller=MediaTek Installer
slovenian.CompSpFlashTool=SP Flash Tool (Obvezno)
slovenian.TypeFull=Polna namestitev
slovenian.TypeCustom=Namestitev po meri
slovenian.StepStopping=Ustavljanje procesov Updater CE, Python, SP Flash Tool in ADB...
slovenian.StepRemovingOld=Odstranjevanje prejšnjih različic...
slovenian.StepShortcuts=Odstranjevanje starih bližnjic...
slovenian.StepLegacy=Čiščenje starih podatkov in namestitvenih map...
slovenian.StepDriverInstall=Nameščanje gonilnika MediaTek SP...
slovenian.RestartPcToFinish=Ponovno zaženi računalnik za dokončanje namestitve
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Spanish.isl")
spanish.CreateStartMenuIcon=Crear accesos directos en el menú Inicio
spanish.SpFlashToolShortcut=SP Flash Tool
spanish.CompMediaTekInstaller=MediaTek Installer (Instalador guiado de firmware)
spanish.CompSpFlashTool=SP Flash Tool (Obligatorio)
spanish.TypeFull=Instalación completa
spanish.TypeCustom=Instalación personalizada
spanish.StepStopping=Deteniendo procesos en ejecución de Updater CE, Python, SP Flash Tool y ADB...
spanish.StepRemovingOld=Eliminando versiones anteriores...
spanish.StepShortcuts=Eliminando accesos directos antiguos...
spanish.StepLegacy=Limpiando datos y carpetas de instalación antiguas...
spanish.StepDriverInstall=Instalando el controlador MediaTek SP...
spanish.RestartPcToFinish=Reiniciar el PC para finalizar la instalación
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Swedish.isl")
swedish.CreateStartMenuIcon=Skapa genvägar på Start-menyn
swedish.SpFlashToolShortcut=SP Flash Tool
swedish.CompMediaTekInstaller=MediaTek Installer
swedish.CompSpFlashTool=SP Flash Tool (Krävs)
swedish.TypeFull=Fullständig installation
swedish.TypeCustom=Anpassad installation
swedish.StepStopping=Stoppar körande Updater CE-, Python-, SP Flash Tool- och ADB-processer...
swedish.StepRemovingOld=Tar bort tidigare versioner...
swedish.StepShortcuts=Tar bort gamla genvägar...
swedish.StepLegacy=Rensar gamla data och installationsmappar...
swedish.StepDriverInstall=Installerar MediaTek SP-drivrutin...
swedish.RestartPcToFinish=Starta om datorn för att slutföra installationen
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Tamil.isl")
tamil.CreateStartMenuIcon=தொடக்க மெனுவில் குறுக்குவழிகளை உருவாக்கு
tamil.SpFlashToolShortcut=SP Flash Tool
tamil.CompMediaTekInstaller=MediaTek Installer
tamil.CompSpFlashTool=SP Flash Tool (அவசியம்)
tamil.TypeFull=முழு நிறுவல்
tamil.TypeCustom=தனிப்பயன் நிறுவல்
tamil.StepStopping=இயங்கும் செயல்முறைகளை நிறுத்துகிறது...
tamil.StepRemovingOld=முந்தைய பதிப்புகளை அகற்றுகிறது...
tamil.StepShortcuts=பழைய குறுக்குவழிகளை அகற்றுகிறது...
tamil.StepLegacy=பழைய கோப்புறைகளை சுத்தம் செய்கிறது...
tamil.StepDriverInstall=MediaTek SP இயக்கியை நிறுவுகிறது...
tamil.RestartPcToFinish=அமைப்பை முடிக்க கணினியை மறுதொடக்கம் செய்
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Turkish.isl")
turkish.CreateStartMenuIcon=Başlat Menüsü kısayolları oluştur
turkish.SpFlashToolShortcut=SP Flash Tool
turkish.CompMediaTekInstaller=MediaTek Installer (Rehberli Ürün Yazılımı Yükleyicisi)
turkish.CompSpFlashTool=SP Flash Tool (Zorunlu)
turkish.TypeFull=Tam kurulum
turkish.TypeCustom=Özel kurulum
turkish.StepStopping=Çalışan Updater CE, Python, SP Flash Tool ve ADB işlemleri durduruluyor...
turkish.StepRemovingOld=Önceki sürümler kaldırılıyor...
turkish.StepShortcuts=Eski kısayollar kaldırılıyor...
turkish.StepLegacy=Eski veriler ve kurulum klasörleri temizleniyor...
turkish.StepDriverInstall=MediaTek SP Sürücüsü yükleniyor...
turkish.RestartPcToFinish=Kurulumu tamamlamak için bilgisayarı yeniden başlat
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Ukrainian.isl")
ukrainian.CreateStartMenuIcon=Створити ярлики в меню «Пуск»
ukrainian.SpFlashToolShortcut=SP Flash Tool
ukrainian.CompMediaTekInstaller=MediaTek Installer (Покроковий інсталятор прошивок)
ukrainian.CompSpFlashTool=SP Flash Tool (Обов'язково)
ukrainian.TypeFull=Повна інсталяція
ukrainian.TypeCustom=Вибіркова інсталяція
ukrainian.StepStopping=Зупинення запущених процесів Updater CE, Python, SP Flash Tool та ADB...
ukrainian.StepRemovingOld=Видалення попередніх версій...
ukrainian.StepShortcuts=Видалення старих ярликів...
ukrainian.StepLegacy=Очищення старих даних і папок інсталяції...
ukrainian.StepDriverInstall=Встановлення драйвера MediaTek SP...
ukrainian.RestartPcToFinish=Перезавантажити ПК для завершення встановлення
#endif

[Files]
; Main application binaries, _internal runtime, and bundled SP Flash Tool 5.1904
; ignoreversion replaces older files, so this same installer updates in place.
#ifdef MyIsMediaTek
Source: "{#MyDistDir}\SP_Flash_Tool\*"; DestDir: "{app}\SP_Flash_Tool"; Components: sp_flash_tool; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyDistDir}\*"; Excludes: "SP_Flash_Tool\*,mtkclient\*,_internal\mtkclient\*,1.*.exe,2.*.exe,1. Install*,2. Install*"; DestDir: "{app}"; Components: mediatek_installer; Flags: ignoreversion recursesubdirs createallsubdirs
#else
Source: "{#MyDistDir}\*"; Excludes: "mtkclient\*,_internal\mtkclient\*,1.*.exe,2.*.exe,1. Install*,2. Install*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif
; UnRAR tool for .rar firmware package extraction compatibility
Source: "{#MyRepoRoot}tools\windows\UnRAR.exe"; DestDir: "{app}\tools"; Flags: ignoreversion skipifsourcedoesntexist
; MTKClient is only installed on ARM64 Windows systems
Source: "{#MyDistDir}\mtkclient\*"; DestDir: "{app}\mtkclient"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Check: IsArm64
Source: "{#MyDistDir}\_internal\mtkclient\*"; DestDir: "{app}\_internal\mtkclient"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Check: IsArm64
; Bundled MediaTek Driver Installer executed during setup
Source: "{#MyRepoRoot}tools\DriverInstall.exe"; DestDir: "{tmp}"; Flags: ignoreversion skipifsourcedoesntexist; Check: ShouldInstallDriver

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: startmenuicon; Check: ShouldInstallMainApp
Name: "{group}\{cm:SpFlashToolShortcut}"; Filename: "{app}\SP_Flash_Tool\flash_tool.exe"; WorkingDir: "{app}\SP_Flash_Tool"; IconFilename: "{app}\SP_Flash_Tool\flash_tool.exe"; Tasks: startmenuicon
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"; Tasks: startmenuicon
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Check: ShouldInstallMainApp

[Run]
; Install the bundled MediaTek SP USB driver silently during the install phase
; (before Inno Setup builds the Finish page checkboxes).
Filename: "{tmp}\DriverInstall.exe"; Parameters: "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"; StatusMsg: "{cm:StepDriverInstall}"; Flags: runhidden waituntilterminated skipifdoesntexist; Check: ShouldInstallDriver; AfterInstall: MarkDriverInstalled
; Offer to launch the application after installation (only if driver installation was not required/run).
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent; Check: ShouldLaunchMainAppAfterInstall
; Offer the "Restart PC to finish setup" checkbox on the Finish page (when driver install ran).
Filename: "{sys}\shutdown.exe"; Parameters: "/r /t 2"; Description: "{cm:RestartPcToFinish}"; Flags: nowait postinstall runhidden unchecked skipifsilent; Check: ShouldOfferRestartAfterInstall

[UninstallDelete]
Type: filesandordirs; Name: "{app}\SP_Flash_Tool\SP_FT_Logs"
Type: filesandordirs; Name: "{app}\SP_Flash_Tool\history.ini"
Type: filesandordirs; Name: "{app}\SP_Flash_Tool"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\mtkclient"
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\updater.log"
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{localappdata}\Updater CE"
Type: filesandordirs; Name: "{localappdata}\MediaTek Installer"
Type: filesandordirs; Name: "{localappdata}\MediaTekInstaller"
Type: filesandordirs; Name: "{localappdata}\Innioasis Updater"
Type: filesandordirs; Name: "{localappdata}\innioasis"
Type: filesandordirs; Name: "C:\ProgramData\SP_FT_Logs"
Type: filesandordirs; Name: "C:\ProgramData\SP_FT_Dump"

[Code]
const
  UninstallRoot = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\';
  CurrentUninstallKey = UninstallRoot + '{#MyAppId}_is1';
  LegacyUninstallKey = UninstallRoot + 'Innioasis Updater_is1';
  (* The first Updater CE installer shipped under its own AppId and installed
     into "{autopf}\Ryan Specter". A machine that ran it has a second uninstall
     entry and a second set of shortcuts; both are removed on upgrade. *)
  LegacyUpdaterCeKey = UninstallRoot + '{8A1C6F5B-9A3C-4E6D-8F9A-7B3D2E1C0A9F}_is1';
  { Start Menu folders are a few levels deep at most. The bound stops a
    runaway walk from ever hanging the installer. }
  MaxShortcutPurgeDepth = 6;

var
  PriorDesktopShortcut: Boolean;
  DriverInstalled: Boolean;
  SettingsBackupFile: String;

{ The app keeps its settings in HKCU\Software\innioasis (QSettings). Removing an
  older copy wipes that key, so an upgrade saves it here first and puts it back
  once the new files are in place. }
procedure BackupSettings;
var
  ResultCode: Integer;
begin
  SettingsBackupFile := ExpandConstant('{tmp}\updater-settings.reg');
  DeleteFile(SettingsBackupFile);
  if RegKeyExists(HKEY_CURRENT_USER, 'Software\innioasis') then
    Exec(ExpandConstant('{sys}\reg.exe'),
      'export "HKCU\Software\innioasis" "' + SettingsBackupFile + '" /y',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure RestoreSettings;
var
  ResultCode: Integer;
begin
  if (SettingsBackupFile <> '') and FileExists(SettingsBackupFile) then
  begin
    Exec(ExpandConstant('{sys}\reg.exe'),
      'import "' + SettingsBackupFile + '"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    DeleteFile(SettingsBackupFile);
  end;
end;

function IsArm64: Boolean;
begin
  Result := (ProcessorArchitecture = paARM64);
end;

function IsUpdaterCeBrand: Boolean;
begin
#ifdef MyIsMediaTek
  Result := False;
#else
  Result := True;
#endif
end;

function ShouldInstallMainApp: Boolean;
begin
#ifdef MyIsMediaTek
  Result := WizardIsComponentSelected('mediatek_installer');
#else
  Result := True;
#endif
end;

function ShouldOfferRestartCheckbox: Boolean;
begin
  Result := True;
end;

function IsMediaTekDriverAlreadyInstalled: Boolean;
begin
  Result := FileExists('C:\Program Files\MediaTek\SP Flash\unins000.exe') or
            FileExists('C:\Program Files\MediaTek\SP Driver\unins000.exe') or
            FileExists(ExpandConstant('{commonpf64}\MediaTek\SP Flash\unins000.exe')) or
            FileExists(ExpandConstant('{commonpf64}\MediaTek\SP Driver\unins000.exe')) or
            FileExists(ExpandConstant('{commonpf}\MediaTek\SP Flash\unins000.exe')) or
            FileExists(ExpandConstant('{commonpf}\MediaTek\SP Driver\unins000.exe'));
  if Result then
    Log('MediaTek SP Driver already detected (unins000.exe); skipping driver installation.');
end;

function ShouldInstallDriver: Boolean;
begin
  Result := not IsMediaTekDriverAlreadyInstalled;
end;

function ShouldLaunchMainAppAfterInstall: Boolean;
begin
  { Do not offer to launch Updater CE if the driver install ran because unins000.exe was not found. }
  Result := ShouldInstallMainApp and (not DriverInstalled) and (not ShouldInstallDriver);
end;

function ShouldOfferRestartAfterInstall: Boolean;
begin
  Result := DriverInstalled or ShouldInstallDriver;
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

function IsShortcutFile(const Name: String): Boolean;
var
  Ext: String;
begin
  Ext := Lowercase(ExtractFileExt(Name));
  Result := (Ext = '.lnk') or (Ext = '.url');
end;

function IsRetiredShortcut(const Name: String): Boolean;
begin
  Result :=
    IsShortcutFile(Name) and (
      NameContains(Name, 'Innioasis') or
      NameContains(Name, 'SP Flash') or
      NameContains(Name, 'Updater CE')
    );
end;

function IsRetiredFolder(const Name: String): Boolean;
begin
  { Every menu/desktop folder that carries our name goes, whatever version
    wrote it: "Innioasis Updater", "Innioasis Updater CE", "Innioasis
    Community" and any SP Flash folder. The generic MediaTek Installer
    folder is not named here, so that brand is left alone. }
  Result :=
    NameContains(Name, 'Innioasis') or
    NameContains(Name, 'SP Flash') or
    NameContains(Name, 'Updater CE');
end;

{ Removes a retired Start Menu / Desktop shortcut folder's contents without
  following junctions or symlinks and without ever deleting non-shortcut user
  files: only .lnk/.url files and empty directories are removed. }
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
        if (FindRec.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then
          Continue;
        Child := Path + '\' + FindRec.Name;
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          RemoveRetiredFolderAt(Child, Depth + 1)
        else if IsShortcutFile(FindRec.Name) then
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
  if present, before falling back to removing the directory. }
procedure RunOldLocalUninstaller(const Dir: String);
var
  ResultCode: Integer;
begin
  if FileExists(Dir + '\uninstall.exe') then
    Exec(Dir + '\uninstall.exe', '/S /VERYSILENT /SUPPRESSMSGBOXES /NORESTART', Dir, SW_HIDE, ewWaitUntilTerminated, ResultCode)
  else if FileExists(Dir + '\Uninstall.exe') then
    Exec(Dir + '\Uninstall.exe', '/S /VERYSILENT /SUPPRESSMSGBOXES /NORESTART', Dir, SW_HIDE, ewWaitUntilTerminated, ResultCode)
  else if FileExists(Dir + '\unins000.exe') then
    Exec(Dir + '\unins000.exe', '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', Dir, SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ Every profile's leftovers: the retired LocalAppData data folder, a per-user
  Programs copy, its Start Menu, Desktop and taskbar shortcuts. A locked or
  missing folder is skipped rather than stopping the install. }
procedure RemoveLegacyLocalAppData;
var
  Profiles: TStringList;
  I: Integer;
  P, CurrentLocalDir: String;
begin
  CurrentLocalDir := ExpandConstant('{localappdata}\Innioasis Updater');
  RunOldLocalUninstaller(CurrentLocalDir);
  if DirExists(CurrentLocalDir) then
    DelTree(CurrentLocalDir, True, True, True);

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

procedure ClearProfileAppData(const BaseDir: String);
begin
  if (BaseDir <> '') and DirExists(BaseDir) then
  begin
    { Clear cached files: .cache, downloads, logs, tools, SP_Flash_Tool, updater.log }
    if DirExists(BaseDir + '\.cache') then
      DelTree(BaseDir + '\.cache', True, True, True);
    if DirExists(BaseDir + '\downloads') then
      DelTree(BaseDir + '\downloads', True, True, True);
    if DirExists(BaseDir + '\logs') then
      DelTree(BaseDir + '\logs', True, True, True);
    if DirExists(BaseDir + '\tools') then
      DelTree(BaseDir + '\tools', True, True, True);
    if DirExists(BaseDir + '\SP_Flash_Tool') then
      DelTree(BaseDir + '\SP_Flash_Tool', True, True, True);
    if FileExists(BaseDir + '\updater.log') then
      DeleteFile(BaseDir + '\updater.log');
    RemoveDir(BaseDir);
  end;
end;

procedure ClearAppCacheAndData;
var
  Profiles: TStringList;
  I: Integer;
  P, LocalApp: String;
begin
  { 1. Current user AppData\Local }
  LocalApp := ExpandConstant('{localappdata}');
  ClearProfileAppData(LocalApp + '\Updater CE');
  ClearProfileAppData(LocalApp + '\MediaTek Installer');
  ClearProfileAppData(LocalApp + '\MediaTekInstaller');
  ClearProfileAppData(LocalApp + '\Innioasis Updater');
  ClearProfileAppData(LocalApp + '\innioasis');

  { 2. All user profiles under Users }
  Profiles := ProfileRoots;
  try
    for I := 0 to Profiles.Count - 1 do
    begin
      P := Profiles[I];
      ClearProfileAppData(P + '\AppData\Local\Updater CE');
      ClearProfileAppData(P + '\AppData\Local\MediaTek Installer');
      ClearProfileAppData(P + '\AppData\Local\MediaTekInstaller');
      ClearProfileAppData(P + '\AppData\Local\Innioasis Updater');
      ClearProfileAppData(P + '\AppData\Local\innioasis');
    end;
  finally
    Profiles.Free;
  end;

  { 3. System-wide ProgramData SP Flash Tool logs/dumps }
  if DirExists('C:\ProgramData\SP_FT_Logs') then
    DelTree('C:\ProgramData\SP_FT_Logs', True, True, True);
  if DirExists('C:\ProgramData\SP_FT_Dump') then
    DelTree('C:\ProgramData\SP_FT_Dump', True, True, True);

  { 4. Settings in registry from older versions }
  RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, 'Software\innioasis');
  RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, 'Software\Innioasis');
  RegDeleteKeyIncludingSubkeys(HKEY_LOCAL_MACHINE, 'Software\innioasis');
  RegDeleteKeyIncludingSubkeys(HKEY_LOCAL_MACHINE, 'Software\Innioasis');
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
  PriorDesktopShortcut :=
    DirHasRetiredShortcut(ExpandConstant('{userdesktop}')) or
    DirHasRetiredShortcut(ExpandConstant('{commondesktop}'));
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
  if not TasksWereRecorded then
  begin
    if PriorDesktopShortcut then
      WizardSelectTasks('startmenuicon,desktopicon')
    else
      WizardSelectTasks('startmenuicon');
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

procedure MarkDriverInstalled;
begin
  DriverInstalled := True;
  if ShouldInstallMainApp then
    RegisterRunOnceStartup;
end;

procedure InstallBundledDriver;
var
  DriverExe: String;
  ResultCode: Integer;
begin
  if DriverInstalled or (not ShouldInstallDriver) then
    Exit;
  DriverExe := ExpandConstant('{tmp}\DriverInstall.exe');
  if not FileExists(DriverExe) then
    Exit;

  ShowStep('StepDriverInstall');
  { Execute DriverInstall.exe silently without rebooting immediately }
  if Exec(DriverExe, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    MarkDriverInstalled;
  end;
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
    StopProcess('MediaTekInstaller.exe');
    StopProcess('flash_tool.exe');
    StopProcess('adb.exe');
    StopProcess('python.exe');
    StopProcess('pythonw.exe');

    BackupSettings;

    ShowStep('StepRemovingOld');
    RemoveOldInstalls;

    ShowStep('StepShortcuts');
    PurgeRetiredShortcuts;

    ShowStep('StepLegacy');
    RemoveLegacyLocalAppData;
    ClearAppCacheAndData;
    if IsUpdaterCeBrand then
      RemoveLegacyProgramFiles;
  end
  else if CurStep = ssPostInstall then
  begin
    RestoreSettings;
    InstallBundledDriver;
  end
  else if CurStep = ssDone then
  begin
    { Register RunOnce so the app launches on the next reboot. }
    if ShouldInstallMainApp then
      RegisterRunOnceStartup;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopProcess('InnioasisUpdater.exe');
    StopProcess('MediaTekInstaller.exe');
    StopProcess('flash_tool.exe');
    StopProcess('adb.exe');
    StopProcess('python.exe');
    StopProcess('pythonw.exe');
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    ClearAppCacheAndData;
    DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;

