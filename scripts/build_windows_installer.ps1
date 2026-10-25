# Compiles the Inno Setup installer from a finished PyInstaller folder.
#
#   .\scripts\build_windows_installer.ps1 -AppDir dist\windows\InnioasisUpdater -Version 2.5.0
#
# Produces dist\windows\UpdaterCE-Setup-<version>.exe (or MediaTekInstaller-Setup-<version>.exe)

param(
    [Parameter(Mandatory = $false)]
    [string]$AppDir = "",
    [string]$Version = "",
    [string]$OutDir = "",
    [ValidateSet("", "updater_ce", "mediatek_installer")]
    [string]$Brand = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not $AppDir) {
    if ($Brand -eq "mediatek_installer") {
        $AppDir = Join-Path $ProjectRoot "dist\windows\MediaTekInstaller"
    } else {
        $AppDir = Join-Path $ProjectRoot "dist\windows\InnioasisUpdater"
    }
}

if (-not (Test-Path $AppDir)) {
    throw "App directory not found: $AppDir"
}
$AppDir = (Resolve-Path $AppDir).Path.TrimEnd('\')

if (-not $Version) {
    $ConfigFile = Join-Path $ProjectRoot "src\config.py"
    if (Test-Path $ConfigFile) {
        $match = Select-String -Path $ConfigFile -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
        if ($match -and $match.Matches.Count -gt 0) {
            $Version = $match.Matches[0].Groups[1].Value
        }
    }
    if (-not $Version) { $Version = "2.5.0" }
}

if (-not $OutDir) {
    $OutDir = Split-Path -Parent $AppDir
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path.TrimEnd('\')

# Detect brand from AppDir or exe if not explicitly provided
$isMtk = ($Brand -eq "mediatek_installer") -or
         (Test-Path (Join-Path $AppDir "MediaTekInstaller.exe")) -or
         ($AppDir -match "MediaTek")

$setupBaseName = if ($isMtk) { "MediaTekInstaller-Setup-$Version" } else { "UpdaterCE-Setup-$Version" }

# Locate ISCC.exe
function Find-ISCC {
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$IsccExe = Find-ISCC
if (-not $IsccExe) {
    Write-Error "ISCC.exe (Inno Setup 6) was not found. Install Inno Setup 6 and run again."
    exit 1
}

$issScript = Join-Path $ProjectRoot "scripts\build_windows_installer.iss"
$isccArgs = @(
    "/DMyAppVersion=$Version",
    "/DMyRepoRoot=$ProjectRoot\",
    "/DMyDistDir=$AppDir",
    "/O$OutDir",
    "/F$setupBaseName"
)
if ($isMtk) {
    $isccArgs += "/DMyIsMediaTek"
}
$isccArgs += $issScript

Write-Host "  Compiling Inno Setup wizard: $setupBaseName.exe..." -ForegroundColor DarkGray
& $IsccExe @isccArgs
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed (exit code $LASTEXITCODE)"
}

$targetSetupExe = Join-Path $OutDir "$setupBaseName.exe"
if (Test-Path $targetSetupExe) {
    $setupSizeMb = [math]::Round((Get-Item $targetSetupExe).Length / 1MB, 2)
    Write-Host "  -> Setup installer created: $targetSetupExe ($setupSizeMb MB)" -ForegroundColor Green
} else {
    Write-Host "  -> Setup installer created in $OutDir" -ForegroundColor Green
}
