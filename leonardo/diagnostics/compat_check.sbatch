#!/bin/bash
# Check a profile's runtime against its model on one GPU, without loading weights (diagnostics/compat_check.py).
# Needs the runtime and at least the model's config.json: `cli.py install <profile> --config-only`.
# Submitted by `cli.py diagnose compat --profile <profile>`. By hand, from the repository root:
#   BASE=$WORK/<team folder> PROFILE=glm53 sbatch -A <account> leonardo/diagnostics/compat_check.sbatch
#SBATCH --job-name=compat-check
#SBATCH --partition=boost_usr_prod
#SBATCH --output=%x-%j.out
#SBATCH --qos=boost_qos_dbg
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=60GB
#SBATCH --time=00:15:00
set -euo pipefail
BASE="${BASE:?}"
PROFILE="${PROFILE:?set PROFILE to a profile name, e.g. glm53}"
# The leonardo/ folder with serve/: set by cli.py, or found from where you ran sbatch.
for RELEASE in "${LEONARDO_RELEASE:-}" "${SLURM_SUBMIT_DIR:-.}/leonardo" "${SLURM_SUBMIT_DIR:-.}"; do
    [[ -n "$RELEASE" && -f "$RELEASE/serve/catalog.json" ]] && break
done
[[ -f "$RELEASE/serve/catalog.json" ]] || { echo 'Run sbatch from the repository root, or set LEONARDO_RELEASE to its leonardo/ folder' >&2; exit 1; }
# Runtime and checkpoint folders of the profile ($BASE/runtimes/<id>, $BASE/checkpoints/<name> by default).
read -r RUNTIME_DIR SIF OVERLAY CHECKPOINT < <(python3 -c 'import json, os, sys
base, release, name = sys.argv[1:]
profile = json.load(open(os.path.join(release, "serve/profiles", name + ".json")))
site = json.load(open(os.path.join(base, "inference-site.json")))
runtime = os.path.join(base, site.get("runtime_paths", {}).get(profile["runtime"], "runtimes/" + profile["runtime"]))
registration = json.load(open(os.path.join(runtime, "runtime.json")))
checkpoint = os.path.join(base, site.get("checkpoints", {}).get(profile["checkpoint"], "checkpoints/" + profile["checkpoint"]))
print(runtime, os.path.join(runtime, registration["sif"]), os.path.join(runtime, registration["overlay"]), checkpoint)' "$BASE" "$RELEASE" "$PROFILE")
[[ -s "$CHECKPOINT/config.json" ]] || { echo "No $CHECKPOINT/config.json; run: cli.py install $PROFILE --config-only" >&2; exit 1; }
CACHE=$(mktemp -d /tmp/compat-check-XXXXXX)
trap 'rm -rf "$CACHE"' EXIT
export SINGULARITYENV_VLLM_ENABLE_CUDA_COMPATIBILITY=1 SINGULARITYENV_VLLM_NO_USAGE_STATS=1
export SINGULARITYENV_HF_HUB_OFFLINE=1 SINGULARITYENV_TRANSFORMERS_OFFLINE=1
export SINGULARITYENV_PYTHONPATH="$OVERLAY"
for v in HF_HOME:hf VLLM_CACHE_ROOT:vllm TRITON_CACHE_DIR:triton XDG_CACHE_HOME:xdg TILELANG_CACHE_DIR:tilelang FLASHINFER_WORKSPACE_BASE:flashinfer; do
    mkdir -p "$CACHE/${v#*:}"; export "SINGULARITYENV_${v%%:*}=$CACHE/${v#*:}"
done
export SINGULARITYENV_COMPAT_CONFIG="$CHECKPOINT/config.json" SINGULARITYENV_COMPAT_PROFILE="$PROFILE"
export SINGULARITYENV_SLURM_JOB_ID="$SLURM_JOB_ID" SINGULARITYENV_CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
echo "profile $PROFILE, runtime $RUNTIME_DIR"
# Re-exec after vLLM installs its CUDA forward-compatibility library paths.
singularity exec --nv --cleanenv --no-home --contain --bind "$CACHE" --bind "$RELEASE/diagnostics" \
    --bind "$RUNTIME_DIR:$RUNTIME_DIR:ro" --bind "$CHECKPOINT:$CHECKPOINT:ro" "$SIF" \
    python3 -c 'import os, sys, vllm; os.execv(sys.executable, [sys.executable, *sys.argv[1:]])' \
    "$RELEASE/diagnostics/compat_check.py"
