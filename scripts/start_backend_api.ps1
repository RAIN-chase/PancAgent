param(
  [string]$HostAddress = "127.0.0.1",
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = "backend"

conda run -n protocol_agent python -m uvicorn protocol_optimization_agent.api:create_app --factory --host $HostAddress --port $Port
