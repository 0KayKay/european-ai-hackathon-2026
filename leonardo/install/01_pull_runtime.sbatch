#!/bin/bash
# Pull a runtime image from serve/catalog.json, pinned by digest:
#   sbatch 01_pull_runtime.sbatch vllm-backport-sm80-0.13.1   # the large profiles
#   sbatch 01_pull_runtime.sbatch vllm-0.31.0                 # stock vLLM, the small profiles
# CPU-only serial queue: it has internet and costs no GPU budget. Submit from this folder.
#SBATCH --job-name=install-runtime
#SBATCH --account=YOUR_PROJECT
#SBATCH --partition=lrd_all_serial
#SBATCH --cpus-per-task=4
#SBATCH --mem=30000M
#SBATCH --time=01:30:00
#SBATCH --output=%x-%j.out
set -euo pipefail
RUNTIME="${1:?usage: sbatch 01_pull_runtime.sbatch <runtime id from serve/catalog.json>}"
BASE="${BASE:-${WORK:?}/team_vllm}"
CATALOG="${SLURM_SUBMIT_DIR:?}/../serve/catalog.json"
IMAGE=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["runtimes"][sys.argv[2]]["image"])' "$CATALOG" "$RUNTIME")
STAGING="$BASE/staging/$RUNTIME"
mkdir -p "$STAGING" "$BASE/tmp" "$BASE/oci-cache"
export SINGULARITY_TMPDIR="$BASE/tmp"
export SINGULARITY_CACHEDIR="$BASE/oci-cache"
SIF="$STAGING/image.sif"
if [[ -s "$SIF" ]]; then
    singularity inspect "$SIF" >/dev/null
else
    singularity pull "$SIF.partial" "$IMAGE"
    mv "$SIF.partial" "$SIF"
fi
# The image digest is pinned; a re-pulled SIF may still hash differently from an earlier one
# (the backport's was 775d1d1a...) because squashfs builds are not reproducible.
sha256sum "$SIF"
singularity inspect --json "$SIF" > "$STAGING/labels.json"
singularity exec --cleanenv --no-home "$SIF" python3 -c 'import vllm, torch; print("versions: vllm", vllm.__version__, "torch", torch.__version__, "cuda", torch.version.cuda)' || true
echo IMAGE_READY "$SIF"
