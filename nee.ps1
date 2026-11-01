$ErrorActionPreference = "Stop"
& python (Join-Path $PSScriptRoot "nee.py") @args
exit $LASTEXITCODE

