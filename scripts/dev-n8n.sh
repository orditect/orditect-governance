#!/usr/bin/env bash
# Dev n8n launcher with the ordigovernance community nodes synced.
# Pairs with scripts/dev-gateway.sh (gateway on :8180, n8n on :5678).
# Usage: scripts/dev-n8n.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODES_DIR="$REPO_ROOT/n8n-nodes-ordigovernance"
CUSTOM_DIR="${N8N_CUSTOM_DIR:-$HOME/.n8n/custom}"
PKG_DIR="$CUSTOM_DIR/node_modules/n8n-nodes-ordigovernance"

# 1. Build the node package (tsc + icons).
cd "$NODES_DIR"
npm run build

# 2. Sync a PHYSICAL copy into n8n's custom extensions dir.
#    `npm install <local path>` creates a symlink; a physical copy is
#    loaded by every n8n version and survives editor-side rebuilds.
rm -rf "$PKG_DIR"
mkdir -p "$PKG_DIR"
cp -r package.json dist "$PKG_DIR/"

# 3. Stop any stale n8n process so the fresh copy is what gets loaded.
pkill -f "n8n start" 2>/dev/null || true
sleep 1

# 4. Resolve the n8n binary: PATH first, then the nvm installs, then
#    the npm global root. Non-interactive shells skip nvm init, so a
#    bare `exec n8n` fails here even when n8n is installed.
N8N_BIN="$(command -v n8n || true)"
if [ -z "$N8N_BIN" ]; then
  for candidate in "$HOME"/.nvm/versions/node/*/bin/n8n; do
    if [ -x "$candidate" ]; then
      N8N_BIN="$candidate"
    fi
  done
fi
if [ -z "$N8N_BIN" ]; then
  NPM_GLOBAL_ROOT="$(npm root -g 2>/dev/null || true)"
  if [ -n "$NPM_GLOBAL_ROOT" ] && [ -x "$NPM_GLOBAL_ROOT/n8n/bin/n8n" ]; then
    N8N_BIN="$NPM_GLOBAL_ROOT/n8n/bin/n8n"
  fi
fi
if [ -z "$N8N_BIN" ]; then
  echo "ERROR: n8n binary not found (PATH, ~/.nvm and npm global root scanned)." >&2
  exit 1
fi
echo "using n8n: $N8N_BIN"

# 5. Start n8n in the foreground.
#    N8N_COMMUNITY_PACKAGES_ENABLED=false hides the community-package
#    manager UI, including the "Install this node" banner on imported
#    workflows: the package is not on npm, so the manager can never
#    resolve it. Loading from ~/.n8n/custom is a separate mechanism
#    and is unaffected. Set it to "true" only if you rely on OTHER
#    community packages installed through the n8n UI.
export N8N_COMMUNITY_PACKAGES_ENABLED="${N8N_COMMUNITY_PACKAGES_ENABLED:-false}"
exec "$N8N_BIN" start