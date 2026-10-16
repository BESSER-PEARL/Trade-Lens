# SPDX-License-Identifier: MIT
# From a fresh clone to the running app in one command: setup (once), data load (once), run.
#   powershell -ExecutionPolicy Bypass -File scripts\quickstart.ps1             # skips the steps already done
#   powershell -ExecutionPolicy Bypass -File scripts\quickstart.ps1 -Reload     # re-download the data from Eurostat
#   powershell -ExecutionPolicy Bypass -File scripts\quickstart.ps1 -NoAgent    # backend and page only
#   powershell -ExecutionPolicy Bypass -File scripts\quickstart.ps1 --port 8400 --frontend-port 3400   # other flags go to run.py
param([switch]$Reload, [switch]$NoAgent, [Parameter(ValueFromRemainingArguments = $true)][string[]]$RunArgs)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue  # never pick up another BESSER checkout

$py = ".venv\Scripts\python.exe"
if (-not (Test-Path ".venv\.setup-done") -or -not (Test-Path "vendor\BESSER")) {
    Write-Host "== Setup (first run only: .venv, BESSER v8.1.0 + patches; 2-3 minutes)"
    & powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (-not (Test-Path "data\lux_trade.db") -or $Reload) {
    Write-Host "== Loading the data live from the Eurostat API (1.15 million observations; up to 3 minutes)"
    # Into a temporary file first, so that an interrupted load is not taken for a finished one.
    Remove-Item "data\lux_trade.loading.db" -ErrorAction SilentlyContinue
    & $py -m besser_sdmx load --mapping model/lux_trade_mapping.json --orm app/backend/sql_alchemy.py `
        --slices model/lux_trade_slices.json --db data/lux_trade.loading.db
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Move-Item -Force "data\lux_trade.loading.db" "data\lux_trade.db"
}
Write-Host "== Starting the backend, the frontend and the agent (Ctrl+C to stop)"
$passArgs = @()  # (PowerShell names are case-insensitive: not $runArgs)
if ($NoAgent) { $passArgs += "--no-agent" }
if ($RunArgs) { $passArgs += $RunArgs }
& $py run.py @passArgs
