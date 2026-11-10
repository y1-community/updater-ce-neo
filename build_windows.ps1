param(
    [ValidateSet("", "both", "all", "updater_ce", "mediatek_installer")]
    [string]$Brand = "updater_ce",
    [ValidateSet("", "x64", "x86_64", "amd64", "arm64", "aarch64")]
    [string]$Arch = "",
    [switch]$Clean,
    [switch]$NoInstaller,
    [switch]$Installer,
    [switch]$InstallerOnly,
    # Also pack the finished folder as an MSIX for Microsoft Store submission.
    # Needs the Windows SDK (MakeAppx.exe); identity values come from
    # scripts\msix\msix_identity.<brand>.json (see msix_identity.example.json).
    [switch]$Msix,
    [switch]$MsixOnly,
    [switch]$SkipPyInstaller,
    # The SP Flash Tool-only folder (dist\windows-legacy) is what serves Windows
    # XP/Vista/7/8/8.1 and 32-bit PCs, where Updater CE cannot run. Pass
    # -NoLegacy to skip it; releases publish it as a separate ZIP.
    [switch]$NoLegacy,
    # Local test certificate (.pfx) for the MSIX. The Store signs the upload
    # itself, so this is only for installing the package on a test machine.
    [string]$MsixSignCert = "",
    [string]$MsixSignPassword = ""
)

if ($MsixOnly -or ($Msix -and -not $Installer)) {
    $Msix = $true
    $NoInstaller = $true
}

if ($InstallerOnly) {
    $Installer = $true
    $NoInstaller = $false
    $Msix = $false
}

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Version = "2.5.3"
$ConfigFile = Join-Path $ProjectRoot "src\config.py"
if (Test-Path $ConfigFile) {
    $match = Select-String -Path $ConfigFile -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($match -and $match.Matches.Count -gt 0) {
        $Version = $match.Matches[0].Groups[1].Value
    }
}

# Fresh clones have no virtualenv and no packages: build the same .venv-build
# every other platform uses (scripts\bootstrap_build_env.py) instead of failing
# later on a missing PyInstaller/PySide6 import. Skipped for -SkipPyInstaller,
# which only repackages an existing build.
if (-not $SkipPyInstaller) {
    $BootstrapScript = Join-Path $ProjectRoot "scripts\bootstrap_build_env.py"
    if (Test-Path $BootstrapScript) {
        $BootstrapLauncher = $null
        foreach ($c in @("py", "python", "python3")) {
            $bootstrapCmd = Get-Command $c -ErrorAction SilentlyContinue
            if ($bootstrapCmd) { $BootstrapLauncher = $bootstrapCmd.Source; break }
        }
        if (-not $BootstrapLauncher) {
            Write-Error "Python 3.10+ is required. Install it from https://www.python.org/downloads/windows/ (or: winget install -e --id Python.Python.3.12), then run this build again."
            exit 1
        }
        # The bootstrapper reports progress on stderr; under
        # $ErrorActionPreference = "Stop" Windows PowerShell turns that into a
        # terminating NativeCommandError, so collect it and judge the exit code.
        $BootstrapErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $BootstrapOutput = & $BootstrapLauncher $BootstrapScript 2>&1
        $BootstrapExitCode = $LASTEXITCODE
        $ErrorActionPreference = $BootstrapErrorAction
        if ($BootstrapExitCode -ne 0) {
            $BootstrapOutput | ForEach-Object { Write-Host $_ }
            Write-Error "Could not prepare the Python build environment (see the messages above)."
            exit 1
        }
        $BootstrapPython = $BootstrapOutput | Select-String -Pattern '^PYTHON=' | Select-Object -Last 1
        if ($BootstrapPython) {
            $env:PYTHON = ($BootstrapPython.Line -replace '^PYTHON=', '')
        }
    }
}

# Locate Python / PyInstaller
$PythonExe = $null
$isExplicitArm64 = ($Arch -match "arm64|aarch64")
if ($isExplicitArm64) {
    # Search for an ARM64-specific Python interpreter first
    $arm64Candidates = @(
        (Join-Path $ProjectRoot ".venv-arm64\Scripts\python.exe"),
        "C:\Program Files\Python313-arm64\python.exe",
        "C:\Program Files\Python312-arm64\python.exe",
        "C:\Program Files\Python311-arm64\python.exe",
        "C:\Program Files\Python310-arm64\python.exe"
    )
    foreach ($c in $arm64Candidates) {
        if (Test-Path $c) {
            $PythonExe = $c
            break
        }
    }
}
if (-not $PythonExe) {
    $candidates = @(
        (Join-Path $ProjectRoot ".venv-build\Scripts\python.exe"),
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $PythonExe = $c
            break
        }
    }
}
if (-not $PythonExe) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}
if (-not $PythonExe) {
    Write-Error "Python 3.10+ could not be found."
    exit 1
}

