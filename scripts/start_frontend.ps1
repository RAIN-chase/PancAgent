param(
  [string]$HostAddress = "0.0.0.0",
  [int]$Port = 5173,
  [string]$LangGraphApiUrl = "",
  [ValidateSet("orchestrator")][string]$AssistantId = "orchestrator",
  [string]$AllowedDevOrigins = "10.203.168.222,172.20.128.1"
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path (Split-Path -Parent $PSScriptRoot) "frontend")

if (-not (Test-Path -LiteralPath ".env.local")) {
  Copy-Item -LiteralPath ".env.local.example" -Destination ".env.local"
}

if (-not $LangGraphApiUrl) {
  $configuredApiUrl = Select-String -Path ".env.local" -Pattern "^NEXT_PUBLIC_API_URL=(.*)$" |
    Select-Object -First 1
  $LangGraphApiUrl = if ($configuredApiUrl) { $configuredApiUrl.Matches[0].Groups[1].Value } else { "http://localhost:2024" }
}

$lines = @(
  "NEXT_PUBLIC_API_URL=$LangGraphApiUrl",
  "NEXT_PUBLIC_ASSISTANT_ID=$AssistantId",
  "NEXT_PUBLIC_AUTH_SCHEME="
)
Set-Content -LiteralPath ".env.local" -Value $lines -Encoding UTF8

if ($AllowedDevOrigins) {
  $env:NEXT_ALLOWED_DEV_ORIGINS = $AllowedDevOrigins
}

pnpm dev --port $Port --hostname $HostAddress
