# SPDX-License-Identifier: MIT
# One-time setup on Windows: Python venv, BESSER v8.1.0 + our patches (vendor\), project dependencies.
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue  # never pick up another BESSER checkout

# Windows PowerShell 5.1 does not stop when a program (git, pip) fails: check every exit code.
function Invoke-Step([string]$What, [scriptblock]$Cmd) {
    Write-Host "-- $What"
    & $Cmd
    if ($LASTEXITCODE -ne 0) { throw "Setup failed while trying to $What (exit code $LASTEXITCODE). See README, 'Troubleshooting'." }
}

# The first Python 3.11+ that runs ($env:PYTHON, python, py). "python" can be the Microsoft Store stub.
$python = $null
foreach ($c in @($env:PYTHON, "python", "py") | Where-Object { $_ }) {
    try {
        & $c -c "import sys; sys.exit(sys.version_info < (3, 11))" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $python = $c; break }
    } catch { }
}
if (-not $python) { throw "No Python 3.11+ found (tried python, py). Install one from python.org, or set `$env:PYTHON." }

if (-not (Test-Path ".venv\Scripts\python.exe")) { Invoke-Step "create .venv" { & $python -m venv .venv } }
$py = ".venv\Scripts\python.exe"
Remove-Item ".venv\.setup-done" -ErrorAction SilentlyContinue  # written last: quickstart re-runs a setup that did not finish

if (-not (Test-Path "vendor\BESSER")) {
    Invoke-Step "clone BESSER v8.1.0" { git clone --quiet --depth 1 --branch v8.1.0 https://github.com/BESSER-PEARL/BESSER.git vendor/BESSER }
}
# Always start from clean v8.1.0 and apply the current patches (re-running setup picks up patch updates).
Invoke-Step "reset vendor\BESSER" { git -C vendor/BESSER checkout -q -- . }
Invoke-Step "clean vendor\BESSER" { git -C vendor/BESSER clean -qfd }
Get-ChildItem patches/besser/*.patch | Sort-Object Name | ForEach-Object {
    $name = $_.Name
    Invoke-Step "apply $name" { git -C vendor/BESSER apply "../../patches/besser/$name" }
}
Invoke-Step "upgrade pip" { & $py -m pip install --quiet --upgrade pip }
Invoke-Step "install BESSER" { & $py -m pip install --quiet -e vendor/BESSER }
Invoke-Step "install requirements.txt" { & $py -m pip install --quiet -r requirements.txt }
New-Item -ItemType File -Force ".venv\.setup-done" | Out-Null
Write-Host "Setup done. Activate with: .venv\Scripts\activate"
