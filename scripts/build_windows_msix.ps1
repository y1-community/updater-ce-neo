# Builds the Microsoft Store (MSIX) package from a finished PyInstaller folder.
#
#   .\scripts\build_windows_msix.ps1 -AppDir dist\windows\InnioasisUpdater -Version 2.5.0
#
# Produces dist\windows\UpdaterCE-<version>.msix, unsigned unless -SignCert is
# passed (a local test certificate; the Store signs the uploaded package).
#
# Identity values come from scripts\msix\msix_identity.json, copied from
# msix_identity.example.json and filled in from Partner Center -> App identity.
# Without that file the script still packs, using placeholder identity, and
# says so: the result is only useful for a local install test.

param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir,
    [string]$Version = "",
    [string]$OutDir = "",
    [string]$IdentityFile = (Join-Path $PSScriptRoot "msix\msix_identity.json"),
    [string]$SignCert = "",
    [string]$SignPassword = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ManifestTemplate = Join-Path $PSScriptRoot "msix\AppxManifest.xml"
$ExampleIdentity = Join-Path $PSScriptRoot "msix\msix_identity.example.json"

if (-not (Test-Path $AppDir)) {
    throw "App directory not found: $AppDir"
}

if (-not $Version) {
    $ConfigFile = Join-Path $ProjectRoot "src\config.py"
    $match = Select-String -Path $ConfigFile -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($match -and $match.Matches.Count -gt 0) {
        $Version = $match.Matches[0].Groups[1].Value
    } else {
        $Version = "0.0.0"
    }
}
# MSIX wants a four-part version: 2.5.0 -> 2.5.0.0
$parts = $Version.Split(".")
while ($parts.Count -lt 4) { $parts += "0" }
$MsixVersion = ($parts[0..3] -join ".")

if (-not $OutDir) {
    $OutDir = Split-Path -Parent $AppDir
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Find-MakeAppx {
    $cmd = Get-Command makeappx.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $roots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "$env:ProgramFiles\Windows Kits\10\bin"
    )
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        $candidate = Get-ChildItem -Path $root -Filter makeappx.exe -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\' } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }
    return $null
}

$MakeAppx = Find-MakeAppx
if (-not $MakeAppx) {
    Write-Error "MakeAppx.exe was not found. Install the Windows 10/11 SDK (the 'MSIX Packaging Tools' component ships makeappx.exe) and run again."
    exit 1
}
Write-Host "  Using MakeAppx: $MakeAppx" -ForegroundColor DarkGray

# --- identity ---------------------------------------------------------------
if (Test-Path $IdentityFile) {
    $identity = Get-Content -Raw -Path $IdentityFile | ConvertFrom-Json
    Write-Host "  Identity from $IdentityFile" -ForegroundColor Green
} else {
    Write-Warning "No identity file at $IdentityFile. Packing with the placeholder values from $ExampleIdentity; the Store will reject this package until the real Partner Center identity is used."
    $identity = Get-Content -Raw -Path $ExampleIdentity | ConvertFrom-Json
}

function Get-IdentityValue($obj, $name, $fallback) {
    $value = $obj.$name
    if ([string]::IsNullOrWhiteSpace($value)) { return $fallback }
    return [string]$value
}

$tokens = @{
    "@@IDENTITY_NAME@@"         = Get-IdentityValue $identity "identityName" "Innioasis.UpdaterCE"
    "@@PUBLISHER@@"             = Get-IdentityValue $identity "publisher" "CN=00000000-0000-0000-0000-000000000000"
    "@@PUBLISHER_DISPLAY_NAME@@" = Get-IdentityValue $identity "publisherDisplayName" "Innioasis"
    "@@DISPLAY_NAME@@"          = Get-IdentityValue $identity "displayName" "Updater CE"
    "@@SHORT_NAME@@"            = Get-IdentityValue $identity "shortName" "Updater CE"
    "@@DESCRIPTION@@"           = Get-IdentityValue $identity "description" "Innioasis firmware updater."
    "@@APPLICATION_ID@@"        = Get-IdentityValue $identity "applicationId" "UpdaterCE"
    "@@EXECUTABLE@@"            = Get-IdentityValue $identity "executable" "InnioasisUpdater.exe"
    "@@ARCH@@"                  = Get-IdentityValue $identity "architecture" "x64"
    "@@VERSION@@"               = $MsixVersion
}

$exePath = Join-Path $AppDir $tokens["@@EXECUTABLE@@"]
if (-not (Test-Path $exePath)) {
    throw "Executable named in the identity file is not in the app folder: $exePath"
}

