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
echo [INFO] Compiling application with PyInstaller (InnioasisUpdater.spec)...
%PYTHON% -m PyInstaller --clean --noconfirm InnioasisUpdater.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

rem 4. Ensure SP Flash Tool payload is bundled next to the executable
set "SP_WIN_SRC=tools\windows\SP_Flash_Tool_v5.1904_Win"
set "SP_WIN_DST=dist\InnioasisUpdater\SP_Flash_Tool"
if exist "%SP_WIN_SRC%" (
    if not exist "%SP_WIN_DST%" (
        echo [INFO] Staging bundled SP Flash Tool Windows payload...
        xcopy /E /I /Q /Y "%SP_WIN_SRC%" "%SP_WIN_DST%" >nul
    )
) else (
    echo [WARNING] SP Flash Tool source not found at %SP_WIN_SRC%.
)

echo [SUCCESS] Application built at: dist\InnioasisUpdater\InnioasisUpdater.exe

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
    "%ISCC%" scripts\build_windows_installer.iss
    if errorlevel 1 (
        echo [WARNING] Inno Setup compilation failed.
    ) else (
        echo [SUCCESS] Windows Installer created in dist\
    )
) else (
    echo [NOTE] Inno Setup 6 (ISCC.exe) not found.
    echo        Install Inno Setup 6 to generate InnioasisUpdater-Setup-3.0.0.exe.
    echo        Stand-alone directory ready in dist\InnioasisUpdater\
)

echo ======================================================================
echo  Build complete!
echo ======================================================================
exit /b 0
