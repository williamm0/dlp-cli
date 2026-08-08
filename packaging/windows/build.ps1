$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location $Root

if ($env:PYTHON) {
    $Python = $env:PYTHON
} elseif (Test-Path (Join-Path $Root ".venv/Scripts/python.exe")) {
    $Python = Join-Path $Root ".venv/Scripts/python.exe"
} else {
    $Python = (Get-Command python -ErrorAction Stop).Source
}

if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
    throw "This build targets Windows x64. Run it on an AMD64 Python environment."
}

$ExistingPythonPath = if ($env:PYTHONPATH) { $env:PYTHONPATH } else { "" }
$env:PYTHONPATH = (Join-Path $Root "src") + ";" + $ExistingPythonPath
$Version = & $Python -c "from dlp import __version__; print(__version__)"

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root "build/dlp")
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root "build/licenses")
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root "dist/dlp")
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $Root "dist/dlp-windows-x64-setup.exe")

& $Python packaging/collect_licenses.py (Join-Path $Root "build/licenses")

& $Python -m PyInstaller `
    --clean `
    --noconfirm `
    --onedir `
    --console `
    --name dlp `
    --paths src `
    --add-data "src/dlp/ui/styles.tcss;dlp/ui" `
    --add-data "THIRD_PARTY_NOTICES.md;." `
    --add-data "build/licenses;licenses" `
    --collect-all textual `
    --collect-all yt_dlp `
    --collect-all yt_dlp_ejs `
    packaging/entrypoint.py

if (Get-Command iscc -ErrorAction SilentlyContinue) {
    & iscc "/DAppVersion=$Version" "packaging/windows/dlp.iss"
    Write-Output "Created dist/dlp-windows-x64-setup.exe"
} else {
    Write-Warning "Inno Setup (iscc) is not installed. The PyInstaller folder is in dist/dlp."
}
