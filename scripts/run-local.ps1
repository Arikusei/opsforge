$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
pip install -r apps\api\requirements.txt
$env:PYTHONPATH = "apps\api"
$env:KNOWLEDGE_DIR = (Resolve-Path .\knowledge).Path
New-Item -ItemType Directory -Force -Path data | Out-Null
Write-Host "Starting OpsForge on http://127.0.0.1:8000"
uvicorn app.main:app --app-dir apps\api --reload --port 8000
