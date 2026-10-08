@echo off
rem ==============================================================================
rem build_windows.bat — Build Innioasis Updater CE Windows Executable & Installer
rem Repository: https://github.com/y1-community/updater-ce-neo
rem ==============================================================================
setlocal enabledelayedexpansion

echo ======================================================================
echo  Building Innioasis Updater CE for Windows
echo ======================================================================

cd /d "%~dp0"

rem 0. Brand plan: a build produces BOTH front ends — Updater CE and the
rem    generic MediaTek Installer — unless a brand is named as the argument.
rem    Each brand is built by a child run of this script.
if not defined INNOASIS_BRAND_LOCK (
    set "INNOASIS_BRAND_LOCK=1"
    if "%~1"=="" (
        call "%~f0" updater_ce
        if errorlevel 1 exit /b 1
        call "%~f0" mediatek_installer
        if errorlevel 1 exit /b 1
        echo [SUCCESS] Built for Windows: Updater CE and MediaTek Installer
    ) else (
        call "%~f0" "%~1"
        if errorlevel 1 exit /b 1
        echo [SUCCESS] Built for Windows: %~1
    )
    echo        See dist\ for the application folders.
    exit /b 0
)

rem 0b. Packaging brand (this is the child pass): the argument selects it.
rem    build_windows.bat                      -> both
rem    build_windows.bat mediatek_installer   -> MediaTek Installer only
set "BUILD_BRAND=%~1"
if "%BUILD_BRAND%"=="" set "BUILD_BRAND=updater_ce"
if /i "%BUILD_BRAND%"=="mediatek_installer" (
    rem dist/exe stay slugs like Updater CE's; "MediaTek Installer" is what the
    rem user sees (installer, Start Menu, window title).
    set "APP_DISPLAY=MediaTek Installer"
    set "APP_DIR=MediaTekInstaller"
    set "APP_EXE=MediaTekInstaller.exe"
    set "INSTALLER_BASE=MediaTekInstaller-Setup"
    rem /DMyIsMediaTek gives this app its own [Setup] identity: both apps can
    rem then be installed side by side instead of replacing each other.
    set "ISCC_MEDIA=/DMyIsMediaTek=1"
) else if /i "%BUILD_BRAND%"=="updater_ce" (
    set "APP_DISPLAY=Updater CE"
    set "APP_DIR=InnioasisUpdater"
    set "APP_EXE=InnioasisUpdater.exe"
    set "INSTALLER_BASE=UpdaterCE-Setup"
    set "ISCC_MEDIA="
) else (
    echo [ERROR] Unknown brand "%BUILD_BRAND%" - use updater_ce or mediatek_installer.
    exit /b 1
)

echo [INFO] Brand: %APP_DISPLAY% (%BUILD_BRAND%)

rem 1. Detect Python
set "PYTHON=python"
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else if exist ".venv-build\Scripts\python.exe" (
    set "PYTHON=.venv-build\Scripts\python.exe"
)

%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+ and add to PATH.
    exit /b 1
)

echo [INFO] Using Python: %PYTHON%

rem 2. Install requirements & PyInstaller if needed
echo [INFO] Checking Python dependencies...
%PYTHON% -m pip install -q -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install Python dependencies.
    exit /b 1
)

rem 3. Build with PyInstaller
rem Bake the brand into the frozen app (the spec also names dist/ after it).
%PYTHON% scripts\set_build_brand.py %BUILD_BRAND%
if errorlevel 1 (
    echo [ERROR] Failed to bake the build brand.
    exit /b 1
)
echo [INFO] Compiling application with PyInstaller (InnioasisUpdater.spec)...
%PYTHON% -m PyInstaller --clean --noconfirm InnioasisUpdater.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

rem 4. Ensure SP Flash Tool payload is bundled next to the executable
set "SP_WIN_SRC=tools\windows\SP_Flash_Tool_v5.1904_Win"
set "SP_WIN_DST=dist\%APP_DIR%\SP_Flash_Tool"
if exist "%SP_WIN_SRC%" (
    if not exist "%SP_WIN_DST%" (
        echo [INFO] Staging bundled SP Flash Tool Windows payload...
        xcopy /E /I /Q /Y "%SP_WIN_SRC%" "%SP_WIN_DST%" >nul
    )
) else (
    echo [WARNING] SP Flash Tool source not found at %SP_WIN_SRC%.
)

%PYTHON% scripts\set_build_brand.py --reset

echo [SUCCESS] Application built at: dist\%APP_DIR%\%APP_EXE%

rem 5. Optional Inno Setup compilation
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
) else if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" (
    set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
) else (
    for %%X in (ISCC.exe) do (set "ISCC=%%~$PATH:X")
)

if defined ISCC (
    echo [INFO] Compiling Windows Installer with Inno Setup...
    "%ISCC%" /DMyAppName="%APP_DISPLAY%" /DMyAppExeName="%APP_EXE%" /DMyDistDir="..\dist\%APP_DIR%" /DMyOutputBase="%INSTALLER_BASE%" %ISCC_MEDIA% scripts\build_windows_installer.iss
    if errorlevel 1 (
        echo [WARNING] Inno Setup compilation failed.
    ) else (
        echo [SUCCESS] Windows Installer created in dist\
    )
) else (
    echo [NOTE] Inno Setup 6 (ISCC.exe) not found.
    echo        Install Inno Setup 6 to generate %INSTALLER_BASE%-3.0.0.exe.
    echo        Stand-alone directory ready in dist\InnioasisUpdater\
)

echo ======================================================================
echo  Build complete!
echo ======================================================================
exit /b 0
