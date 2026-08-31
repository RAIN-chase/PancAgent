param(
  [string]$HostAddress = "127.0.0.1",
  [int]$Port = 2024,
  [string]$OutputDir = "runs/protocol_analysis_agent_simplified_autofeatures"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = "backend"
$env:PROTOCOL_ANALYSIS_OUTPUT_DIR = $OutputDir

conda run -n protocol_agent langgraph dev --host $HostAddress --port $Port
