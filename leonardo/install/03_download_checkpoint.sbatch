#!/bin/bash
# Download a profile's checkpoint (model_id and revision from serve/profiles/<profile>.json) to
# $BASE/checkpoints/<checkpoint>, where site.example.json expects it. CPU-only serial queue
# (internet, no GPU budget). Submit from this folder:
#
#   sbatch 03_download_checkpoint.sbatch qwen235b      # ~239 GB, 48 shards
#   sbatch 03_download_checkpoint.sbatch glm53         # ~328 GB, 62 shards
#   sbatch 03_download_checkpoint.sbatch deepseek41    # 48 shards
#   sbatch 03_download_checkpoint.sbatch qwen06b       # ~1.5 GB; prints the revision to pin
#   MODEL_ID=org/name MODEL_REVISION=<commit> sbatch 03_download_checkpoint.sbatch my-model
#SBATCH --job-name=install-checkpoint
#SBATCH --account=YOUR_PROJECT
#SBATCH --partition=lrd_all_serial
#SBATCH --cpus-per-task=4
#SBATCH --mem=30000M
#SBATCH --time=04:00:00
#SBATCH --output=%x-%j.out
set -euo pipefail
NAME="${1:?usage: sbatch 03_download_checkpoint.sbatch <profile-name>}"
PROFILE="${SLURM_SUBMIT_DIR:?}/../serve/profiles/$NAME.json"
if [[ -f "$PROFILE" ]]; then
    read -r PROFILE_MODEL PROFILE_REVISION CHECKPOINT RUNTIME < <(python3 -c 'import json, sys
p = json.load(open(sys.argv[1]))
print(p["model_id"], p.get("revision") or "-", p["checkpoint"], p["runtime"])' "$PROFILE")
    MODEL_ID="${MODEL_ID:-$PROFILE_MODEL}"
    if [[ "$PROFILE_REVISION" != - ]]; then MODEL_REVISION="${MODEL_REVISION:-$PROFILE_REVISION}"; fi
    # glm53.json has no "revision" field and stays byte-identical to the GPU-validated file.
    if [[ "$NAME" == glm53 ]]; then MODEL_REVISION="${MODEL_REVISION:-91b69215804f2d2036a8867f6ce44848e278dcda}"; fi
else
    : "${MODEL_ID:?set MODEL_ID for an unlisted model}" "${MODEL_REVISION:?pin MODEL_REVISION to a commit}"
    CHECKPOINT="$NAME" RUNTIME="${RUNTIME:-vllm-0.31.0}"
fi
BASE="${BASE:-${WORK:?}/team_vllm}"
# Any image with huggingface_hub works; the profile's own runtime is already there.
SIF="${SIF:-$BASE/staging/$RUNTIME/image.sif}"
mkdir -p "$BASE/checkpoints" "$BASE/cache/hf"
export SINGULARITYENV_MODEL_ID="$MODEL_ID"
export SINGULARITYENV_MODEL_REVISION="${MODEL_REVISION:-}"
export SINGULARITYENV_MODEL_DIR="$BASE/checkpoints/$CHECKPOINT"
export SINGULARITYENV_HF_HOME="$BASE/cache/hf"
export SINGULARITYENV_HF_HUB_DOWNLOAD_TIMEOUT=120
export SINGULARITYENV_HF_HUB_DISABLE_TELEMETRY=1
for v in HF_TOKEN HTTPS_PROXY HTTP_PROXY https_proxy http_proxy NO_PROXY no_proxy; do
    if [[ -n "${!v:-}" ]]; then export "SINGULARITYENV_$v=${!v}"; fi
done
singularity exec --cleanenv --no-home --contain --bind "$BASE" "$SIF" python3 -u - <<'PY'
import json, os, shutil
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download

repo = os.environ["MODEL_ID"]
revision = os.environ["MODEL_REVISION"]
directory = Path(os.environ["MODEL_DIR"])
if not revision:
    # Profile not pinned yet: take the current main and say so, so the profile can be pinned.
    revision = HfApi().model_info(repo).sha
    print("PIN_REVISION", repo, revision, "- set this as \"revision\" in the profile", flush=True)
metadata = HfApi().model_info(repo, revision=revision, files_metadata=True)
assert metadata.sha == revision
weights = {f.rfilename: f for f in metadata.siblings if f.rfilename.endswith(".safetensors")}
needed = sum(f.size for f in weights.values())
existing = sum((directory / name).stat().st_size for name, f in weights.items() if (directory / name).is_file() and (directory / name).stat().st_size == f.size)
free = shutil.disk_usage(directory.parent).free
if free < needed - existing + 100 * 2**30:
    raise RuntimeError("Insufficient free shared storage for checkpoint plus 100 GiB reserve")
print("Downloading", repo, revision, "weight_bytes", needed, flush=True)
snapshot_download(repo, revision=revision, local_dir=directory, max_workers=4,
                  allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "*.tiktoken", "*.jinja", "*.py", "LICENSE"])
if (directory / "model.safetensors.index.json").exists():
    shards = set(json.loads((directory / "model.safetensors.index.json").read_text())["weight_map"].values())
else:
    shards = {"model.safetensors"}  # small models ship one unindexed file
required = shards | {"config.json", "tokenizer.json", "tokenizer_config.json"}
remote = {f.rfilename: f for f in metadata.siblings}
for name in sorted(required):
    path = directory / name
    if not path.is_file() or path.stat().st_size != remote[name].size:
        raise RuntimeError("Missing or incomplete checkpoint file: " + name)
manifest = {"repo": repo, "revision": revision, "weight_bytes": needed,
            "verified_files": {name: remote[name].size for name in sorted(required)},
            "validation": "HF download integrity and required-file size checks"}
(directory / "download_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("CHECKPOINT_READY", directory, "revision", revision, "shards", len(shards), flush=True)
PY
du -sh "$BASE/checkpoints/$CHECKPOINT"
