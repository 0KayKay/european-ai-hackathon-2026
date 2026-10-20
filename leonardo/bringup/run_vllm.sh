#!/bin/bash
# Starts vLLM on the current node. Called directly by 02_vllm_single.sbatch and
# once per node (via srun) by 04_vllm_multinode.sbatch.
#
# Inputs (environment): SCRIPT_DIR, TP, PP, NNODES, PORT, MASTER_ADDR (multi-node)
# NODE_RANK defaults to $SLURM_NODEID. Extra vLLM flags: VLLM_EXTRA_ARGS.
set -euo pipefail
source "${SCRIPT_DIR:-${SLURM_SUBMIT_DIR:-$(dirname "$0")}}/common.sh"

NNODES="${NNODES:-1}"
NODE_RANK="${NODE_RANK:-${SLURM_NODEID:-0}}"
TP="${TP:-1}"
PP="${PP:-1}"

container_env_common
maybe_cuda_compat
forward_net_env
cenv VLLM_API_KEY "$(cat "$API_KEY_FILE")"
cenv VLLM_HOST_IP "$(node_ip)"

args=( vllm serve "$MODEL_DIR"
       --served-model-name "$SERVED_NAME"
       --tensor-parallel-size "$TP"
       --pipeline-parallel-size "$PP"
       --gpu-memory-utilization "${GPU_MEM_UTIL:-0.90}"
       --max-model-len "${MAX_MODEL_LEN:-8192}" )

if (( NNODES > 1 )); then
  args+=( --nnodes "$NNODES" --node-rank "$NODE_RANK" --master-addr "${MASTER_ADDR:?}" )
fi
if (( NODE_RANK == 0 )); then
  args+=( --host 0.0.0.0 --port "${PORT:?}" )
else
  args+=( --headless )
fi
# shellcheck disable=SC2206  # word splitting is intended for extra flags
args+=( ${VLLM_EXTRA_ARGS:-} )

echo "[$(hostname -s) rank ${NODE_RANK}/${NNODES}] ${args[*]}"
exec "$CTR" exec --nv --cleanenv --bind "$BASE" "$SIF" "${args[@]}"
