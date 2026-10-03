# One repeatable path from source to a verified executable.
#
#   .\packaging\release.ps1                 build the current version
#   .\packaging\release.ps1 -Bump patch     bump first, then build
#   .\packaging\release.ps1 -SkipVerify     build without the frozen verification
#
# Order matters: tests gate the build, and the build gates verification. A failure
# at any stage stops the release rather than leaving a half-checked executable
# sitting in the project root.
param(
    [ValidateSet("major", "minor", "patch")][string]$Bump = "",
    [switch]$SkipTests,
    [switch]$SkipVerify
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
Push-Location -LiteralPath $projectRoot
try {
    if ($Bump) {
        & $pythonExe scripts/bump_version.py $Bump
        if ($LASTEXITCODE -ne 0) { throw "Version bump failed." }
    }
    $version = (& $pythonExe packaging/project_version.py).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not read the project version." }
    $outputName = "OpenDP3-$version.exe"
    Write-Output "=== Releasing $outputName ==="

    if (-not $SkipTests) {
        Write-Output "--- Tests"
        & $pythonExe -m pytest tests -q -n auto
        if ($LASTEXITCODE -ne 0) { throw "Tests failed; not building." }
    }

    Write-Output "--- Build"
    & (Join-Path $PSScriptRoot "build.ps1") -OutputName $outputName
    if ($LASTEXITCODE -ne 0) { throw "Build failed." }

    if (-not $SkipVerify) {
        Write-Output "--- Verify"
        & $pythonExe packaging/verify.py --executable $outputName
        if ($LASTEXITCODE -ne 0) { throw "Frozen verification failed." }
    }

    Write-Output ""
    Write-Output "Released $outputName"
    Get-Content -LiteralPath (Join-Path $projectRoot "$outputName.sha256")
    Write-Output "Record the result in docs/VALIDATION.md before distributing."
} finally {
    Pop-Location
}