# Determine target architecture from -Arch or from the Python interpreter itself
$pyArch = (& $PythonExe -c "import platform; print(platform.machine().lower())").Trim()
$targetArch = if ($Arch) {
    if ($Arch -match "arm64|aarch64") { "arm64" } else { "x64" }
} else {
    if ($pyArch -match "arm64|aarch64") { "arm64" } else { "x64" }
}
$isArm64 = ($targetArch -eq "arm64")
$env:TARGET_ARCH = $targetArch
$env:BUILD_ARCH = $targetArch

# One output root per architecture: an ARM64 build must never robocopy /MIR
# over the x64 portable folder that a release was cut from.
$DistDir = Join-Path $ProjectRoot $(if ($isArm64) { "dist\windows-arm64" } else { "dist\windows" })
$BuildDir = Join-Path $ProjectRoot $(if ($isArm64) { "build\windows-arm64" } else { "build\windows" })

# PyInstaller freezes for the interpreter that runs it and cannot cross-compile,
# so an x64 Python can only ever produce an x64 bundle. Refuse to continue
# rather than shipping something labelled ARM64 that is really x64.
$pyIsArm64 = ($pyArch -match "arm64|aarch64|armv8")
$pyBits = (& $PythonExe -c "import struct; print(struct.calcsize('P') * 8)").Trim()
if ($pyBits -ne "64") {
    Write-Error "The Python at $PythonExe is $pyBits-bit; this app requires a 64-bit interpreter."
    exit 1
}
if ($pyIsArm64 -ne $isArm64) {
    if ($isArm64) {
        $wanted = "ARM64"
        $guidance = @"
Build it on Windows on ARM (a Snapdragon PC/VM, or a windows-11-arm CI runner) with an ARM64 Python:
  py -3.12-arm64 -m venv .venv-arm64
  .venv-arm64\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
  .\build_windows.ps1 -Arch arm64        :: -> dist\windows-arm64
"@
    } else {
        $wanted = "x64"
        $guidance = "Build it on an Intel/AMD Windows host with an x64 Python (omit -Arch on that machine)."
    }
    Write-Error @"
Cannot build $wanted from $PythonExe ($pyArch): PyInstaller cannot cross-compile, it only builds for the interpreter running it.
$guidance
"@
    exit 1
}

# PySide6 6.12.0 on Python < 3.12 leaks a None reference per paint call and the
# frozen app aborts ("none_dealloc: deallocating None") within a minute.
if (-not $SkipPyInstaller) {
    & $PythonExe (Join-Path $ProjectRoot "scripts\check_pyside_refcount.py")
    if ($LASTEXITCODE -eq 1) {
        Write-Error "Leaking PySide6 build environment; run: $PythonExe -m pip install -r requirements.txt"
        exit 1
    }
}

# Locate Inno Setup (ISCC.exe). Stay on Inno Setup 6: 7.1.1's SetupLdr
# helper is blocked by Application Control when it is copied to %TEMP%
# (error 4551), while the 6.x helper is what working installers use. Do not
# fall back to 7 just because it is installed.
$IsccExe = $null
$isccCmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
if ($isccCmd -and ($isccCmd.Source -notmatch 'Inno Setup 7')) {
    $IsccExe = $isccCmd.Source
} else {
    foreach ($p in @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )) {
        if (Test-Path $p) {
            $IsccExe = $p
            break
        }
    }
}
if (-not $IsccExe) {
    if ($Installer -or $InstallerOnly) {
        Write-Error "ISCC.exe (Inno Setup 6) not found. Install Inno Setup 6."
        exit 1
    } elseif ($Msix) {
        Write-Host "ISCC.exe (Inno Setup 6) not found. Skipping Inno Setup installer; proceeding with MSIX packaging." -ForegroundColor Yellow
        $NoInstaller = $true
    } elseif (-not $NoInstaller) {
        Write-Host "ISCC.exe (Inno Setup 6) not found. Skipping Inno Setup installer (the portable folder will still be generated)." -ForegroundColor Yellow
        $NoInstaller = $true
    }
}

