#!/bin/bash
# Resources are supplied by manage.py, not guessed from ntasks.
set -euo pipefail
umask 077
source /etc/profile.d/modules.sh
module load slurm
B300_RUN="${1:?private run directory required}"
cd "$B300_RUN"
B300_ROOT="$B300_RUN/serve"
B300_IMAGE=$(python3 -c 'import json; print(json.load(open("RUN_CONFIG.json"))["image"])')
B300_REUSE=$(python3 -c 'import json; print(json.load(open("RUN_CONFIG.json")).get("reuse_scratch") or "")')
if [[ -n "$B300_REUSE" ]]; then
  [[ "$B300_REUSE" == /tmp/b300-models.* && -O "$B300_REUSE" && -r "$B300_REUSE/vllm.sqsh" ]]
  B300_SCRATCH="$B300_REUSE"
else
  B300_SCRATCH=$(mktemp -d "/tmp/b300-models.${SLURM_JOB_ID:?}.XXXXXX")
fi
printf '%s\n' "$B300_SCRATCH" > scratch-path
cleanup() {
  # This private run's files only. Slurm terminates the owned step itself.
  rm -f "$B300_RUN/endpoint.json" "$B300_RUN/glm.api-key" \
    "$B300_RUN/ultra.api-key" "$B300_RUN/safety.api-key" "$B300_RUN/safeguard.api-key"
}
trap cleanup EXIT
export ENROOT_CACHE_PATH="$B300_SCRATCH/enroot-cache" ENROOT_DATA_PATH="$B300_SCRATCH/enroot-data"
export ENROOT_RUNTIME_PATH="$B300_SCRATCH/enroot-runtime" ENROOT_TEMP_PATH="$B300_SCRATCH/tmp"
export XDG_RUNTIME_DIR="$B300_SCRATCH/xdg-runtime" XDG_CONFIG_HOME="$B300_SCRATCH/xdg-config"
export XDG_CACHE_HOME="$B300_SCRATCH/xdg-cache" XDG_DATA_HOME="$B300_SCRATCH/xdg-data" TMPDIR="$B300_SCRATCH/tmp"
mkdir -p "$ENROOT_CACHE_PATH" "$ENROOT_DATA_PATH" "$ENROOT_RUNTIME_PATH" "$ENROOT_TEMP_PATH" \
  "$XDG_RUNTIME_DIR" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME"
export HF_HUB_DISABLE_PROGRESS_BARS=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 VLLM_NO_USAGE_STATS=1
export VLLM_ENGINE_READY_TIMEOUT_S=5400
scontrol show job -d "$SLURM_JOB_ID" > slurm-allocation.txt
if [[ ! -r "$B300_SCRATCH/vllm.sqsh" ]]; then
  enroot import --output "$B300_SCRATCH/vllm.sqsh" "docker://$B300_IMAGE"
fi
# Keep the event's mandatory prolog and Python hook; never bypass them.
env -u TMPDIR srun --container-image="$B300_SCRATCH/vllm.sqsh" \
  --container-mounts="$B300_ROOT:/deployment,$B300_RUN:/runstate,$B300_SCRATCH:/workspace,/cm/shared/apps/slurm/scripts:/cm/shared/apps/slurm/scripts:ro,/cm/shared/apps/python_hooks:/cm/shared/apps/python_hooks:ro" \
  --container-workdir=/deployment --no-container-mount-home \
  python3 /deployment/server.py --run /runstate --scratch /workspace/work --host-scratch "$B300_SCRATCH"
