# Compiles the Inno Setup installer from a finished PyInstaller folder.
#
#   .\scripts\build_windows_installer.ps1 -AppDir dist\windows\InnioasisUpdater -Version 2.5.0
#
# Produces dist\windows\UpdaterCE-Setup-<version>.exe (or MediaTekInstaller-Setup-<version>.exe)
# in the same architecture folder as the app it packages: dist\windows-arm64 for ARM64.

param(
    [Parameter(Mandatory = $false)]
    [string]$AppDir = "",
    [string]$Version = "",
    [string]$OutDir = "",
    [ValidateSet("", "updater_ce", "mediatek_installer")]
    [string]$Brand = "",
    [ValidateSet("", "x64", "arm64")]
    [string]$Arch = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not $AppDir) {
    $distRoot = if ($Arch -eq "arm64" -or $env:TARGET_ARCH -eq "arm64") { "dist\windows-arm64" } else { "dist\windows" }
    if ($Brand -eq "mediatek_installer") {
        $AppDir = Join-Path $ProjectRoot "$distRoot\MediaTekInstaller"
    } else {
        $AppDir = Join-Path $ProjectRoot "$distRoot\InnioasisUpdater"
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
    if (-not $Version) { $Version = "2.5.5" }
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

$isArm64 = ($Arch -eq "arm64") -or
           ($env:TARGET_ARCH -eq "arm64") -or
           ($AppDir -match "arm64")

$setupBaseName = if ($isMtk) { "MediaTekInstaller-Setup-$Version" } else { "UpdaterCE-Setup-$Version" }
if ($isArm64 -and -not ($setupBaseName -match "-arm64$")) {
    $setupBaseName += "-arm64"
}

# Locate ISCC.exe. Stay on Inno Setup 6: 7.1.1's SetupLdr helper is blocked
# by Application Control when it is copied to %TEMP% (error 4551), while the
# 6.x helper is what the installers that work were built with. Do not fall
# back to 7 just because it is installed.
function Find-ISCC {
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd -and ($cmd.Source -notmatch 'Inno Setup 7')) { return $cmd.Source }
    foreach ($p in @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
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
if ($isArm64) {
    $isccArgs += "/DMyIsArm64"
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
