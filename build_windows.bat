@echo off
rem Build script for Updater CE / MediaTek Installer on Windows.
rem Usage:
rem   build_windows.bat                     :: Updater CE into dist\windows (x64)
rem   build_windows.bat updater_ce          :: Updater CE only into dist\windows (x64)
rem   build_windows.bat mediatek_installer  :: MediaTek Installer only into dist\windows (x64)
rem   build_windows.bat --arch arm64        :: ARM64 build into dist\windows-arm64 (needs ARM64 Windows + Python)
rem   build_windows.bat updater_ce -Msix     :: also pack the Microsoft Store (MSIX) package

setlocal enabledelayedexpansion

set "BRAND=updater_ce"
set "ARCH="
set "CLEAN=0"
set "NOINSTALLER=0"
set "INSTALLER=0"
set "MSIX=0"
set "MSIXONLY=0"
set "SKIT_PYI=0"
set "NOLEGACY=0"
set "VERSION=2.5.5"
rem Output roots are chosen by build_windows.ps1 from the target architecture:
rem x64 -> dist\windows, ARM64 -> dist\windows-arm64.

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
if /i "%~1"=="arm64" (
    set "ARCH=arm64"
    shift
    goto :parse_args
)
if /i "%~1"=="x64" (
    set "ARCH=x64"
    shift
    goto :parse_args
)
if /i "%~1"=="--arch" (
    set "ARCH=%~2"
    shift
    shift
    goto :parse_args
)
if /i "%~1"=="-Arch" (
    set "ARCH=%~2"
    shift
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
if /i "%~1"=="-NoLegacy" (
    set "NOLEGACY=1"
    shift
    goto :parse_args
)
if /i "%~1"=="--no-legacy" (
    set "NOLEGACY=1"
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
if not "%ARCH%"=="" set "PS_ARGS=%PS_ARGS% -Arch %ARCH%"
if "%CLEAN%"=="1" set "PS_ARGS=%PS_ARGS% -Clean"
if "%NOINSTALLER%"=="1" set "PS_ARGS=%PS_ARGS% -NoInstaller"
if "%INSTALLER%"=="1" set "PS_ARGS=%PS_ARGS% -Installer"
if "%MSIX%"=="1" set "PS_ARGS=%PS_ARGS% -Msix"
if "%MSIXONLY%"=="1" set "PS_ARGS=%PS_ARGS% -MsixOnly"
if "%SKIT_PYI%"=="1" set "PS_ARGS=%PS_ARGS% -SkipPyInstaller"
if "%NOLEGACY%"=="1" set "PS_ARGS=%PS_ARGS% -NoLegacy"

echo ================================================================================
echo   Innioasis Updater CE / MediaTek Installer - Windows Build
echo ================================================================================
echo   Author:          Ryan Specter
echo   Brand:           %BRAND%
if not "%ARCH%"=="" (echo   Architecture:    %ARCH%) else (echo   Architecture:    auto-detect)
echo   Clean previous:  %CLEAN%
echo   Inno Setup:      %INSTALLER% (Skip: %NOINSTALLER%)
echo   Store (MSIX):    %MSIX% (MSIX only: %MSIXONLY%)
echo   Skip PyInstaller:%SKIT_PYI%
echo   Legacy flash:    %NOLEGACY% skip (0 = build dist\windows-legacy)
echo   Working folder:  %~dp0
echo.
echo   Launching PowerShell build engine (build_windows.ps1)...
echo ================================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1" %PS_ARGS%
exit /b %ERRORLEVEL%

:help
echo Usage: build_windows.bat [updater_ce^|mediatek_installer] [--arch arm64^|x64] [-Clean] [-NoInstaller] [-Installer] [-Msix] [-MsixOnly] [-SkipPyInstaller] [-NoLegacy]
echo.
echo Builds Updater CE and/or MediaTek Installer on Windows into dist\windows\ (x64)
echo or dist\windows-arm64\ (--arch arm64). ARM64 needs Windows on ARM: PyInstaller
echo cannot cross-compile from an x64 host.
echo Options:
echo   updater_ce         Build Updater CE only.
echo   mediatek_installer Build MediaTek Installer only.
echo   --arch arm64^|x64   Target CPU architecture (defaults to Python's machine architecture).
echo   -Clean             Remove previous Windows build artifacts before compiling.
echo   -NoInstaller       Skip Inno Setup and build the portable folder only.
echo   -Installer         Ensure Inno Setup installer is compiled.
echo   -Msix              Pack the Microsoft Store (MSIX) package.
echo   -MsixOnly          Build/pack MSIX only (skips Inno Setup without requiring ISCC.exe).
echo   -SkipPyInstaller   Package existing compiled folder without re-running PyInstaller.
echo   -NoLegacy          Skip the SP Flash Tool-only package for Windows XP/7/8
echo                      and 32-bit PCs (dist\windows-legacy).
echo.
echo Prerequisites: Python 3.10+, PyInstaller, and Inno Setup 6 (optional for the
echo setup wizard). The app bundles SP Flash Tool from tools/windows/SP_Flash_Tool_v5.1904_Win.
echo.
echo An x64 build also writes dist\windows\SP_Flash_Tool\ and SP_Flash_Tool-Windows.zip:
echo the tool without the app, for Windows XP/Vista/7/8/8.1 and 32-bit Windows,
echo where Updater CE cannot run. Publish that zip as a release asset so the
echo firmware packager can build rom-windows-legacy.zip packages.
exit /b 0
