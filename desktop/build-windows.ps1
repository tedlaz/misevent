param([switch]$SkipInstaller)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
Set-Location (Split-Path $PSScriptRoot)  # ρίζα του repo

$Version = (uv version --short).Trim()
Write-Host "Building misevent $Version"

uv sync --dev
uv run pyinstaller --noconfirm --clean desktop/misevent.spec
# Smoke test σε προσωρινό φάκελο — όχι στα πραγματικά δεδομένα του %LOCALAPPDATA%
$env:MISEVENT_DATA_DIR = Join-Path ([IO.Path]::GetTempPath()) "misevent-smoke-$PID"
& "dist\misevent\misevent.exe" --smoke-test | Out-Null
Remove-Item Env:MISEVENT_DATA_DIR
if ($LASTEXITCODE -ne 0) { throw "Smoke test failed" }

if ($SkipInstaller) {
    Write-Host "Portable build: dist\misevent"
    exit 0
}

$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw "Inno Setup 6 not found. Install it or run with -SkipInstaller." }

& $iscc "/DMyAppVersion=$Version" "desktop\installer.iss"
Write-Host "Installer: installer-output\misevent-Setup-$Version.exe"
