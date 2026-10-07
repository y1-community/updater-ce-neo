<#
.SYNOPSIS
    Builds Innioasis Updater CE for Windows (Executable and Inno Setup Installer).
.DESCRIPTION
    Compiles Innioasis Updater CE using PyInstaller, bundles the SP Flash Tool
    payload, and compiles the Inno Setup installer script.
.PARAMETER Clean
    Clean build and dist artifacts before compiling.
.PARAMETER NoInstaller
    Skip Inno Setup compilation even if ISCC is found.
#>
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$NoInstaller
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host " Building Innioasis Updater CE for Windows" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

# 1. Clean if requested
if ($Clean) {
    Write-Host "[INFO] Cleaning build and dist folders..." -ForegroundColor Yellow
    if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
    if (Test-Path "dist\InnioasisUpdater") { Remove-Item -Recurse -Force "dist\InnioasisUpdater" }
}

# 2. Detect Python
$PythonExe = "python"
if (Test-Path ".venv\Scripts\python.exe") {
    $PythonExe = ".venv\Scripts\python.exe"
} elseif (Test-Path ".venv-build\Scripts\python.exe") {
    $PythonExe = ".venv-build\Scripts\python.exe"
}

try {
    $PyVersion = & $PythonExe --version 2>&1
    Write-Host "[INFO] Using Python: $PythonExe ($PyVersion)" -ForegroundColor Green
} catch {
    Write-Error "[ERROR] Python 3.10+ not found in PATH or virtualenv."
    exit 1
}

# 3. Dependencies
Write-Host "[INFO] Checking Python dependencies..." -ForegroundColor Cyan
& $PythonExe -m pip install -q -r requirements.txt pyinstaller

# 4. PyInstaller build
Write-Host "[INFO] Compiling with PyInstaller (InnioasisUpdater.spec)..." -ForegroundColor Cyan
& $PythonExe -m PyInstaller --clean --noconfirm InnioasisUpdater.spec

# 5. Verify SP Flash Tool payload
$SpSrc = Join-Path $ScriptDir "tools\windows\SP_Flash_Tool_v5.1904_Win"
$SpDst = Join-Path $ScriptDir "dist\InnioasisUpdater\SP_Flash_Tool"
if (Test-Path $SpSrc) {
    if (-not (Test-Path $SpDst)) {
        Write-Host "[INFO] Copying SP Flash Tool Windows payload..." -ForegroundColor Cyan
        Copy-Item -Recurse -Force $SpSrc $SpDst
    }
} else {
    Write-Warning "SP Flash Tool source not found at $SpSrc"
}

Write-Host "[SUCCESS] Standalone application ready at dist\InnioasisUpdater\InnioasisUpdater.exe" -ForegroundColor Green

# 6. Inno Setup Compilation
if (-not $NoInstaller) {
    $IsccCandidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    ) | Where-Object { $_ -and (Test-Path $_) }

    if ($IsccCandidates.Count -gt 0) {
        $Iscc = $IsccCandidates[0]
        Write-Host "[INFO] Compiling installer with Inno Setup: $Iscc" -ForegroundColor Cyan
        & $Iscc "scripts\build_windows_installer.iss"
        Write-Host "[SUCCESS] Installer generated in dist\" -ForegroundColor Green
    } else {
        Write-Host "[NOTE] Inno Setup 6 not found; skipping installer (.exe installer)." -ForegroundColor Yellow
        Write-Host "       The standalone app is available in dist\InnioasisUpdater\" -ForegroundColor Yellow
    }
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host " Build finished successfully!" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Cyan
