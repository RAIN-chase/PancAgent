param(
  [int]$Port = 5173
)

& (Join-Path $PSScriptRoot "start_frontend.ps1") -Port $Port
