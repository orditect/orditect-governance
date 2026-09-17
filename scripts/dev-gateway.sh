#!/usr/bin/env bash
# Dev gateway launcher with the n8n demo registry loaded.
# Usage: scripts/dev-gateway.sh [port]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export GATEWAY_REGISTRY_MODULE="examples.gateway_n8n.registry:build_registry"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

PORT="${1:-8180}"
exec uvicorn ordigovernance.gateway.app:build_app --factory \
  --host 127.0.0.1 --port "$PORT"
