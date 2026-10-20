#!/bin/bash
# Step 3: before multi-node vLLM, prove that GPUs on two nodes can talk over
# InfiniBand from inside the container. Prints bandwidth numbers and the
# transport NCCL picked.
#SBATCH --job-name=nccl-check
#SBATCH --account=YOUR_PROJECT
#SBATCH --partition=boost_usr_prod
#SBATCH --qos=boost_qos_dbg
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=4
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=8
#SBATCH --mem=480GB
#SBATCH --time=00:15:00
#SBATCH --output=%x-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:-.}/common.sh"

HEAD_HOST=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -1)
MASTER_ADDR=$(getent hosts "$HEAD_HOST" | awk '{print $1; exit}')
MASTER_PORT=$(( 29500 + SLURM_JOB_ID % 1000 ))
export MASTER_ADDR MASTER_PORT
export SRUN_CPUS_PER_TASK="$SLURM_CPUS_PER_TASK"
export PMIX_MCA_gds=hash          # silences a PMIx warning on Leonardo

# Verbose NCCL init so the log shows which transport was chosen.
export NCCL_DEBUG="${NCCL_DEBUG:-INFO}"
export NCCL_DEBUG_SUBSYS="${NCCL_DEBUG_SUBSYS:-INIT,NET}"
# Optional knobs, uncomment once the probe told you the names:
# export NCCL_SOCKET_IFNAME=ib0     # interface for NCCL's TCP bootstrap
# export GLOO_SOCKET_IFNAME=ib0
# export NCCL_IB_HCA=mlx5

echo "nodes: $SLURM_JOB_NODELIST  master: $HEAD_HOST ($MASTER_ADDR:$MASTER_PORT)"
srun --kill-on-bad-exit=1 bash "$SCRIPT_DIR/nccl_task.sh"

echo
echo "===== transport summary ====="
grep -hoE 'NET/(IB|Socket)[^ ]*|via NET/[A-Za-z/]+|GDRDMA' "${SLURM_SUBMIT_DIR}/${SLURM_JOB_NAME}-${SLURM_JOB_ID}.out" \
  | sort | uniq -c || echo "no NET lines found - check NCCL_DEBUG output above"
