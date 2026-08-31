param(
  [int]$Port = 5173
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path (Split-Path -Parent $PSScriptRoot) "frontend")

conda run -n protocol_agent python -m http.server $Port --bind 127.0.0.1
