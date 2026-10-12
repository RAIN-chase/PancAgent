#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$ROOT"

usage() {
  printf '%s\n' 'Usage: bash scripts/start_linux.sh {chat|api|batch|frontend-dev|frontend-build|frontend-start} [arguments...]'
}

MODE="${1:-help}"
case "$MODE" in
  help|-h|--help) usage; exit 0 ;;
  chat|api|batch|frontend-dev|frontend-build|frontend-start) shift ;;
  *) usage >&2; exit 2 ;;
esac

# Parse dotenv as data, not shell code; explicitly exported variables take precedence.
ENV_FILE="${PROTOCOL_ENV_FILE:-$ROOT/.env}"
if [[ "$MODE" == chat || "$MODE" == api || "$MODE" == batch ]]; then
  if [[ -f "$ENV_FILE" && "${PROTOCOL_ENV_LOADED:-0}" != 1 ]]; then
    if ! command -v dotenv >/dev/null 2>&1; then
      printf '%s\n' 'Install server dependencies: python -m pip install -e ".[h5ad,server]"' >&2
      exit 1
    fi
    exec dotenv -f "$ENV_FILE" run --no-override -- env PROTOCOL_ENV_LOADED=1 bash "$ROOT/scripts/start_linux.sh" "$MODE" "$@"
  fi
fi

export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
HOST="${HOST_ADDRESS:-127.0.0.1}"

case "$MODE" in
  chat)
    if (( $# > 0 )); then
      printf '%s\n' 'Chat accepts no extra CLI arguments; use HOST_ADDRESS, PORT or LANGGRAPH_CONFIG.' >&2
      exit 2
    fi
    CONFIG="${LANGGRAPH_CONFIG:-langgraph.json}"
    if [[ "${PROTOCOL_ACCESS_MODE:-local}" == authenticated ]]; then
      CONFIG=langgraph.authenticated.json
    fi
    # Archives contain copied Python source; watching them causes reload/cancel loops.
    exec langgraph dev --no-reload --no-browser --config "$CONFIG" --host "$HOST" --port "${PORT:-2024}"
    ;;
  api)
    exec python -m uvicorn protocol_optimization_agent.interfaces.api:create_app --factory --host "$HOST" --port "${PORT:-8000}" "$@"
    ;;
  batch)
    exec python -m protocol_optimization_agent.interfaces.cli "$@"
    ;;
  frontend-*)
    cd -- "$ROOT/frontend"
    case "$MODE" in
      frontend-dev) exec pnpm dev --hostname "$HOST" --port "${PORT:-3000}" "$@" ;;
      frontend-build) exec pnpm build "$@" ;;
      frontend-start) exec pnpm start --hostname "$HOST" --port "${PORT:-3000}" "$@" ;;
    esac
    ;;
esac
