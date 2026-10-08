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
    [switch]$NoInstaller,
    [string]$Brand = "updater_ce"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

# Packaging brand: mediatek_installer builds the generic cross-platform
# MediaTek Installer (its own name, offline-only inside the app); the default
# keeps building Updater CE exactly as before.
$Brand = $Brand.ToLower().Replace("-", "_")
switch ($Brand) {
    "updater_ce" {
        $AppDisplay = "Updater CE"
        $AppDirName = "InnioasisUpdater"
        $AppExeName = "InnioasisUpdater.exe"
        $InstallerBase = "UpdaterCE-Setup"
        $IsMediaTek = $false
    }
    "mediatek_installer" {
        # dist/exe stay slugs like Updater CE's ("InnioasisUpdater"); "MediaTek
        # Installer" is the name the user sees (installer, Start Menu, window).
        $AppDisplay = "MediaTek Installer"
        $AppDirName = "MediaTekInstaller"
        $AppExeName = "MediaTekInstaller.exe"
        $InstallerBase = "MediaTekInstaller-Setup"
        $IsMediaTek = $true
    }
    default {
        Write-Error "[ERROR] Unknown -Brand '$Brand' (use updater_ce or mediatek_installer)"
        exit 1
    }
}
$env:BUILD_BRAND = $Brand

# A build produces BOTH front ends: Updater CE and the generic MediaTek
# Installer (same internals, different identity). -Brand builds just one.
# Each brand is built by a child run of this script, so the per-brand path is
# exactly the one used for a single-brand build.
if (-not $env:INNOASIS_BRAND_LOCK) {
    if ($PSBoundParameters.ContainsKey("Brand")) {
        $brands = @($Brand)
    } else {
        $brands = @("updater_ce", "mediatek_installer")
    }

    if ($Clean) {
        Write-Host "[INFO] Cleaning build and dist folders..." -ForegroundColor Yellow
        if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
        foreach ($dir in @("InnioasisUpdater", "MediaTekInstaller")) {
            if (Test-Path "dist\$dir") { Remove-Item -Recurse -Force "dist\$dir" }
        }
    }

    $env:INNOASIS_BRAND_LOCK = "1"
    foreach ($b in $brands) {
        & $PSCommandPath -Brand $b -Clean:$false -NoInstaller:$NoInstaller
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    $env:INNOASIS_BRAND_LOCK = $null

    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "[SUCCESS] Built for Windows: $($brands -join ', ')" -ForegroundColor Green
    Get-ChildItem "dist" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "          dist\$($_.Name)" -ForegroundColor Green }
    Write-Host "======================================================================" -ForegroundColor Cyan
    exit 0
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host " Building $AppDisplay for Windows" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

# 1. Clean if requested
if ($Clean) {
    Write-Host "[INFO] Cleaning build and dist folders..." -ForegroundColor Yellow
    if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
    if (Test-Path "dist\$AppDirName") { Remove-Item -Recurse -Force "dist\$AppDirName" }
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
# Bake the brand into the frozen app; the spec also names dist/ after it. Every
# build writes this file, so a previous brand can never leak into this one.
& $PythonExe scripts/set_build_brand.py $Brand
Write-Host "[INFO] Compiling with PyInstaller (InnioasisUpdater.spec, brand $Brand)..." -ForegroundColor Cyan
& $PythonExe -m PyInstaller --clean --noconfirm InnioasisUpdater.spec

# 5. Verify SP Flash Tool payload
$SpSrc = Join-Path $ScriptDir "tools\windows\SP_Flash_Tool_v5.1904_Win"
$SpDst = Join-Path $ScriptDir "dist\$AppDirName\SP_Flash_Tool"
if (Test-Path $SpSrc) {
    if (-not (Test-Path $SpDst)) {
        Write-Host "[INFO] Copying SP Flash Tool Windows payload..." -ForegroundColor Cyan
        Copy-Item -Recurse -Force $SpSrc $SpDst
    }
} else {
    Write-Warning "SP Flash Tool source not found at $SpSrc"
}

$env:BUILD_BRAND = $null
& $PythonExe scripts/set_build_brand.py --reset | Out-Null

Write-Host "[SUCCESS] Standalone application ready at dist\$AppDirName\$AppExeName" -ForegroundColor Green

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
        $IsccArgs = @(
            "/DMyAppName=$AppDisplay",
            "/DMyAppExeName=$AppExeName",
            "/DMyDistDir=..\dist\$AppDirName",
            "/DMyOutputBase=$InstallerBase"
        )
        # Its own [Setup] identity, so both apps can be installed side by side.
        if ($IsMediaTek) { $IsccArgs += "/DMyIsMediaTek=1" }
        $IsccArgs += "scripts\build_windows_installer.iss"
        & $Iscc @IsccArgs
        Write-Host "[SUCCESS] Installer generated in dist\" -ForegroundColor Green
    } else {
        Write-Host "[NOTE] Inno Setup 6 not found; skipping installer (.exe installer)." -ForegroundColor Yellow
        Write-Host "       The standalone app is available in dist\$AppDirName\" -ForegroundColor Yellow
    }
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host " Build finished successfully!" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Cyan
