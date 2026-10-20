# common.sh - shared settings and helpers, sourced by every job script.
# Submit all jobs from this folder (sbatch copies the script, so we locate
# helpers through $SLURM_SUBMIT_DIR).

# ---- edit me ----------------------------------------------------------------
TEAM="${TEAM:-team_guardrails}"                 # folder under $WORK
VLLM_TAG="${VLLM_TAG:-latest}"                 # pin a real tag (e.g. v0.x.y) once it works
VLLM_IMAGE="${VLLM_IMAGE:-docker://vllm/vllm-openai:${VLLM_TAG}}"
MODEL_ID="${MODEL_ID:-Qwen/Qwen3-0.6B}"        # small model for plumbing tests
SERVED_NAME="${SERVED_NAME:-${MODEL_ID##*/}}"  # name clients use in API calls
CUDA_COMPAT="${CUDA_COMPAT:-auto}"             # auto | 1 | 0  (see maybe_cuda_compat)
# -----------------------------------------------------------------------------

SCRIPT_DIR="${SCRIPT_DIR:-${SLURM_SUBMIT_DIR:-$(pwd)}}"
export SCRIPT_DIR

BASE="${BASE:-${WORK:?WORK is not set - are you on Leonardo?}/${TEAM}}"
SIF="${SIF:-${BASE}/images/vllm-${VLLM_TAG}.sif}"
MODELS_DIR="${BASE}/models"
MODEL_DIR="${MODEL_DIR:-${MODELS_DIR}/${MODEL_ID//\//__}}"
CACHE_DIR="${BASE}/cache"
ENDPOINT_DIR="${BASE}/endpoints"
SECRETS_DIR="${HOME}/.config/leonardo-vllm"
API_KEY_FILE="${SECRETS_DIR}/api_key"
AUTH_HEADER_FILE="${SECRETS_DIR}/auth_header"

mkdir -p "${BASE}/images" "${MODELS_DIR}" "${CACHE_DIR}" "${ENDPOINT_DIR}"

# Leonardo ships Singularity PRO; plain Apptainer elsewhere. Same CLI.
if command -v singularity >/dev/null 2>&1; then
  CTR=singularity; ENVPFX=SINGULARITYENV_
elif command -v apptainer >/dev/null 2>&1; then
  CTR=apptainer; ENVPFX=APPTAINERENV_
else
  module load singularity >/dev/null 2>&1 || true
  if command -v singularity >/dev/null 2>&1; then CTR=singularity; ENVPFX=SINGULARITYENV_
  else echo "ERROR: neither singularity nor apptainer found" >&2; return 1 2>/dev/null || exit 1; fi
fi

# Set a variable inside the container. We always run with --cleanenv so host
# modules (LD_LIBRARY_PATH, PYTHONPATH, ...) cannot leak in.
cenv() { export "${ENVPFX}$1=$2"; }

# Forward NCCL/GLOO/UCX tuning variables set on the host into the container.
forward_net_env() {
  local v
  for v in $(compgen -e | grep -E '^(NCCL_|GLOO_|UCX_|TORCH_NCCL_|TORCH_DISTRIBUTED_)' || true); do
    cenv "$v" "${!v}"
  done
  if [[ -n "${CUDA_VISIBLE_DEVICES:-}" ]]; then cenv CUDA_VISIBLE_DEVICES "$CUDA_VISIBLE_DEVICES"; fi
}

container_env_common() {
  cenv HF_HOME "${CACHE_DIR}/hf"
  cenv HF_HUB_OFFLINE 1               # compute nodes have no internet
  cenv TRANSFORMERS_OFFLINE 1
  cenv VLLM_CACHE_ROOT "${CACHE_DIR}/vllm"
  cenv TRITON_CACHE_DIR "${CACHE_DIR}/triton"
  cenv TORCHINDUCTOR_CACHE_DIR "${CACHE_DIR}/inductor"
  cenv XDG_CACHE_HOME "${CACHE_DIR}/xdg"
  cenv VLLM_NO_USAGE_STATS 1
  cenv DO_NOT_TRACK 1
}

# vLLM images ship NVIDIA's CUDA forward-compatibility libraries. If the host
# driver is older than what the image was built for (CUDA 13 needs R580+),
# VLLM_ENABLE_CUDA_COMPATIBILITY=1 makes vLLM load them. A100 is a supported GPU.
maybe_cuda_compat() {
  local drv
  drv=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || true)
  echo "[$(hostname -s)] host NVIDIA driver: ${drv:-unknown}"
  case "$CUDA_COMPAT" in
    1) cenv VLLM_ENABLE_CUDA_COMPATIBILITY 1 ;;
    0) ;;
    auto)
      if [[ -n "$drv" && "${drv%%.*}" -lt 580 ]]; then
        echo "[$(hostname -s)] driver < 580 -> enabling vLLM CUDA compatibility libs (set CUDA_COMPAT=0 to disable)"
        cenv VLLM_ENABLE_CUDA_COMPATIBILITY 1
      fi ;;
  esac
}

# Run a command inside the vLLM image with GPUs and the team folder mounted.
ctr_exec() {
  "$CTR" exec --nv --cleanenv \
    --bind "${BASE}" --bind "${SCRIPT_DIR}" \
    "${SIF}" "$@"
}

node_ip() {
  local ip
  ip=$(getent hosts "$(hostname -s)" | awk '{print $1; exit}')
  [[ -n "$ip" ]] || ip=$(hostname -I | awk '{print $1}')
  echo "$ip"
}

