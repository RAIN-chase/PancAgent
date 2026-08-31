param(
  [int]$Port = 5173
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path (Split-Path -Parent $PSScriptRoot) "frontend")

if (-not (Test-Path -LiteralPath ".env.local")) {
  Copy-Item -LiteralPath ".env.local.example" -Destination ".env.local"
}

pnpm dev --port $Port --hostname 127.0.0.1
