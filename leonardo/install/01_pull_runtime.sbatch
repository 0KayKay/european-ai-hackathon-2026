#!/bin/bash
# Pull the pinned community vLLM SM80 backport image that every serve/ profile
# runs on. CPU-only serial queue: it has internet and costs no GPU budget.
#SBATCH --job-name=install-runtime
#SBATCH --account=YOUR_PROJECT
#SBATCH --partition=lrd_all_serial
#SBATCH --cpus-per-task=4
#SBATCH --mem=30000M
#SBATCH --time=01:30:00
#SBATCH --output=%x-%j.out
set -euo pipefail
BASE="${BASE:-${WORK:?}/team_vllm}"
STAGING="$BASE/staging/vllm-backport-sm80-0.13.1"
mkdir -p "$STAGING" "$BASE/tmp" "$BASE/oci-cache"
export SINGULARITY_TMPDIR="$BASE/tmp"
export SINGULARITY_CACHEDIR="$BASE/oci-cache"
IMAGE='docker://lazymio/vllm-backport@sha256:0fc33cd7d2be777bf6faaee9b02adf781dc6d0bc44b9a09ce4865089f67e9cba'
SIF="$STAGING/image.sif"
if [[ -s "$SIF" ]]; then
    singularity inspect "$SIF" >/dev/null
else
    singularity pull "$SIF" "$IMAGE"
fi
# The image digest is pinned; a re-pulled SIF may still hash differently from
# the historical one (775d1d1a...) because squashfs builds are not reproducible.
sha256sum "$SIF"
singularity inspect --json "$SIF" > "$STAGING/labels.json"
echo IMAGE_READY "$SIF"
