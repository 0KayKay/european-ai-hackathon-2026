#!/bin/bash
# Download a pinned checkpoint to $BASE/checkpoints/<profile>, where
# site.example.json expects it. CPU-only serial queue (internet, no GPU budget).
#
#   sbatch 03_download_checkpoint.sbatch qwen235b      # ~239 GB, 48 shards
#   sbatch 03_download_checkpoint.sbatch glm53         # ~328 GB, 62 shards
#   sbatch 03_download_checkpoint.sbatch deepseek41    # 48 shards
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
case "$NAME" in
    qwen235b)   MODEL_ID=Qwen/Qwen3-235B-A22B-FP8;             MODEL_REVISION=39eb2b067ea6b8e3e1dd97d3cd0c7ffeaf3e1a35 ;;
    glm53)      MODEL_ID=zai-org/GLM-5.3-Flash;                MODEL_REVISION=91b69215804f2d2036a8867f6ce44848e278dcda ;;
    deepseek41) MODEL_ID=deepseek-ai/DeepSeek-V4.1-Flash-1017; MODEL_REVISION=1bae0e1088993b9b82de08f59de5be5a34e42170 ;;
    *) : "${MODEL_ID:?set MODEL_ID for an unlisted model}" "${MODEL_REVISION:?pin MODEL_REVISION to a commit}" ;;
esac
BASE="${BASE:-${WORK:?}/team_vllm}"
SIF="${SIF:-$BASE/staging/vllm-backport-sm80-0.13.1/image.sif}"
mkdir -p "$BASE/checkpoints" "$BASE/cache/hf"
export SINGULARITYENV_MODEL_ID="$MODEL_ID"
export SINGULARITYENV_MODEL_REVISION="$MODEL_REVISION"
export SINGULARITYENV_MODEL_DIR="$BASE/checkpoints/$NAME"
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
index = json.loads((directory / "model.safetensors.index.json").read_text())
required = set(index["weight_map"].values()) | {"config.json", "tokenizer.json", "tokenizer_config.json"}
remote = {f.rfilename: f for f in metadata.siblings}
for name in sorted(required):
    path = directory / name
    if not path.is_file() or path.stat().st_size != remote[name].size:
        raise RuntimeError("Missing or incomplete checkpoint file: " + name)
manifest = {"repo": repo, "revision": revision, "weight_bytes": needed,
            "verified_files": {name: remote[name].size for name in sorted(required)},
            "validation": "HF download integrity and required-file size checks"}
(directory / "download_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("CHECKPOINT_READY", directory, "shards", len(set(index["weight_map"].values())), flush=True)
PY
du -sh "$BASE/checkpoints/$NAME"
