# Starts the target analyser. Runs setup.ps1 first if the venv does not exist yet.
# Usage:  .\run.ps1                       (database: targets.db next to this script)
#         .\run.ps1 C:\path\to\other.db   (use another database file)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & (Join-Path $PSScriptRoot "setup.ps1")
}
& (Join-Path $PSScriptRoot ".venv\Scripts\python.exe") -m target_analyzer @args
