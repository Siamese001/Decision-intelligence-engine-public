[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$PythonExe = "py",
    [string]$VenvPath = "",
    [switch]$IncludeDev
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $VenvPath) {
    $VenvPath = Join-Path $repoRoot ".venv"
}
$resolvedVenv = [System.IO.Path]::GetFullPath($VenvPath)
$lockName = if ($IncludeDev) { "dev.lock.txt" } else { "runtime-cpu.lock.txt" }
$lockPath = Join-Path $repoRoot "requirements\$lockName"

if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
    throw "Required lock file is missing: $lockPath"
}

if (-not $PSCmdlet.ShouldProcess($resolvedVenv, "Create CPython 3.12 virtual environment")) {
    return
}
if ($PythonExe -eq "py") {
    & py -3.12 -m venv $resolvedVenv
} else {
    $resolvedPython = [System.IO.Path]::GetFullPath($PythonExe)
    & $resolvedPython -m venv $resolvedVenv
}
if ($LASTEXITCODE -ne 0) { throw "Virtual-environment creation failed" }

$venvPython = Join-Path $resolvedVenv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Virtual-environment interpreter is missing: $venvPython"
}

if (-not $PSCmdlet.ShouldProcess($resolvedVenv, "Install hash-locked dependencies from $lockName")) {
    return
}
& $venvPython -m pip install --require-hashes --only-binary=:all: -r $lockPath
if ($LASTEXITCODE -ne 0) { throw "Locked dependency installation failed" }
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "pip dependency check failed" }
& $venvPython (Join-Path $repoRoot "tools\verify_runtime_dependencies.py")
if ($LASTEXITCODE -ne 0) { throw "Runtime dependency verification failed" }
