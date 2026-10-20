#!/bin/bash
# Step 0: pull the vLLM image as a .sif and download the test model.
# Runs on lrd_all_serial: budget free, has internet, 4 cores / 30 GB, max 4 h.
#SBATCH --job-name=prepare
#SBATCH --account=YOUR_PROJECT
#SBATCH --partition=lrd_all_serial
#SBATCH --cpus-per-task=4
#SBATCH --mem=30000M
#SBATCH --gres=tmpfs:150G
#SBATCH --time=03:30:00
#SBATCH --output=%x-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:-.}/common.sh"

ensure_api_key

# Keep image layers out of $HOME (50 GB) and build squashfs on the local SSD.
export SINGULARITY_CACHEDIR="${SCRATCH:-$BASE}/.container-cache"
export APPTAINER_CACHEDIR="$SINGULARITY_CACHEDIR"
export SINGULARITY_TMPDIR="${TMPDIR:-/tmp}"
export APPTAINER_TMPDIR="$SINGULARITY_TMPDIR"
mkdir -p "$SINGULARITY_CACHEDIR"

echo "== $CTR $("$CTR" --version)"
echo "== quick internet check (expect 200)"
curl -sS -m 10 -o /dev/null -w '%{http_code}\n' https://huggingface.co || echo "no direct internet here"

if [[ -s "$SIF" ]]; then
  echo "== image exists: $SIF"
else
  echo "== pulling $VLLM_IMAGE -> $SIF (takes a while)"
  tmp="${SIF%.sif}.partial.sif"
  rm -f "$tmp"
  "$CTR" pull "$tmp" "$VLLM_IMAGE"
  mv "$tmp" "$SIF"
fi
ls -lh "$SIF"

echo "== versions inside the image"
"$CTR" exec --cleanenv "$SIF" python3 -c \
  "import torch, vllm; print('vllm', vllm.__version__, '| torch', torch.__version__, '| built for CUDA', torch.version.cuda, '| NCCL', torch.cuda.nccl.version())" \
  || echo "WARNING: version check failed"

echo "== InfiniBand userspace libs inside the image (needed for NCCL over IB)"
if "$CTR" exec --cleanenv "$SIF" sh -c 'ldconfig -p | grep -q "libibverbs.so.1"'; then
  echo "libibverbs: present"
  "$CTR" exec --cleanenv "$SIF" sh -c 'ls /etc/libibverbs.d 2>/dev/null | head; ls /usr/lib/x86_64-linux-gnu/libibverbs/ 2>/dev/null | grep -i mlx5 || echo "WARNING: no mlx5 provider found"'
else
  echo "WARNING: libibverbs missing -> multi-node NCCL will fall back to TCP. See Dockerfile.ib"
fi

echo "== downloading model $MODEL_ID -> $MODEL_DIR"
container_env_common
cenv HF_HUB_OFFLINE 0
cenv TRANSFORMERS_OFFLINE 0
for v in HF_TOKEN HTTPS_PROXY HTTP_PROXY https_proxy http_proxy NO_PROXY no_proxy; do
  if [[ -n "${!v:-}" ]]; then cenv "$v" "${!v}"; fi
done
"$CTR" exec --cleanenv --bind "$BASE" "$SIF" python3 -c \
  "from huggingface_hub import snapshot_download; p = snapshot_download('${MODEL_ID}', local_dir='${MODEL_DIR}', allow_patterns=['*.json','*.safetensors','*.txt','*.model','*.tiktoken','*.py','*.jinja']); print('model at', p)"
du -sh "$MODEL_DIR"
echo "== done"
