#!/bin/bash
# What a Curiosity v2 GPU node (DGX B300) looks like from the inside, and which of the things the
# Leonardo setup relies on work there: GPU and driver, Apptainer/Singularity, Enroot/Pyxis, rootless
# Docker, internet (Hugging Face, Docker Hub), node-local and shared storage speed, InfiniBand, and
# whether the login node reaches a port on this node. One GPU, 30 minutes. By hand, after scp:
#   sbatch -A <account> -p <partition> curiosity_node.sbatch           # account/partition: curiosity_login.sh
#   CHECK_VLLM=1 sbatch -A <account> -p <partition> --time=01:00:00 curiosity_node.sbatch
# CHECK_VLLM=1 also pulls the stock vLLM image from serve/catalog.json and checks that its PyTorch
# runs on B300 (compute capability 10.3); the pull alone can take 20+ minutes.
#SBATCH --job-name=curiosity-node
#SBATCH --output=%x-%j.out
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=00:30:00
set -uo pipefail

section() { printf '\n===== %s =====\n' "$1"; }
VLLM_IMAGE="${VLLM_IMAGE:-docker://vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b}"
HOLD_SECONDS="${HOLD_SECONDS:-600}"

section "identity"
echo "node: $(hostname -s)  job: $SLURM_JOB_ID  partition: ${SLURM_JOB_PARTITION:-?}  account: ${SLURM_JOB_ACCOUNT:-?}"
echo "resolved IP of own hostname: $(getent hosts "$(hostname -s)" | awk '{print $1; exit}')"
grep PRETTY_NAME /etc/os-release
uname -r
lscpu | grep -E '^(Architecture|Model name|Socket|Core|Thread|CPU\(s\))'
free -g | head -2

section "GPU"
nvidia-smi --query-gpu=index,name,compute_cap,memory.total,driver_version --format=csv
nvidia-smi | head -4
echo "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-unset}"
nvidia-smi topo -m 2>/dev/null | head -12

# Start the test page early, so there is time to curl it from the login node.
section "port reachability test page"
PORT=$(( 31000 + SLURM_JOB_ID % 10000 ))
WWW=$(mktemp -d)
printf '<h1>Hello from %s, job %s, port %s</h1>\n' "$(hostname -s)" "$SLURM_JOB_ID" "$PORT" > "$WWW/index.html"
(cd "$WWW" && exec python3 -m http.server "$PORT" --bind 0.0.0.0 >/dev/null 2>&1) &
SRV=$!
trap 'kill "$SRV" 2>/dev/null; rm -rf "$WWW" "${SCRATCH:-/nonexistent}/curiosity-io"' EXIT
sleep 2
curl -sS -m 5 -o /dev/null -w "local: %{http_code}\n" "http://127.0.0.1:$PORT/"
echo "From the login node, while this job runs:  curl http://$(hostname -s):$PORT/"
echo "From your laptop through Axis (tests SSH port forwarding):"
echo "  ssh -N -L 18000:$(hostname -s):$PORT ssh.axisapps.io -l <auto-login username>   then open http://127.0.0.1:18000/"

section "proxy settings in the job environment"
env | grep -i _proxy || echo "none"

section "outbound internet"
for url in https://huggingface.co https://registry-1.docker.io/v2/ https://pypi.org https://github.com https://ghcr.io https://nvcr.io; do
  curl -sS -m 10 -o /dev/null -w "$url -> %{http_code}\n" "$url" || echo "$url -> UNREACHABLE"
done
echo "Hugging Face download speed (Qwen3-0.6B weights, ~1.5 GB, at most 90 s):"
curl -sSL -m 90 -o /dev/null -w 'downloaded %{size_download} bytes at %{speed_download} bytes/s\n' \
  https://huggingface.co/Qwen/Qwen3-0.6B/resolve/c1899de289a04d12100db370d81485cdf75e47ca/model.safetensors || true

section "storage"
df -hT "$HOME" /tmp /dev/shm /raid 2>/dev/null
ls -ld "/raid/$SLURM_JOB_ID" "/raid/$SLURM_JOB_ID/tmp" 2>/dev/null || echo "no /raid/$SLURM_JOB_ID"
echo "TMPDIR=${TMPDIR:-unset}"
SCRATCH=""
for candidate in "/raid/$SLURM_JOB_ID" /raid "${TMPDIR:-}" /tmp; do
  if [[ -n "$candidate" && -d "$candidate" && -w "$candidate" ]]; then SCRATCH="$candidate"; break; fi
