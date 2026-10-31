#!/bin/bash
# Run the load test inside a running b300/node job: an extra `srun --overlap` step on the
# same node, so it needs no GPUs or second allocation and can read every vLLM server's /metrics.
#
#   b300/loadtest/loadtest.sh JOBID ramp --profile mixed
#   b300/loadtest/loadtest.sh JOBID suite
#   b300/loadtest/loadtest.sh JOBID run --profile agentic --employees 5000 --pattern bursty
#   b300/loadtest/loadtest.sh JOBID sweep --sweep-role main --input 8000 --output 500
#
# Environment: CACHE_DIR (as for node.sbatch), LOADTEST_CPUS (default 8), OUT (result folder).
# The step ends when this terminal's session does: run it inside tmux/screen, or with nohup.
set -euo pipefail
usage='usage: loadtest.sh JOBID run|ramp|suite|sweep [options]'
JOBID=${1:?$usage}
COMMAND=${2:?$usage}
shift 2
if [[ -r /etc/profile.d/modules.sh ]]; then
  source /etc/profile.d/modules.sh
  module load slurm
fi
CACHE_DIR=${CACHE_DIR:-/storage/hackathon_teams/eai-team09/experiment}
RUN="$CACHE_DIR/runs/$JOBID"
[[ -r "$RUN/endpoint.json" ]] || { echo "No $RUN/endpoint.json: is $JOBID a running b300/node job?" >&2; exit 1; }
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=${OUT:-$CACHE_DIR/loadtests/$(date +%Y%m%d-%H%M%S)-$COMMAND}
mkdir -p "$OUT/src"
cp "$HERE"/{loadtest,workload,client,report}.py "$OUT/src/"  # the exact code behind these results
echo "results: $OUT"
srun --jobid="$JOBID" --overlap --nodes=1 --ntasks=1 --cpus-per-task="${LOADTEST_CPUS:-8}" --job-name=loadtest \
  python3 "$OUT/src/loadtest.py" "$COMMAND" --endpoint "$RUN/endpoint.json" --plan "$RUN/plan.json" \
  --out "$OUT" "$@" 2>&1 | tee -a "$OUT/console.log"
