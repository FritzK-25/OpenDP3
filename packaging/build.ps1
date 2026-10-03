# Builds the standalone executable. Never starts, stops, or replaces a running recorder.
#
# The output filename defaults to OpenDP3-<version>.exe, derived from the single
# version source in src/opendp3/__init__.py. Pass -OutputName to override.
param([string]$OutputName = "")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

function Test-FileLocked([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        $stream = [System.IO.File]::Open($Path, 'Open', 'ReadWrite', 'None')
        $stream.Close(); $stream.Dispose()
        return $false
    } catch { return $true }
}

if ([string]::IsNullOrWhiteSpace($OutputName)) {
    $version = (& $pythonExe (Join-Path $PSScriptRoot "project_version.py")).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not read the project version." }
    $OutputName = "OpenDP3-$version.exe"
}
if ($OutputName -notmatch '^OpenDP3(?:-[0-9]+\.[0-9]+\.[0-9]+)?\.exe$') {
    throw "OutputName must be OpenDP3.exe or a versioned OpenDP3 executable filename."
}

# Fail before the build, not after 70 seconds of work. Replacing the executable
# behind a live recording is exactly what the versioned filename exists to avoid.
$targetPath = Join-Path $projectRoot $OutputName
foreach ($candidate in @($targetPath, (Join-Path $projectRoot "dist/OpenDP3.exe"))) {
    if (Test-FileLocked $candidate) {
        throw ("$candidate is in use by a running process. Close it, or build under a " +
               "different version so the running recorder keeps its executable.")
    }
}
$qualification = Join-Path $projectRoot "data/qualification-run.json"
if ((Test-Path -LiteralPath $qualification) -and
    -not (Test-Path -LiteralPath (Join-Path $projectRoot "data/qualification.stop"))) {
    Write-Warning ("A qualification run is recorded in data/qualification-run.json. " +
                   "This build does not touch it; switch executables only after it ends.")
}

$previousBuildPath = $env:PATH
$previousMplConfig = $env:MPLCONFIGDIR
$previousPyiConfig = $env:PYINSTALLER_CONFIG_DIR
# External document/image tools can carry incompatible ICU/OpenSSL DLLs.
# Never let their PATH entries become dependency sources for this application.
$env:PATH = "$(Join-Path $projectRoot '.venv\Scripts');$env:SystemRoot\System32;$env:SystemRoot"
$env:MPLCONFIGDIR = Join-Path $projectRoot "artifacts/build-matplotlib"
$env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot "artifacts/pyinstaller-cache"
Push-Location -LiteralPath $projectRoot
try {
    & $pythonExe packaging/prepare.py
    if ($LASTEXITCODE -ne 0) { throw "Packaging preparation failed." }
    & $pythonExe -m PyInstaller --clean --noconfirm --distpath dist --workpath build packaging/OpenDP3.spec
    if ($LASTEXITCODE -ne 0) { throw "Executable build failed." }
    Copy-Item -LiteralPath (Join-Path $projectRoot "dist/OpenDP3.exe") -Destination $targetPath
    $digest = (Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash.ToLower()
    Set-Content -LiteralPath "$targetPath.sha256" -Value "$digest  $OutputName" -Encoding ascii
    Write-Output "Built standalone $OutputName. Build does not start or stop recording."
} finally {
    $env:PATH = $previousBuildPath
    $env:MPLCONFIGDIR = $previousMplConfig
    $env:PYINSTALLER_CONFIG_DIR = $previousPyiConfig
    Pop-Location
}
