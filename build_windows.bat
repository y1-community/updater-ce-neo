@echo off
rem Build script for Updater CE / MediaTek Installer on Windows.
rem Usage:
rem   build_windows.bat            :: build both Updater CE and MediaTek Installer
rem   build_windows.bat updater_ce :: build Updater CE only
rem   build_windows.bat mediatek_installer :: build MediaTek Installer only

setlocal enabledelayedexpansion

set "BRAND=updater_ce"
set "CLEAN=0"
set "NOINSTALLER=0"

rem Parse arguments.
if "%~1"=="" goto :check_inno
if /i "%~1"=="updater_ce" goto :parse_branded
if /i "%~1"=="mediatek_installer" goto :parse_branded
if /i "%~1"=="--help" goto :help
if /i "%~1"=="-help" goto :help
echo Unknown argument: %~1
goto :help

:parse_branded
set "BRAND=%~1"
if "%~2"=="-Clean" set "CLEAN=1"
if "%~2"=="--clean" set "CLEAN=1"
if "%~2"=="-NoInstaller" set "NOINSTALLER=1"
if "%~2"=="--noinstaller" set "NOINSTALLER=1"
goto :check_inno

:check_inno
rem Inno Setup is optional; only used to build setup wizards.
where /q iscc.exe
if errorlevel 1 (
    echo WARNING: iscc.exe not found on PATH. Inno Setup installers will be skipped.
    set "NOINSTALLER=1"
)

cd /d "%~dp0"

if "%CLEAN%"=="1" (
    echo Cleaning previous build artifacts...
    if exist "dist\InnioasisUpdater" rmdir /s /q "dist\InnioasisUpdater" 2>nul
    if exist "dist\MediaTekInstaller" rmdir /s /q "dist\MediaTekInstaller" 2>nul
    if exist "dist\*.exe" del /q "dist\*.exe" 2>nul
    if exist "build" rmdir /s /q "build" 2>nul
)

rem Locate PyInstaller (prefer local .venv)
set "PYINSTALLER_EXE=pyinstaller"
if exist ".venv\Scripts\pyinstaller.exe" (
    set "PYINSTALLER_EXE=.venv\Scripts\pyinstaller.exe"
)

rem Locate spec file
set "SPEC_FILE=InnioasisUpdater.spec"
if not exist "%SPEC_FILE%" (
    if exist "build\InnioasisUpdater.spec" set "SPEC_FILE=build\InnioasisUpdater.spec"
)

if "%BRAND%"=="updater_ce" (
    echo Building Updater CE...
    set "BUILD_BRAND="
    set "PYINSTALLER_CMD=%PYINSTALLER_EXE% --clean --noconfirm %SPEC_FILE%"
) else if "%BRAND%"=="mediatek_installer" (
    echo Building MediaTek Installer...
    set "BUILD_BRAND=mediatek_installer"
    set "PYINSTALLER_CMD=%PYINSTALLER_EXE% --clean --noconfirm %SPEC_FILE%"
) else (
    echo Building both Updater CE and MediaTek Installer...
    set "BUILD_BRAND="
    set "PYINSTALLER_CMD=%PYINSTALLER_EXE% --clean --noconfirm %SPEC_FILE%"
)

echo Running: %PYINSTALLER_CMD%
%PYINSTALLER_CMD%
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

if "%NOINSTALLER%"=="0" (
    if not "%BRAND%"=="mediatek_installer" (
        echo Building Updater CE setup installer with Inno Setup...
        iscc.exe "InnioasisUpdater.iss" /O"dist" /F"UpdaterCE-Setup-2.5.0.exe"
        if errorlevel 1 (
            echo Inno Setup build failed; continuing with portable exe.
        )
    )
    if not "%BRAND%"=="updater_ce" (
        echo Building MediaTek Installer setup installer with Inno Setup...
        iscc.exe "MediaTekInstaller.iss" /O"dist" /F"MediaTekInstaller-Setup-2.5.0.exe"
        if errorlevel 1 (
            echo Inno Setup build failed; continuing with portable exe.
        )
    )
)

rem Portable zip: the PyInstaller folder unpacked and runnable without installing.
if not "%BRAND%"=="mediatek_installer" (
    if exist "dist\InnioasisUpdater\InnioasisUpdater.exe" (
        echo Creating portable zip...
        powershell -NoProfile -Command "Compress-Archive -Path 'dist\InnioasisUpdater\*' -DestinationPath 'dist\UpdaterCE-Portable-2.5.0.zip' -Force"
        if errorlevel 1 (
            echo Portable zip build failed.
            exit /b 1
        )
    )
)

echo.
echo Build complete.
echo Portable exe: dist\InnioasisUpdater\InnioasisUpdater.exe
echo Portable exe: dist\MediaTekInstaller\MediaTekInstaller.exe
echo Portable zip: dist\UpdaterCE-Portable-2.5.0.zip
if "%NOINSTALLER%"=="0" (
    echo Installers: dist\UpdaterCE-Setup-2.5.0.exe
    echo Installers: dist\MediaTekInstaller-Setup-2.5.0.exe
)
endlocal
exit /b 0

:help
echo Usage: build_windows.bat [updater_ce^|mediatek_installer] [-Clean^|-NoInstaller]
echo.
echo Builds Updater CE and/or MediaTek Installer on Windows.
echo Options:
echo   updater_ce         Build Updater CE only.
echo   mediatek_installer Build MediaTek Installer only.
echo   -Clean             Remove previous build artifacts before compiling.
echo   -NoInstaller       Skip Inno Setup and build the portable .exe only.
echo.
echo Prerequisites: Python 3.10+, PyInstaller, and Inno Setup 6 (optional for the
echo setup wizard). The app bundles SP Flash Tool from tools/windows/SP_Flash_Tool.
exit /b 0
