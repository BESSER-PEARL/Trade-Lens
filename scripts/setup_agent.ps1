# SPDX-License-Identifier: MIT
# One-time setup of the agent (app/agents/lux_insights) on Windows: its own venv, .venv-agent, with
# BAF + OpenAI. BAF pins versions the app does not use (websockets 11, numpy 1.26, ...), so it does not share .venv.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue  # never pick up another BESSER checkout

if (-not (Test-Path ".venv-agent")) { python -m venv .venv-agent }
$py = ".venv-agent\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements-agent.txt
& $py -c "import baf.core.agent, baf.library.state.reasoning_state_library, baf.nlp.llm.llm_openai_api, baf.platforms.websocket"
if ($LASTEXITCODE -eq 0) { Write-Host "BAF imports fine." }

$hasKey = (Test-Path ".env") -and (Select-String -Path ".env" -Pattern '^OPENAI_API_KEY=.+' -Quiet)
if (-not $hasKey) {
    if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
    Write-Host "Put your OpenAI key in .env (OPENAI_API_KEY=...); it is gitignored."
}
Write-Host "Setup done. Run: python run.py   (the agent starts on ws://localhost:8766 when a key is set)"
