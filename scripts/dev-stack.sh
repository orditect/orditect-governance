#!/usr/bin/env bash
# Full dev stack launcher: gateway (:8180) + viewer (:8181) + n8n (:5678),
# each in its own terminal window (foreground processes, independent
# Ctrl+C, no log mixing, no orphan pid management).
#
# Why not one script running three background processes: dev-gateway.sh
# is deliberately foreground/blocking; backgrounding it (nohup + &)
# breaks its set -e semantics, mixes logs and orphans processes on
# laptop-close. The write path (gateway) and the read path (viewer)
# stay separate processes by design (D14) -- this launcher only
# removes the "remember to open three terminals" burden.
#
# Usage: scripts/dev-stack.sh
# Requires: a terminal emulator (tmux fallback; see TERM_CMD below).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODES_REPO="${N8N_NODES_REPO:-$REPO_ROOT/../n8n-nodes-ordigovernance}"

# ---- terminal command ------------------------------------------------
# Prefer a windowed terminal; fall back to tmux panes. Extend TERM_CMD
# for other emulators (alacritty, kitty, ...).
if command -v gnome-terminal >/dev/null 2>&1; then
  open_term() { gnome-terminal -- bash -lc "$1"; }
elif command -v konsole >/dev/null 2>&1; then
  open_term() { konsole -e bash -lc "$1"; }
elif command -v tmux >/dev/null 2>&1; then
  SESSION="ordigovernance-dev"
  tmux has-session -t "$SESSION" 2>/dev/null \
    && { echo "tmux session '$SESSION' exists; attach: tmux attach -t $SESSION"; exit 0; }
  open_term() {
    # First call creates the session with the command; subsequent ones
    # open new windows in it.
    if ! tmux has-session -t "$SESSION" 2>/dev/null; then
      tmux new-session -d -s "$SESSION" -n "$(echo "$1" | head -c12)" "$1"
    else
      tmux new-window -t "$SESSION" -n "$(echo "$1" | head -c12)" "$1"
    fi
    tmux attach-session -t "$SESSION" 2>/dev/null || true
  }
else
  echo "ERROR: no supported terminal emulator (gnome-terminal/konsole) or tmux found." >&2
  echo "Start the three services manually (see scripts/dev-gateway.sh and" >&2
  echo "examples/gateway_n8n/viewer_app.py, then scripts/dev-n8n.sh)." >&2
  exit 1
fi

# ---- 1. gateway (write path, :8180) ----------------------------------
echo "==> starting gateway (:8180) in a new terminal"
open_term "cd '$REPO_ROOT' && scripts/dev-gateway.sh"

# ---- 2. viewer (read path, :8181) ------------------------------------
# Quiet by design (warning log level): no output in its terminal means
# it is running. GATEWAY_TRACE_ROOT must match the gateway's (both
# default to data/gateway-runs when launched from this repo).
echo "==> starting viewer (:8181) in a new terminal"
open_term "cd '$REPO_ROOT' && \
  GATEWAY_TRACE_ROOT=data/gateway-runs \
  VIEWER_PORT=8181 \
  python -m examples.gateway_n8n.viewer_app"

# ---- 3. n8n (:5678) ---------------------------------------------------
if [[ -d "$NODES_REPO" && -f "$NODES_REPO/scripts/dev-n8n.sh" ]]; then
  echo "==> starting n8n (:5678) in a new terminal (node repo: $NODES_REPO)"
  open_term "cd '$NODES_REPO' && scripts/dev-n8n.sh"
else
  echo "!! n8n node repo not found at $NODES_REPO (set N8N_NODES_REPO to override)" >&2
  echo "   starting gateway + viewer only; run scripts/dev-n8n.sh manually" >&2
fi

# ---- readiness probe (gateway + viewer) ------------------------------
echo
echo "==> probing readiness (gateway :8180, viewer :8181) ..."
GW_TOKEN="${GATEWAY_AUTH_TOKEN:-dev-token}"
for i in $(seq 1 30); do
  GW_OK=$(curl -s -o /dev/null -w '%{http_code}' \
    "http://localhost:8180/healthz" 2>/dev/null || true)
  VW_OK=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $GW_TOKEN" \
    "http://localhost:8181/api/runs" 2>/dev/null || true)
  if [[ "$GW_OK" == "200" && "$VW_OK" == "200" ]]; then
    echo "PASS  gateway :8180 up, viewer :8181 up"
    echo
    echo "stack ready: n8n http://localhost:5678  gateway :8180  viewer :8181"
    exit 0
  fi
  sleep 1
done
echo "WARN  not all services answered within 30s (gateway=$GW_OK viewer=$VW_OK)." >&2
echo "      Check the terminals above; the stack may still be starting." >&2
exit 0