# A port derived from the job id, skipping ports already in use on this node.
pick_port() {
  local p=$(( 20000 + ${SLURM_JOB_ID:-$$} % 20000 ))
  while ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":${p}\$"; do p=$((p + 1)); done
  echo "$p"
}

# Endpoint files are how the tunnel (and later your gateway) finds the server.
# Stable per-model names follow restarts; explicit ENDPOINT_NAME names services.
validate_endpoint_name() {
  if [[ ! "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ || ${#1} -gt 160 ]]; then
    echo "ERROR: endpoint name must be 1-160 letters/digits/dots/underscores/hyphens, starting with a letter or digit" >&2
    return 1
  fi
}

model_endpoint_name() {  # job kind
  local name slug checksum
  if [[ -n "${ENDPOINT_NAME:-}" ]]; then
    name="$ENDPOINT_NAME"
  else
    slug=$(printf '%s' "$MODEL_ID" | LC_ALL=C tr -c 'A-Za-z0-9._-' '_')
    checksum=$(printf '%s' "$MODEL_ID" | cksum | awk '{print $1}')
    name="$1-${slug:0:80}-${checksum}"
  fi
  validate_endpoint_name "$name" || return 1
  printf '%s\n' "$name"
}

ENDPOINT_OWNER="${SLURM_JOB_ID:-local}-$$"

# Atomic mkdir works across nodes on the shared filesystem. Never take over
# another job's name silently, even if the previous job appears to be dead.
claim_endpoint() {  # name
  local name=$1 lock
  validate_endpoint_name "$name" || return 1
  lock="${ENDPOINT_DIR}/.${name}.lock"
  if ! mkdir "$lock" 2>/dev/null; then
    echo "ERROR: endpoint '$name' is reserved; choose another ENDPOINT_NAME, or check the owning job before clearing $lock" >&2
    return 1
  fi
  if ! printf '%s\n' "$ENDPOINT_OWNER" > "$lock/owner"; then
    rmdir "$lock" 2>/dev/null || true
    return 1
  fi
  if [[ -e "${ENDPOINT_DIR}/${name}.env" ]]; then
    echo "ERROR: endpoint '$name' already exists; check its job before removing the stale file" >&2
    rm -f "$lock/owner"; rmdir "$lock"
    return 1
  fi
  echo "endpoint reserved: $name (owner $ENDPOINT_OWNER)"
}

write_endpoint() {   # name host port
  local name=$1 f lock tmp
  validate_endpoint_name "$name" || return 1
  f="${ENDPOINT_DIR}/${name}.env"
  lock="${ENDPOINT_DIR}/.${name}.lock"
  if [[ "$(cat "$lock/owner" 2>/dev/null || true)" != "$ENDPOINT_OWNER" ]]; then
    echo "ERROR: claim endpoint '$name' before publishing it" >&2
    return 1
  fi
  tmp=$(mktemp "${ENDPOINT_DIR}/.${name}.XXXXXX") || return 1
  {
    echo "HOST=$2"
    echo "IP=$(getent hosts "$2" | awk '{print $1; exit}')"
    echo "PORT=$3"
    echo "JOBID=${SLURM_JOB_ID:-none}"
    echo "OWNER=${ENDPOINT_OWNER}"
    echo "MODEL=${SERVED_NAME}"
    echo "STARTED=$(date -Is)"
  } > "$tmp" || { rm -f "$tmp"; return 1; }
  # mktemp creates mode 600; endpoint addresses may be read by the project.
  chmod 644 "$tmp" || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$f" || { rm -f "$tmp"; return 1; }
  echo "endpoint written: $f"; cat "$f"
}

remove_endpoint() {  # name - only the process which owns the claim may remove it
  local name=$1 f lock
  validate_endpoint_name "$name" || return 1
  f="${ENDPOINT_DIR}/${name}.env"
  lock="${ENDPOINT_DIR}/.${name}.lock"
  if [[ "$(cat "$lock/owner" 2>/dev/null || true)" == "$ENDPOINT_OWNER" ]]; then
    if [[ -f "$f" ]] && grep -Fxq "OWNER=${ENDPOINT_OWNER}" "$f"; then rm -f "$f"; fi
    rm -f "$lock/owner"
    rmdir "$lock"
  fi
}

# One API key per user, kept out of $WORK (which the whole project can read).
ensure_api_key() {
  mkdir -p "$SECRETS_DIR"; chmod 700 "$SECRETS_DIR"
  if [[ ! -s "$API_KEY_FILE" ]]; then
    ( umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$API_KEY_FILE" )
    echo "created API key in $API_KEY_FILE"
  fi
  ( umask 077; printf 'Authorization: Bearer %s\n' "$(cat "$API_KEY_FILE")" > "$AUTH_HEADER_FILE" )
}

wait_healthy() {     # port pid
  local port=$1 pid=$2 t=0
  until curl -sf -o /dev/null -H "@${AUTH_HEADER_FILE}" "http://127.0.0.1:${port}/health"; do
    if ! kill -0 "$pid" 2>/dev/null; then echo "vLLM exited before becoming healthy" >&2; return 1; fi
    sleep 10; t=$((t + 10))
    if (( t % 60 == 0 )); then echo "waiting for vLLM on port ${port} ... ${t}s"; fi
  done
  echo "vLLM healthy on $(hostname -s):${port} after ${t}s"
}
