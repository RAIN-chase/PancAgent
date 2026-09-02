param(
  [string]$HostAddress = "0.0.0.0",
  [int]$Port = 2024,
  [string]$OutputDir = "runs/protocol_analysis_agent_simplified_autofeatures",
  [string]$CondaEnvPath = "D:\anaconda3\envs\protocol_agent"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = "backend"
$env:PROTOCOL_ANALYSIS_OUTPUT_DIR = $OutputDir
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:LANG = "en_US.UTF-8"
$env:LC_ALL = "en_US.UTF-8"

$langGraphExe = Join-Path $CondaEnvPath "Scripts\langgraph.exe"
if (-not (Test-Path -LiteralPath $langGraphExe)) {
  throw "Cannot find langgraph.exe at $langGraphExe. Install LangGraph CLI in protocol_agent or pass -CondaEnvPath."
}

& $langGraphExe dev --host $HostAddress --port $Port