if ($Clean) {
    Write-Host "Cleaning previous Windows build artifacts in $DistDir and $BuildDir..."
    if (Test-Path $DistDir) { Remove-Item -Recurse -Force $DistDir -ErrorAction SilentlyContinue }
    if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir -ErrorAction SilentlyContinue }
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

$BrandsToBuild = @()
if ($Brand -eq "both" -or $Brand -eq "all") {
    $BrandsToBuild = @("updater_ce", "mediatek_installer")
} elseif ($Brand -eq "mediatek_installer") {
    $BrandsToBuild = @("mediatek_installer")
} else {
    $BrandsToBuild = @("updater_ce")
}

function Write-StepBanner([int]$step, [int]$total, [string]$title, [string]$desc) {
    Write-Host ""
    Write-Host "================================================================================" -ForegroundColor Cyan
    Write-Host "  [Step $step/$total] $title" -ForegroundColor Green
    if ($desc) {
        Write-Host "  $desc" -ForegroundColor DarkGray
    }
    Write-Host "================================================================================" -ForegroundColor Cyan
}

$BrandFile = Join-Path $ProjectRoot "src\_build_brand.py"

function Set-BuildBrand([string]$b) {
    Set-Content -Path $BrandFile -Value "# Auto-generated by build_windows.ps1 -- do not commit.`nBUILD_BRAND = '$b'`n" -Encoding UTF8
}

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "  Innioasis Updater Windows Package Builder (v$Version)" -ForegroundColor Yellow
Write-Host "  Author:          Ryan Specter"
Write-Host "  Target brand(s): $($BrandsToBuild -join ', ')"
Write-Host "  Architecture:    $targetArch"
Write-Host "  Destination:     $DistDir"
Write-Host "================================================================================" -ForegroundColor Cyan

# Snapshot innioasis.app's catalogue/notices/donors/blocklist into the bundle so
# a first launch works offline. Best-effort: existing copies are kept on failure.
Write-Host "  -> Refreshing bundled online data (catalogue, notices, donors)..."
& $PythonExe (Join-Path $ProjectRoot "scripts\fetch_online_data.py")