done
echo "node-local scratch used below: ${SCRATCH:-none}"
io_test() {  # write and read 4 GiB, bypassing the page cache
  mkdir -p "$1/curiosity-io"
  echo "write $1: $(dd if=/dev/zero of="$1/curiosity-io/f" bs=1M count=4096 oflag=direct 2>&1 | tail -1)"
  echo "read  $1: $(dd if="$1/curiosity-io/f" of=/dev/null bs=1M iflag=direct 2>&1 | tail -1)"
  rm -rf "$1/curiosity-io"
}
[[ -n "$SCRATCH" ]] && io_test "$SCRATCH"
io_test "$HOME"
[[ -n "${TEAM_DIR:-}" ]] && io_test "$TEAM_DIR"

section "InfiniBand"
ls /sys/class/infiniband 2>/dev/null | tr '\n' ' ' || echo "no /sys/class/infiniband"
echo
ls /dev/infiniband >/dev/null 2>&1 && echo "/dev/infiniband exists" || echo "/dev/infiniband MISSING (node.py binds it)"
echo "locked-memory limit: $(ulimit -l)"

section "container tools on the node"
for tool in apptainer singularity enroot docker; do
  printf '%-12s %s\n' "$tool" "$(command -v "$tool" || echo -)"
done
(apptainer --version || singularity --version) 2>/dev/null
CTR=$(command -v apptainer || command -v singularity || true)
if [[ -n "$CTR" ]]; then
  echo "-- $CTR exec --nv docker://ubuntu:24.04 nvidia-smi -L"
  export APPTAINER_CACHEDIR="${SCRATCH:-/tmp}/apptainer-cache" SINGULARITY_CACHEDIR="${SCRATCH:-/tmp}/apptainer-cache"
  export APPTAINER_TMPDIR="${SCRATCH:-/tmp}" SINGULARITY_TMPDIR="${SCRATCH:-/tmp}"
  timeout 600 "$CTR" exec --nv docker://ubuntu:24.04 nvidia-smi -L || echo "APPTAINER TEST FAILED"
fi
if srun --help 2>&1 | grep -q -- '--container-image'; then
  echo "-- Pyxis: srun --container-image=ubuntu:24.04 nvidia-smi -L"
  timeout 600 srun --ntasks=1 --container-image=ubuntu:24.04 nvidia-smi -L || echo "PYXIS TEST FAILED"
else
  echo "Pyxis: srun has no --container-image"
fi
if module load rootless-docker 2>/dev/null; then
  echo "-- rootless Docker"
  timeout 60 docker info 2>&1 | grep -E 'Server Version|Rootless|Runtimes|Docker Root Dir|rootless' || echo "docker info failed"
  timeout 600 docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L || echo "DOCKER GPU TEST FAILED"
else
  echo "rootless-docker module not loadable here"
fi

if [[ "${CHECK_VLLM:-0}" == 1 && -n "$CTR" ]]; then
  section "stock vLLM image on B300"
  SIF="${SCRATCH:-/tmp}/vllm.sif"
  echo "pulling $VLLM_IMAGE"
  time "$CTR" pull "$SIF" "$VLLM_IMAGE"
  # No VLLM_ENABLE_CUDA_COMPATIBILITY here: the B300 driver should be newer than the image's CUDA.
  "$CTR" exec --nv --cleanenv "$SIF" python3 -c '
import torch, vllm
print("vllm", vllm.__version__, "torch", torch.__version__, "cuda", torch.version.cuda)
print("arch list", torch.cuda.get_arch_list(), "device capability", torch.cuda.get_device_capability(0))
x = torch.randn(4096, 4096, device="cuda", dtype=torch.bfloat16)
print("bf16 matmul ok", float((x @ x).float().abs().mean()))
print("fp8 dtype", torch.float8_e4m3fn)'
  "$CTR" exec --cleanenv "$SIF" vllm serve --help=all 2>/dev/null | grep -oE 'deepseek_v41|engram-config|TRITON_MLA_SPARSE|FLASHMLA_SPARSE' | sort -u \
    || echo "none of the GLM/DeepSeek options found in this image"
fi

section "holding the test page for $HOLD_SECONDS s"
echo "curl http://$(hostname -s):$PORT/ from the login node now"
sleep "$HOLD_SECONDS"
echo "DONE"
