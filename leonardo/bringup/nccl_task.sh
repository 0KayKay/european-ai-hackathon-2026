#!/bin/bash
# One Slurm task = one GPU. Called by 03_nccl_check.sbatch through srun.
set -euo pipefail
source "${SCRIPT_DIR:-${SLURM_SUBMIT_DIR:-$(dirname "$0")}}/common.sh"

container_env_common
if [[ "${SLURM_PROCID}" == 0 ]]; then maybe_cuda_compat; else maybe_cuda_compat >/dev/null; fi
forward_net_env
cenv RANK "$SLURM_PROCID"
cenv LOCAL_RANK "$SLURM_LOCALID"
cenv WORLD_SIZE "$SLURM_NTASKS"
cenv MASTER_ADDR "${MASTER_ADDR:?}"
cenv MASTER_PORT "${MASTER_PORT:?}"

# vLLM sets LD_LIBRARY_PATH for CUDA compatibility. A fresh interpreter is
# required for the dynamic linker to honor that path; importing vLLM inside
# the benchmark's existing interpreter alone does not update linker search.
exec "$CTR" exec --nv --cleanenv --bind "$BASE" --bind "$SCRIPT_DIR" "$SIF" \
  python3 -c 'import os, sys; import vllm; os.execv(sys.executable, [sys.executable, *sys.argv[1:]])' \
  "$SCRIPT_DIR/nccl_check.py"
