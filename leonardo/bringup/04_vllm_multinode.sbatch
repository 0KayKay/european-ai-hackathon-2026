#!/bin/bash
# Step 4: one vLLM instance spread over 2 full nodes (8 GPUs).
# Tensor parallel inside a node (NVLink), pipeline parallel across nodes
# (needs far less bandwidth than TP over the network).
# Uses vLLM's built-in multiprocessing backend: no Ray cluster needed.
#SBATCH --job-name=vllm-multi
#SBATCH --account=YOUR_PROJECT
#SBATCH --partition=boost_usr_prod
#SBATCH --qos=boost_qos_dbg          # 30 min max; remove for longer runs
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=32
#SBATCH --mem=480GB
#SBATCH --time=00:30:00
#SBATCH --signal=B:TERM@90
#SBATCH --output=%x-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:-.}/common.sh"
ensure_api_key

HEAD_HOST=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -1)
MASTER_ADDR=$(getent hosts "$HEAD_HOST" | awk '{print $1; exit}')
PORT=$(pick_port)
export MASTER_ADDR PORT
export NNODES="$SLURM_NNODES" TP=4 PP="$SLURM_NNODES"
export SRUN_CPUS_PER_TASK="$SLURM_CPUS_PER_TASK"
export PMIX_MCA_gds=hash
export NCCL_DEBUG="${NCCL_DEBUG:-INFO}"      # drop to WARN once it works
export NCCL_DEBUG_SUBSYS="${NCCL_DEBUG_SUBSYS:-INIT,NET}"
# export NCCL_SOCKET_IFNAME=ib0  GLOO_SOCKET_IFNAME=ib0   # if the probe says so
NAME=$(model_endpoint_name "${SLURM_JOB_NAME}")
claim_endpoint "$NAME"
SRUN_PID=""
cleanup() { remove_endpoint "$NAME" || true; if [[ -n "$SRUN_PID" ]]; then kill "$SRUN_PID" 2>/dev/null || true; wait "$SRUN_PID" 2>/dev/null || true; fi; }
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

echo "nodes: $SLURM_JOB_NODELIST  head: $HEAD_HOST ($MASTER_ADDR)  api port: $PORT"
srun --kill-on-bad-exit=1 --ntasks-per-node=1 bash "$SCRIPT_DIR/run_vllm.sh" &
SRUN_PID=$!

# this batch script runs on the head node, so localhost reaches the API
wait_healthy "$PORT" "$SRUN_PID"
SERVED_NAME="$SERVED_NAME" bash "$SCRIPT_DIR/smoke_test.sh" "http://127.0.0.1:${PORT}"
write_endpoint "$NAME" "$HEAD_HOST" "$PORT"

echo "serving until the job ends; scancel ${SLURM_JOB_ID} to stop"
wait "$SRUN_PID"
