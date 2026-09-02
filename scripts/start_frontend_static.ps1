param(
  [string]$HostAddress = "0.0.0.0",
  [int]$Port = 5173
)

& (Join-Path $PSScriptRoot "start_frontend.ps1") -HostAddress $HostAddress -Port $Port
