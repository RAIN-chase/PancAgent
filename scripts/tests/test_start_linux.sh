#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT
mkdir -p "$TMP/bin"

# Stub entrypoints so this check never starts a server or performs analysis.
cat > "$TMP/bin/langgraph" <<'STUB'
#!/usr/bin/env bash
printf 'cwd=<%s>\n' "$PWD"
printf 'arg=<%s>\n' "$@"
STUB
cp "$TMP/bin/langgraph" "$TMP/bin/python"
cp "$TMP/bin/langgraph" "$TMP/bin/pnpm"
cat > "$TMP/bin/dotenv" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == -f && "$3" == run && "$4" == --no-override && "$5" == -- ]]
shift 5
export PROTOCOL_ACCESS_MODE=authenticated
exec "$@"
STUB
chmod +x "$TMP/bin/"*
export PATH="$TMP/bin:$PATH"
export PROTOCOL_ENV_FILE="$TMP/missing.env"
unset PROTOCOL_ACCESS_MODE PROTOCOL_ENV_LOADED LANGGRAPH_CONFIG HOST_ADDRESS PORT
cd -- "$TMP"

check_contains() {
  if [[ "$1" != *"$2"* ]]; then
    printf 'Missing expected output: %s\nActual output:\n%s\n' "$2" "$1" >&2
    exit 1
  fi
}

output="$(bash "$ROOT/scripts/start_linux.sh" chat)"
check_contains "$output" "cwd=<$ROOT>"
check_contains "$output" 'arg=<langgraph.json>'
check_contains "$output" 'arg=<127.0.0.1>'
check_contains "$output" 'arg=<2024>'
check_contains "$output" 'arg=<--no-reload>'
check_contains "$output" 'arg=<--no-browser>'

output="$(PROTOCOL_ACCESS_MODE=authenticated LANGGRAPH_CONFIG=langgraph.json PORT=2124 bash "$ROOT/scripts/start_linux.sh" chat)"
check_contains "$output" 'arg=<langgraph.authenticated.json>'
check_contains "$output" 'arg=<2124>'
check_contains "$output" 'arg=<--no-reload>'

printf '%s\n' 'TEST_ONLY=1' > "$TMP/test.env"
output="$(PROTOCOL_ENV_FILE="$TMP/test.env" bash "$ROOT/scripts/start_linux.sh" chat)"
check_contains "$output" 'arg=<langgraph.authenticated.json>'

output="$(bash "$ROOT/scripts/start_linux.sh" batch --config 'configs/server config.local.json')"
check_contains "$output" 'arg=<protocol_optimization_agent.interfaces.cli>'
check_contains "$output" 'arg=<configs/server config.local.json>'

output="$(bash "$ROOT/scripts/start_linux.sh" api)"
check_contains "$output" 'arg=<--factory>'
check_contains "$output" 'arg=<8000>'

for mode in frontend-dev frontend-build frontend-start; do
  output="$(bash "$ROOT/scripts/start_linux.sh" "$mode")"
  check_contains "$output" "cwd=<$ROOT/frontend>"
done

if bash "$ROOT/scripts/start_linux.sh" invalid >/dev/null 2>&1; then
  printf '%s\n' 'Invalid mode unexpectedly succeeded' >&2; exit 1
fi
if PROTOCOL_ACCESS_MODE=authenticated bash "$ROOT/scripts/start_linux.sh" chat --config langgraph.json >/dev/null 2>&1; then
  printf '%s\n' 'Chat config override unexpectedly succeeded' >&2; exit 1
fi
printf '%s\n' 'Linux launcher checks passed (stubbed entrypoints).'
