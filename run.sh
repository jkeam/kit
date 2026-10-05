#!/usr/bin/env bash
# Start the Kit gateway (web UI + WebSocket API).
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "warning: .env not found — copy .env.example to .env and configure LLM settings" >&2
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required (https://docs.astral.sh/uv/)" >&2
  exit 1
fi

echo "Starting Kit gateway on http://127.0.0.1:${GATEWAY_PORT:-18789}"
exec uv run python -m gateway.server
