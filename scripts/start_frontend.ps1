param(
  [string]$HostAddress = "0.0.0.0",
  [int]$Port = 5173,
  [string]$LangGraphApiUrl = "",
  [string]$AssistantId = "orchestrator",
  [string]$AllowedDevOrigins = "10.203.168.222"
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path (Split-Path -Parent $PSScriptRoot) "frontend")

if (-not (Test-Path -LiteralPath ".env.local")) {
  Copy-Item -LiteralPath ".env.local.example" -Destination ".env.local"
}

if ($LangGraphApiUrl) {
  $lines = @(
    "NEXT_PUBLIC_API_URL=$LangGraphApiUrl",
    "NEXT_PUBLIC_ASSISTANT_ID=$AssistantId",
    "NEXT_PUBLIC_AUTH_SCHEME="
  )
  Set-Content -LiteralPath ".env.local" -Value $lines -Encoding UTF8
}

if ($AllowedDevOrigins) {
  $env:NEXT_ALLOWED_DEV_ORIGINS = $AllowedDevOrigins
}

pnpm dev --port $Port --hostname $HostAddress
