#!/bin/bash
# Step 2: one vLLM server on one GPU. Publish a per-model endpoint only after
# health and inference checks pass. ENDPOINT_NAME can name the service explicitly.
#SBATCH --job-name=vllm-single
#SBATCH --account=YOUR_PROJECT
#SBATCH --partition=boost_usr_prod
#SBATCH --qos=boost_qos_dbg          # 30 min max; remove this line for longer runs
#SBATCH --nodes=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=120GB
#SBATCH --time=00:30:00
#SBATCH --signal=B:TERM@90           # time to clean up before the wall clock
#SBATCH --output=%x-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:-.}/common.sh"
ensure_api_key

NGPU=$(nvidia-smi -L | wc -l)
export TP="$NGPU" PP=1 NNODES=1 NODE_RANK=0
PORT=$(pick_port); export PORT
NAME=$(model_endpoint_name "${SLURM_JOB_NAME}")
claim_endpoint "$NAME"
VLLM_PID=""
cleanup() { remove_endpoint "$NAME" || true; if [[ -n "$VLLM_PID" ]]; then kill "$VLLM_PID" 2>/dev/null || true; wait "$VLLM_PID" 2>/dev/null || true; fi; }
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

bash "$SCRIPT_DIR/run_vllm.sh" &
VLLM_PID=$!

wait_healthy "$PORT" "$VLLM_PID"
SERVED_NAME="$SERVED_NAME" bash "$SCRIPT_DIR/smoke_test.sh" "http://127.0.0.1:${PORT}"
write_endpoint "$NAME" "$(hostname -s)" "$PORT"

echo "serving until the job ends; scancel ${SLURM_JOB_ID} to stop"
wait "$VLLM_PID"