# --- stage ----------------------------------------------------------------
$stageRoot = Join-Path $env:TEMP ("updater_msix_stage_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $stageRoot | Out-Null
try {
    Write-Host "  Staging $AppDir ..." -ForegroundColor DarkGray
    # /MIR copies the whole PyInstaller tree, including SP Flash Tool and the
    # Qt plugins it needs.
    & robocopy $AppDir $stageRoot /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NC /NS | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy could not stage the app folder (exit $LASTEXITCODE)"
    }

    # MSIX image assets, rendered from the app icon. These names are what
    # AppxManifest.xml points at.
    $assetsDir = Join-Path $stageRoot "Assets"
    New-Item -ItemType Directory -Force -Path $assetsDir | Out-Null
    $iconSource = Join-Path $ProjectRoot "assets\icon.png"
    if (-not (Test-Path $iconSource)) {
        throw "Icon source not found: $iconSource"
    }
    Add-Type -AssemblyName System.Drawing
    $logoSizes = @{
        "StoreLogo.png"         = @(50, 50)
        "Square44x44Logo.png"   = @(44, 44)
        "Square150x150Logo.png" = @(150, 150)
        "Square310x310Logo.png" = @(310, 310)
        "Wide310x150Logo.png"   = @(310, 150)
    }
    $source = [System.Drawing.Image]::FromFile($iconSource)
    try {
        foreach ($name in $logoSizes.Keys) {
            $w = $logoSizes[$name][0]
            $h = $logoSizes[$name][1]
            $bmp = New-Object System.Drawing.Bitmap $w, $h
            $graphics = [System.Drawing.Graphics]::FromImage($bmp)
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.Clear([System.Drawing.Color]::Transparent)
            # Keep the art square and centred inside a wide tile.
            $side = [Math]::Min($w, $h)
            $x = [int](($w - $side) / 2)
            $y = [int](($h - $side) / 2)
            $graphics.DrawImage($source, $x, $y, $side, $side)
            $graphics.Dispose()
            $target = Join-Path $assetsDir $name
            $bmp.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
            $bmp.Dispose()
        }
    } finally {
        $source.Dispose()
    }

    # Manifest with the identity substituted in.
    $manifest = Get-Content -Raw -Path $ManifestTemplate
    foreach ($token in $tokens.Keys) {
        $manifest = $manifest.Replace($token, $tokens[$token])
    }
    $manifestPath = Join-Path $stageRoot "AppxManifest.xml"
    Set-Content -Path $manifestPath -Value $manifest -Encoding UTF8

    # Name the package after the app it contains, so building both front ends
    # leaves two uploadable files rather than one overwritten name.
    $packageBase = [System.IO.Path]::GetFileNameWithoutExtension($tokens["@@EXECUTABLE@@"])
    $packageName = "$packageBase-$MsixVersion.msix"
    $packagePath = Join-Path $OutDir $packageName
    if (Test-Path $packagePath) { Remove-Item -Force $packagePath }

    Write-Host "  Packing $packageName ..." -ForegroundColor DarkGray
    & $MakeAppx pack /p $packagePath /d $stageRoot /o
    if ($LASTEXITCODE -ne 0) {
        throw "MakeAppx failed (exit $LASTEXITCODE)"
    }

    if ($SignCert) {
        $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if (-not $signtool) {
            $signtool = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match '\\x64\\' } |
                Sort-Object FullName -Descending |
                Select-Object -First 1
        }
        if (-not $signtool) {
            throw "signtool.exe was not found; install the Windows SDK or omit -SignCert."
        }
        $signtoolPath = if ($signtool -is [string]) { $signtool } else { $signtool.FullName }
        $signArgs = @("sign", "/fd", "SHA256", "/f", $SignCert)
        if ($SignPassword) { $signArgs += @("/p", $SignPassword) }
        $signArgs += $packagePath
        & $signtoolPath @signArgs
        if ($LASTEXITCODE -ne 0) {
            throw "signtool failed (exit $LASTEXITCODE)"
        }
        Write-Host "  Signed with $SignCert" -ForegroundColor Green
    } else {
        Write-Host "  Package left unsigned: the Store signs the uploaded MSIX. For a local install test, sign it with a test certificate (-SignCert)." -ForegroundColor DarkGray
    }

    $sizeMb = [math]::Round((Get-Item $packagePath).Length / 1MB, 2)
    Write-Host "  -> Microsoft Store package created: $packagePath ($sizeMb MB)" -ForegroundColor Green
    exit 0
} finally {
    Remove-Item -Recurse -Force $stageRoot -ErrorAction SilentlyContinue
}
