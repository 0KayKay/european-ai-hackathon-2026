#!/bin/bash
# Resource requests are supplied by `inference.py gateway-up` from the site configuration.
set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?}"
export GATEWAY_PORT=$((41000 + SLURM_JOB_ID % 10000))
printf 'START %s job=%s node=%s port=%s\n' "$(date -Is)" "$SLURM_JOB_ID" "$(hostname -s)" "$GATEWAY_PORT"
# Queue the next job first: --dependency=singleton holds it until this one ends.
python3 inference.py gateway-next || echo 'WARNING: successor not queued; the gateway stops at this job end' >&2
GATEWAY_PID=''
cleanup() {
    if [[ -n "$GATEWAY_PID" ]]; then kill "$GATEWAY_PID" 2>/dev/null || true; wait "$GATEWAY_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
python3 gateway.py serve &
GATEWAY_PID=$!
wait "$GATEWAY_PID"
