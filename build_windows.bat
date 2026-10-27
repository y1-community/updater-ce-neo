@echo off
rem Build script for Updater CE / MediaTek Installer on Windows.
rem Usage:
rem   build_windows.bat                     :: build both Updater CE and MediaTek Installer into dist\windows
rem   build_windows.bat updater_ce          :: build Updater CE only into dist\windows
rem   build_windows.bat mediatek_installer  :: build MediaTek Installer only into dist\windows
rem   build_windows.bat updater_ce -Msix     :: also pack the Microsoft Store (MSIX) package

setlocal enabledelayedexpansion

set "BRAND=updater_ce"
set "CLEAN=0"
set "NOINSTALLER=0"
set "INSTALLER=0"
set "MSIX=0"
set "MSIXONLY=0"
set "SKIT_PYI=0"
set "VERSION=2.5.1"
set "DIST_DIR=dist\windows"
set "BUILD_DIR=build\windows"

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="both" (
    set "BRAND=both"
    shift
    goto :parse_args
)
if /i "%~1"=="all" (
    set "BRAND=both"
    shift
    goto :parse_args
)
if /i "%~1"=="updater_ce" (
    set "BRAND=updater_ce"
    shift
    goto :parse_args
)
if /i "%~1"=="mediatek_installer" (
    set "BRAND=mediatek_installer"
    shift
    goto :parse_args
)
if /i "%~1"=="-Clean" (
    set "CLEAN=1"
    shift
    goto :parse_args
)
if /i "%~1"=="--clean" (
    set "CLEAN=1"
    shift
    goto :parse_args
)
if /i "%~1"=="-NoInstaller" (
    set "NOINSTALLER=1"
    shift
    goto :parse_args
)
if /i "%~1"=="--noinstaller" (
    set "NOINSTALLER=1"
    shift
    goto :parse_args
)
if /i "%~1"=="--no-installer" (
    set "NOINSTALLER=1"
    shift
    goto :parse_args
)
if /i "%~1"=="-Installer" (
    set "INSTALLER=1"
    shift
    goto :parse_args
)
if /i "%~1"=="--installer" (
    set "INSTALLER=1"
    shift
    goto :parse_args
)
if /i "%~1"=="-InstallerOnly" (
    set "INSTALLER=1"
    set "MSIX=0"
    set "NOINSTALLER=0"
    shift
    goto :parse_args
)
if /i "%~1"=="--installer-only" (
    set "INSTALLER=1"
    set "MSIX=0"
    set "NOINSTALLER=0"
    shift
    goto :parse_args
)
if /i "%~1"=="-Msix" (
    set "MSIX=1"
    shift
    goto :parse_args
)
if /i "%~1"=="--msix" (
    set "MSIX=1"
    shift
    goto :parse_args
)
if /i "%~1"=="-MsixOnly" (
    set "MSIX=1"
    set "MSIXONLY=1"
    set "NOINSTALLER=1"
    shift
    goto :parse_args
)
if /i "%~1"=="--msix-only" (
    set "MSIX=1"
    set "MSIXONLY=1"
    set "NOINSTALLER=1"
    shift
    goto :parse_args
)
if /i "%~1"=="-SkipPyInstaller" (
    set "SKIT_PYI=1"
    shift
    goto :parse_args
)
if /i "%~1"=="--skip-pyinstaller" (
    set "SKIT_PYI=1"
    shift
    goto :parse_args
)
if /i "%~1"=="-PackageOnly" (
    set "SKIT_PYI=1"
    shift
    goto :parse_args
)
if /i "%~1"=="--brand" (
    set "BRAND=%~2"
    shift
    shift
    goto :parse_args
)
if /i "%~1"=="-Brand" (
    set "BRAND=%~2"
    shift
    shift
    goto :parse_args
)
if /i "%~1"=="--help" goto :help
if /i "%~1"=="-help" goto :help
if /i "%~1"=="-h" goto :help
echo Unknown argument: %~1
goto :help

:args_done
cd /d "%~dp0"

set "PS_ARGS=-Brand %BRAND%"
if "%CLEAN%"=="1" set "PS_ARGS=%PS_ARGS% -Clean"
if "%NOINSTALLER%"=="1" set "PS_ARGS=%PS_ARGS% -NoInstaller"
if "%INSTALLER%"=="1" set "PS_ARGS=%PS_ARGS% -Installer"
if "%MSIX%"=="1" set "PS_ARGS=%PS_ARGS% -Msix"
if "%MSIXONLY%"=="1" set "PS_ARGS=%PS_ARGS% -MsixOnly"
if "%SKIT_PYI%"=="1" set "PS_ARGS=%PS_ARGS% -SkipPyInstaller"

echo ================================================================================
echo   Innioasis Updater CE / MediaTek Installer - Windows Build
echo ================================================================================
echo   Brand:           %BRAND%
echo   Clean previous:  %CLEAN%
echo   Inno Setup:      %INSTALLER% (Skip: %NOINSTALLER%)
echo   Store (MSIX):    %MSIX% (MSIX only: %MSIXONLY%)
echo   Skip PyInstaller:%SKIT_PYI%
echo   Working folder:  %~dp0
echo.
echo   Launching PowerShell build engine (build_windows.ps1)...
echo ================================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1" %PS_ARGS%
exit /b %ERRORLEVEL%

:help
echo Usage: build_windows.bat [updater_ce^|mediatek_installer] [-Clean] [-NoInstaller] [-Installer] [-Msix] [-MsixOnly] [-SkipPyInstaller]
echo.
echo Builds Updater CE and/or MediaTek Installer on Windows into dist\windows\.
echo Options:
echo   updater_ce         Build Updater CE only.
echo   mediatek_installer Build MediaTek Installer only.
echo   -Clean             Remove previous Windows build artifacts before compiling.
echo   -NoInstaller       Skip Inno Setup and build the portable folders and .zip archives only.
echo   -Installer         Ensure Inno Setup installer is compiled.
echo   -Msix              Pack the Microsoft Store (MSIX) package.
echo   -MsixOnly          Build/pack MSIX only (skips Inno Setup without requiring ISCC.exe).
echo   -SkipPyInstaller   Package existing compiled folder without re-running PyInstaller.
echo.
echo Prerequisites: Python 3.10+, PyInstaller, and Inno Setup 6 (optional for the
echo setup wizard). The app bundles SP Flash Tool from tools/windows/SP_Flash_Tool_v5.1904_Win.
exit /b 0
