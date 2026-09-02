param(
  [string]$HostAddress = "0.0.0.0",
  [int]$Port = 8000,
  [string]$CondaEnvPath = "D:\anaconda3\envs\protocol_agent"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = "backend"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$pythonExe = Join-Path $CondaEnvPath "python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
  throw "Cannot find python.exe at $pythonExe. Pass -CondaEnvPath for protocol_agent."
}

& $pythonExe -m uvicorn protocol_optimization_agent.api:create_app --factory --host $HostAddress --port $Port