try {
    $totalBrands = $BrandsToBuild.Count
    $brandIdx = 0
    foreach ($b in $BrandsToBuild) {
        $brandIdx++
        $isMtk = ($b -eq "mediatek_installer")
        $appSlug = if ($isMtk) { "MediaTekInstaller" } else { "InnioasisUpdater" }
        $brandDisplay = if ($isMtk) { "MediaTek Installer" } else { "Innioasis Updater CE" }
        $archSuffix = if ($isArm64) { "-arm64" } else { "" }
        $setupBaseName = if ($isMtk) { "MediaTekInstaller-Setup-$Version$archSuffix" } else { "UpdaterCE-Setup-$Version$archSuffix" }

        Write-Host ""
        Write-Host "********************************************************************************" -ForegroundColor Yellow
        Write-Host "  Building Package $brandIdx of $totalBrands : $brandDisplay ($appSlug)" -ForegroundColor Yellow
        Write-Host "********************************************************************************" -ForegroundColor Yellow

        Set-BuildBrand $b
        $env:BUILD_BRAND = $b

        # Compile, validate, stage the portable folder, then build the setup
        # wizard. A portable ZIP is deliberately not part of this script:
        # anyone who wants one can archive the finished folder themselves.
        $totalSteps = 3
        if (-not $NoInstaller -and $IsccExe) { $totalSteps++ }
        if ($Msix) { $totalSteps++ }

        if ($SkipPyInstaller) {
            $stagedAppDir = Join-Path $DistDir $appSlug
            if (-not (Test-Path $stagedAppDir)) {
                throw "Cannot skip PyInstaller: staged directory not found at $stagedAppDir"
            }
            Write-Host "  -> Skipping PyInstaller step; packaging existing app directory at $stagedAppDir" -ForegroundColor Yellow
        } else {
            # Stage build on local disk first to avoid SMB lock contention on network shares (e.g. Z:\)
            $tempWorkDir = Join-Path $env:TEMP "updater_pyi_work_$b"
            $tempDistDir = Join-Path $env:TEMP "updater_pyi_dist_$b"
            if (Test-Path $tempWorkDir) { Remove-Item -Recurse -Force $tempWorkDir -ErrorAction SilentlyContinue }
            if (Test-Path $tempDistDir) { Remove-Item -Recurse -Force $tempDistDir -ErrorAction SilentlyContinue }
            New-Item -ItemType Directory -Force -Path $tempWorkDir | Out-Null
            New-Item -ItemType Directory -Force -Path $tempDistDir | Out-Null

            Write-StepBanner 1 $totalSteps "Compiling with PyInstaller" "Bundling Python runtime, Qt libraries, and application assets into standalone directory..."
            Write-Host "  -> Running PyInstaller for $b using InnioasisUpdater.spec..."
            & $PythonExe -m PyInstaller --clean --noconfirm --distpath $tempDistDir --workpath $tempWorkDir "InnioasisUpdater.spec"
            if ($LASTEXITCODE -ne 0) {
                throw "PyInstaller failed for $b (exit code $LASTEXITCODE)"
            }
            Write-Host "  -> PyInstaller compilation completed successfully." -ForegroundColor Green

            $stagedAppDir = Join-Path $tempDistDir $appSlug
            $stagedExe = Join-Path $stagedAppDir "$appSlug.exe"
            if (-not (Test-Path $stagedExe)) {
                throw "Expected executable not found: $stagedExe"
            }

            Write-StepBanner 2 $totalSteps "Validating Bundled Payload" "Ensuring $appSlug.exe and runtime payload are present..."
            if ($isArm64) {
                Write-Host "  -> Target architecture is ARM64 ($targetArch)." -ForegroundColor Cyan
                Write-Host "  -> On ARM64 Windows, MTKClient is the primary flashing engine (SP Flash Tool is x86-only and omitted)." -ForegroundColor Green
            } else {
                # The SP Flash Tool and MediaTek Download Agents are required payload for
                # flashing devices on x64. Fail the build rather than shipping an installer without them.
                $requiredPayload = @(
                    "SP_Flash_Tool\flash_tool.exe",
                    "SP_Flash_Tool\MTK_AllInOne_DA.bin",
                    "SP_Flash_Tool\DA_PL.bin",
                    "SP_Flash_Tool\DA_SWSEC.bin"
                )
                foreach ($rel in $requiredPayload) {
                    $payloadPath = Join-Path $stagedAppDir $rel
                    if (-not (Test-Path $payloadPath)) {
                        throw "Required payload missing from build output: $payloadPath"
                    }
                }
                Write-Host "  -> SP Flash Tool and MediaTek Download Agents bundled beside application." -ForegroundColor Green
            }

            # Stage DriverInstall.exe and friendly numbered entrypoints for portable distribution
            $driverSource = Join-Path $ProjectRoot "tools\DriverInstall.exe"
            $driverPortableTarget = Join-Path $stagedAppDir "1. Install Driver First and Reboot PC.exe"
            if (Test-Path $driverSource) {
                Copy-Item -Path $driverSource -Destination $driverPortableTarget -Force
                Write-Host "  -> Bundled '1. Install Driver First and Reboot PC.exe' from tools\DriverInstall.exe" -ForegroundColor Green
            } else {
                Write-Warning "Driver installer not found at $driverSource; '1. Install Driver First and Reboot PC.exe' was skipped."
            }

            $appPortableAlias = Join-Path $stagedAppDir "2. Install Firmware.exe"
            Copy-Item -Path $stagedExe -Destination $appPortableAlias -Force
            Write-Host "  -> Created friendly entry point: '2. Install Firmware.exe' -> $appSlug.exe" -ForegroundColor Green

            # Stage UnRAR.exe into tools/ folder beside the executable for .rar unpacking compatibility
            $unrarArchDir = if ($isArm64) { "arm64" } else { "x64" }
            $unrarSource = Join-Path $ProjectRoot "tools\windows\$unrarArchDir\UnRAR.exe"
            if (-not (Test-Path $unrarSource)) {
                $unrarSource = Join-Path $ProjectRoot "tools\windows\UnRAR.exe"
            }
            $unrarTargetDir = Join-Path $stagedAppDir "tools"
            $unrarTarget = Join-Path $unrarTargetDir "UnRAR.exe"
            if (Test-Path $unrarSource) {
                if (-not (Test-Path $unrarTargetDir)) { New-Item -ItemType Directory -Force -Path $unrarTargetDir | Out-Null }
                Copy-Item -Path $unrarSource -Destination $unrarTarget -Force
                Write-Host "  -> Bundled 'tools\UnRAR.exe' ($unrarArchDir) for cross-platform .rar extraction" -ForegroundColor Green
            }

            Write-StepBanner 3 $totalSteps "Staging Portable Folder" "Syncing compiled distribution files into $DistDir\$appSlug..."
            $targetAppDir = Join-Path $DistDir $appSlug
            & robocopy $stagedAppDir $targetAppDir /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NC /NS | Out-Null
            $roboExit = $LASTEXITCODE
            $deferredSync = $false
            if ($roboExit -ge 8) {
                Write-Warning "Some files in $targetAppDir are currently in use (robocopy exit $roboExit). Continuing to package the installer; the portable folder will finish syncing as soon as $appSlug.exe closes."
                $deferredSync = $true
            } else {
                Write-Host "  -> Portable folder synced to $targetAppDir" -ForegroundColor Green
            }
        }

        if (-not $NoInstaller -and $IsccExe) {
            Write-StepBanner 4 $totalSteps "Building Windows Setup Wizard" "Compiling Inno Setup wizard ($setupBaseName.exe)..."
            $installerScript = Join-Path $ProjectRoot "scripts\build_windows_installer.ps1"
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installerScript -AppDir $stagedAppDir -Version $Version -OutDir $DistDir -Brand $b -Arch $targetArch
            if ($LASTEXITCODE -ne 0) {
                throw "Inno Setup failed for $b (exit code $LASTEXITCODE)"
            }
        }

        if ($Msix) {
            $msixStep = 3 + $(if (-not $NoInstaller -and $IsccExe) { 1 } else { 0 }) + 1
            Write-StepBanner $msixStep $totalSteps "Building Microsoft Store Package" "Packing the app folder as an MSIX (MakeAppx) for Store submission..."
            # Per-brand identity from Partner Center; the generic file is the
            # fallback for a single-product layout.
            $identityCandidates = @(
                (Join-Path $ProjectRoot "scripts\msix\msix_identity_$b.json"),
                (Join-Path $ProjectRoot "scripts\msix\msix_identity.json")
            )
            $identityFile = $null
            foreach ($candidate in $identityCandidates) {
                if (Test-Path $candidate) { $identityFile = $candidate; break }
            }
            $msixArgs = @(
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", (Join-Path $ProjectRoot "scripts\build_windows_msix.ps1"),
                "-AppDir", $stagedAppDir,
                "-Version", $Version,
                "-OutDir", $DistDir,
                "-Arch", $targetArch
            )
            if ($identityFile) { $msixArgs += @("-IdentityFile", $identityFile) }
            if ($MsixSignCert) {
                $msixArgs += @("-SignCert", $MsixSignCert)
                if ($MsixSignPassword) { $msixArgs += @("-SignPassword", $MsixSignPassword) }
            }
            & powershell.exe @msixArgs
            if ($LASTEXITCODE -ne 0) {
                throw "MSIX packaging failed for $b (exit code $LASTEXITCODE)"
            }
        } else {
            Write-Host "  (Pass -Msix to also produce the Microsoft Store package for this build.)" -ForegroundColor DarkGray
        }

        if ($tempWorkDir) {
            Remove-Item -Recurse -Force $tempWorkDir -ErrorAction SilentlyContinue
        }
        if ($deferredSync) {
            $syncCmd = "while (Get-Process -Name '$appSlug' -ErrorAction SilentlyContinue) { Start-Sleep -Seconds 2 }; robocopy '$stagedAppDir' '$targetAppDir' /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NC /NS | Out-Null; Remove-Item -Recurse -Force '$tempDistDir' -ErrorAction SilentlyContinue"
            Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-WindowStyle", "Hidden", "-Command", $syncCmd)
        } elseif ($tempDistDir) {
            Remove-Item -Recurse -Force $tempDistDir -ErrorAction SilentlyContinue
        }
    }

    # ------------------------------------------------------------------
    # Legacy flash-tool package (Windows XP / Vista / 7 / 8 / 8.1, 32-bit)
    #
    # Updater CE needs 64-bit Windows 10 1809 or newer, so on an older or
    # 32-bit PC the only route is SP Flash Tool on its own. This stages a
    # folder that holds the flash tool and nothing else (no updater, no Python
    # runtime), then zips it with donate.txt and a short README. Releases
    # publish the ZIP as Innioasis-SP-Flash-Tool-windows-legacy.zip; the
    # firmware mirrors repack it beside a rom as <romzipname>-windows-legacy.zip.
    # ------------------------------------------------------------------
    if (-not $NoLegacy) {
        $LegacyRoot = Join-Path $ProjectRoot "dist\windows-legacy"
        $LegacyToolDir = Join-Path $LegacyRoot "SP_Flash_Tool"
        $SpFlashSource = Join-Path $ProjectRoot "tools\windows\SP_Flash_Tool_v5.1904_Win"
        if ($isArm64) {
            Write-Host "  -> ARM64 build: SP Flash Tool is x86-only, so the legacy flash-tool package is not produced here." -ForegroundColor Yellow
        } elseif (-not (Test-Path $SpFlashSource)) {
            Write-Warning "SP Flash Tool source not found at $SpFlashSource; the legacy Windows package was skipped."
        } else {
            Write-StepBanner ($totalSteps + 1) ($totalSteps + 1) "Staging Legacy Flash Tool Folder" "Copying SP Flash Tool only into dist\windows-legacy for Windows XP/7/8 and 32-bit PCs..."
            if (Test-Path $LegacyToolDir) { Remove-Item -Recurse -Force $LegacyToolDir }
            New-Item -ItemType Directory -Force -Path $LegacyToolDir | Out-Null
            & robocopy $SpFlashSource $LegacyToolDir /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NC /NS | Out-Null
            if ($LASTEXITCODE -ge 8) {
                throw "Could not copy SP Flash Tool into $LegacyToolDir (robocopy exit $LASTEXITCODE)"
            }
            # Flash history and developer logs must never travel to a reader.
            foreach ($junk in @("history.ini", "flash_tool.log")) {
                $junkPath = Join-Path $LegacyToolDir $junk
                if (Test-Path $junkPath) { Remove-Item -Force $junkPath }
            }

            $DonateText = @"
Innioasis Updater CE and the firmware archive are run by the community, for free.

If this package saved your player, you can say thanks here:
  https://innioasis.app/donate.html
  https://ko-fi.com/teamslide
  https://github.com/sponsors/y1-community
"@
            Set-Content -Path (Join-Path $LegacyRoot "donate.txt") -Value $DonateText -Encoding UTF8

            $ReadmeText = @"
Innioasis SP Flash Tool - portable package for Windows XP / Vista / 7 / 8 / 8.1 (32-bit and 64-bit)

This folder contains SP Flash Tool and its MediaTek download agents, nothing else.
Use it when Updater CE cannot run on your PC: Updater CE needs 64-bit Windows 10 (1809 or newer).

How to use it:
  1. Install the MediaTek USB driver from the Driver folder, then restart the PC.
  2. Download the firmware package for your model from https://innioasis.app/firmware-list.html
     and choose the Windows XP/7/8 (32/64-bit) package.
  3. Unpack the firmware package beside this folder, then open flash_tool.exe.
  4. Choose the scatter file from the firmware folder, press Download, then connect
     the player while it is switched off.

Full guide: https://innioasis.app/windows-legacy.html
"@
            Set-Content -Path (Join-Path $LegacyRoot "README.txt") -Value $ReadmeText -Encoding UTF8

            $LegacyZip = Join-Path $LegacyRoot "Innioasis-SP-Flash-Tool-windows-legacy.zip"
            if (Test-Path $LegacyZip) { Remove-Item -Force $LegacyZip }
            Compress-Archive -Path $LegacyToolDir, (Join-Path $LegacyRoot "donate.txt"), (Join-Path $LegacyRoot "README.txt") -DestinationPath $LegacyZip -CompressionLevel Optimal
            Write-Host "  -> Legacy flash-tool package: $LegacyZip" -ForegroundColor Green
            Write-Host "     Folder with the flash tool only: $LegacyToolDir" -ForegroundColor Green
        }
    }
} finally {
    Set-BuildBrand "updater_ce"
    $env:BUILD_BRAND = ""
    $env:TARGET_ARCH = ""
    $env:BUILD_ARCH = ""
}

Write-Host ""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "  WINDOWS BUILD COMPLETE - Artifacts generated in $DistDir" -ForegroundColor Green
Write-Host "================================================================================" -ForegroundColor Cyan
Get-ChildItem -Path $DistDir | Select-Object Name, Mode, @{Name="Size (MB)";Expression={[math]::Round($_.Length / 1MB, 2)}}, LastWriteTime | Format-Table -AutoSize
exit 0
