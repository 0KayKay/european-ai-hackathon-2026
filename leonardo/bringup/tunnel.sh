#!/usr/bin/env bash
# Run on YOUR machine (laptop, VM, or later a pod). Opens one SSH connection to
# a Leonardo login node, reads the endpoint file a job wrote, and forwards
# LISTEN_ADDR:LOCAL_PORT to <compute node>:<port>. When the job is replaced
# (new node/port), the forward follows it. You authenticate once.
#
# Usage: LEO_USER=YOUR_LOGIN TEAM=your_team_name ./tunnel.sh [endpoint-name]
#        endpoint-name: probe, or the name printed by a vLLM job / ENDPOINT_NAME
set -uo pipefail
LEO_USER="${LEO_USER:?set LEO_USER to your Leonardo username}"
TEAM="${TEAM:?set TEAM to your folder under \$WORK}"
LOGIN="${LOGIN:-login01-ext.leonardo.cineca.it}"
NAME="${1:?pass the endpoint name printed by the job, or your ENDPOINT_NAME}"
if [[ ! "$NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ || ${#NAME} -gt 160 ]]; then
  echo "ERROR: invalid endpoint name" >&2; exit 1
fi
LOCAL_PORT="${LOCAL_PORT:-8000}"
LISTEN_ADDR="${LISTEN_ADDR:-127.0.0.1}"     # 0.0.0.0 only inside a private pod/VM
POLL="${POLL:-30}"

mkdir -p "$HOME/.ssh"
# Separate sessions let multiple model tunnels run without closing each other.
CTL_DIR=$(mktemp -d "$HOME/.ssh/leo.XXXXXX") || exit 1
CTL="$CTL_DIR/control"
TARGET="${LEO_USER}@${LOGIN}"
mux() { ssh -S "$CTL" "$@" "$TARGET"; }          # control commands: -O check/forward/cancel
mux_run() { ssh -S "$CTL" "$TARGET" "$@"; }       # run a command on the login node

start_master() {
  echo "connecting to $LOGIN (enter password/OTP if asked) ..."
  ssh -f -N -M -S "$CTL" -o ServerAliveInterval=30 -o ServerAliveCountMax=3 "$TARGET"
}
current=""
cleanup() {
  [[ -n "$current" ]] && mux -O cancel -L "$current" 2>/dev/null
  mux -O exit 2>/dev/null || true
  rm -f "$CTL"
  rmdir "$CTL_DIR" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

while true; do
  mux -O check 2>/dev/null || { current=""; start_master || { sleep "$POLL"; continue; }; }

  # login shell so CINECA's profile defines $WORK on the remote side
  ep=$(mux_run "bash -lc 'cat \"\$WORK/${TEAM}/endpoints/${NAME}.env\"' 2>/dev/null" 2>/dev/null || true)
  host=$(sed -n 's/^HOST=//p' <<<"$ep"); port=$(sed -n 's/^PORT=//p' <<<"$ep")
  job=$(sed -n 's/^JOBID=//p' <<<"$ep")

  if [[ -z "$host" || -z "$port" ]]; then
    [[ -n "$current" ]] && { mux -O cancel -L "$current" 2>/dev/null; current=""; }
    echo "$(date +%T) no endpoint '$NAME' yet (is the job running?)"
  else
    want="${LISTEN_ADDR}:${LOCAL_PORT}:${host}:${port}"
    if [[ "$want" != "$current" ]]; then
      [[ -n "$current" ]] && mux -O cancel -L "$current" 2>/dev/null
      if mux -O forward -L "$want"; then
        current="$want"
        echo "$(date +%T) forwarding http://${LISTEN_ADDR}:${LOCAL_PORT} -> ${host}:${port} (job ${job})"
      else
        echo "$(date +%T) could not open forward (local port ${LOCAL_PORT} busy?)"
      fi
    fi
  fi
  sleep "$POLL"
done
