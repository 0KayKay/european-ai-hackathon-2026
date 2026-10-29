#!/bin/bash
#SBATCH --job-name=b300-node
#SBATCH --account=eai-hackathon
#SBATCH --partition=hackathon
#SBATCH --qos=eai-team09
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus=8
#SBATCH --cpus-per-task=64
#SBATCH --mem=960G
#SBATCH --time=04:00:00
#SBATCH --signal=B:TERM@120
#SBATCH --output=b300-node-%j.log
#
# Fill one 8-GPU B300 node with the models in lineup.json behind one agentgateway.
#
#   sbatch b300/node/node.sbatch                          # serve the enabled lineup
#   MODE=prefetch sbatch --gpus=1 b300/node/node.sbatch    # only fill the cache
#   B300_MODELS=glm-tp8 sbatch b300/node/node.sbatch       # pick lineup ids
#
# Optional environment (set it in front of sbatch; --export=ALL,X=a,b splits comma lists):
#   CACHE_DIR              persistent weights, images, compile caches and runs
#                          (default /storage/hackathon_teams/eai-team09/experiment)
#   B300_MODELS            comma-separated lineup ids (default: the enabled ones)
#   MODE                   serve (default) or prefetch
#   GATEWAY_PORT           agentgateway port on the node (default 18080)
#   GATEWAY_KEY_FILE       client API key (default CACHE_DIR/keys/gateway.key, created once)
#   PERSIST_COMPILE_CACHE  1 (default) keeps vLLM/Triton/FlashInfer caches in CACHE_DIR
#   CONTAINER_WRITABLE     1 (default) gives each container a writable root filesystem
#   NODE_DIR               this folder, if it cannot be derived from the submitted script
#   HF_TOKEN               only for gated models
set -euo pipefail
umask 007  # the cache is shared with the team; run folders and keys are made private below

if [[ -r /etc/profile.d/modules.sh ]]; then
  source /etc/profile.d/modules.sh
  module load slurm
fi

CACHE_DIR=${CACHE_DIR:-/storage/hackathon_teams/eai-team09/experiment}
MODE=${MODE:-serve}
GATEWAY_PORT=${GATEWAY_PORT:-18080}
GATEWAY_KEY_FILE=${GATEWAY_KEY_FILE:-$CACHE_DIR/keys/gateway.key}
export PERSIST_COMPILE_CACHE=${PERSIST_COMPILE_CACHE:-1}
[[ "$MODE" == serve || "$MODE" == prefetch ]] || { echo "MODE must be serve or prefetch" >&2; exit 1; }

# sbatch runs a spooled copy of this script; the lineup lives next to the original.
if [[ -z "${NODE_DIR:-}" ]]; then
  script=$(scontrol show job "$SLURM_JOB_ID" | sed -n 's/^ *Command=\([^ ]*\).*/\1/p')
  [[ "$script" == /* ]] || script="$SLURM_SUBMIT_DIR/$script"
  NODE_DIR=$(dirname "$script")
fi
[[ -r "$NODE_DIR/lineup.json" ]] || { echo "Cannot find lineup.json; set NODE_DIR to the b300/node folder" >&2; exit 1; }

RUN="$CACHE_DIR/runs/$SLURM_JOB_ID"
mkdir -p "$CACHE_DIR"/{hf,images,bin,keys,compile} "$RUN"/{deploy,logs,state}
chmod 700 "$RUN" "$CACHE_DIR/keys"
# Snapshot the sources so edits during the run do not change it.
cp "$NODE_DIR"/{lineup.json,node.py,launch.py} "$RUN/deploy/"
NODE_PY=("python3" "$RUN/deploy/node.py")

echo "job $SLURM_JOB_ID on $(hostname), mode $MODE, run folder $RUN"
scontrol show job -d "$SLURM_JOB_ID" > "$RUN/slurm-allocation.txt"
df -h "$CACHE_DIR" /tmp || true
nvidia-smi --query-gpu=index,name,memory.total,memory.used --format=csv || true

"${NODE_PY[@]}" plan --lineup "$RUN/deploy/lineup.json" --models "${B300_MODELS:-}" \
  --gateway-port "$GATEWAY_PORT" --out "$RUN/plan.json"
PLAN="$RUN/plan.json"

new_key() { python3 -c 'import secrets; print(secrets.token_urlsafe(36))'; }
if [[ ! -s "$GATEWAY_KEY_FILE" ]]; then (umask 077; new_key > "$GATEWAY_KEY_FILE"); fi
(umask 077; new_key > "$RUN/backend.key")

SCRATCH=$(mktemp -d "/tmp/b300-node.$SLURM_JOB_ID.XXXXXX")
export ENROOT_CACHE_PATH="$SCRATCH/enroot-cache" ENROOT_DATA_PATH="$SCRATCH/enroot-data"
export ENROOT_RUNTIME_PATH="$SCRATCH/enroot-runtime" ENROOT_TEMP_PATH="$SCRATCH/tmp"
export XDG_RUNTIME_DIR="$SCRATCH/xdg-runtime" XDG_CONFIG_HOME="$SCRATCH/xdg-config"
export XDG_CACHE_HOME="$SCRATCH/xdg-cache" XDG_DATA_HOME="$SCRATCH/xdg-data" TMPDIR="$SCRATCH/tmp"
mkdir -p "$ENROOT_CACHE_PATH" "$ENROOT_DATA_PATH" "$ENROOT_RUNTIME_PATH" "$ENROOT_TEMP_PATH" \
  "$XDG_RUNTIME_DIR" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME"
export HF_HUB_DISABLE_PROGRESS_BARS=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 VLLM_NO_USAGE_STATS=1

STEP_PIDS=()
GW_PID=''
cleanup() {
  [[ -n "$GW_PID" ]] && kill "$GW_PID" 2>/dev/null || true
  # srun forwards TERM to its container; killing only the wrapper subshell would not.
  for pid in "${STEP_PIDS[@]}"; do pkill -TERM -P "$pid" 2>/dev/null || true; done
  sleep 5
  rm -f "$RUN/backend.key"
  if grep -qs "\"job\": \"$SLURM_JOB_ID\"" "$CACHE_DIR/runs/latest-endpoint.json"; then
    rm -f "$CACHE_DIR/runs/latest-endpoint.json"
  fi
  rm -rf "$SCRATCH" || true
}
trap cleanup EXIT
trap 'echo "$(date +%T) stopping (signal)"; exit 0' TERM INT

# Container images: imported once into the cache as squashfs files.
while read -r key ref sqsh; do
  if [[ ! -s "$CACHE_DIR/images/$sqsh" ]]; then
    echo "$(date +%T) importing image $key ($ref)"
    enroot import --output "$SCRATCH/$sqsh" "docker://$ref"
    cp "$SCRATCH/$sqsh" "$CACHE_DIR/images/.$sqsh.$SLURM_JOB_ID"
    mv -f "$CACHE_DIR/images/.$sqsh.$SLURM_JOB_ID" "$CACHE_DIR/images/$sqsh"
    rm -f "$SCRATCH/$sqsh"
  else
    echo "image $key: cache hit"
  fi
done < <("${NODE_PY[@]}" get --plan "$PLAN" images)

# agentgateway: one static binary, checksum-pinned, run on the batch host.
read -r GW_VERSION GW_ARCH GW_URL GW_SHA < <("${NODE_PY[@]}" get --plan "$PLAN" gateway)
GW_BIN="$CACHE_DIR/bin/agentgateway-$GW_VERSION-$GW_ARCH"
if ! echo "$GW_SHA  $GW_BIN" | sha256sum -c --status 2>/dev/null; then
  echo "$(date +%T) downloading agentgateway $GW_VERSION"
  curl -fsSL -o "$GW_BIN.$SLURM_JOB_ID" "$GW_URL"
  echo "$GW_SHA  $GW_BIN.$SLURM_JOB_ID" | sha256sum -c --status || { echo "agentgateway checksum mismatch" >&2; exit 1; }
  chmod 750 "$GW_BIN.$SLURM_JOB_ID"
  mv -f "$GW_BIN.$SLURM_JOB_ID" "$GW_BIN"
fi

CPUS=${SLURM_CPUS_PER_TASK:-$SLURM_CPUS_ON_NODE}
WRITABLE=()
[[ "${CONTAINER_WRITABLE:-1}" == 1 ]] && WRITABLE=(--container-writable)
# Keep the site's read-only Slurm prolog and Python hook mounts; never bypass them.
MOUNTS="$CACHE_DIR:$CACHE_DIR,$SCRATCH:$SCRATCH"
MOUNTS+=",/cm/shared/apps/slurm/scripts:/cm/shared/apps/slurm/scripts:ro"
MOUNTS+=",/cm/shared/apps/python_hooks:/cm/shared/apps/python_hooks:ro"

# One overlapping step per model: all steps share the allocation, launch.py picks the GPUs.
step() {
  local sqsh=$1
  shift
  env -u TMPDIR srun --overlap --nodes=1 --ntasks=1 --cpus-per-task="$CPUS" \
    --container-image="$CACHE_DIR/images/$sqsh" --container-name="b300node-${sqsh%.sqsh}" \
    --container-mounts="$MOUNTS" --container-workdir="$RUN" --no-container-mount-home \
    "${WRITABLE[@]}" "$@" < /dev/null  # srun would otherwise consume the read loops' input
}
LAUNCH=(python3 "$RUN/deploy/launch.py" --plan "$PLAN" --cache "$CACHE_DIR" --run "$RUN" --scratch "$SCRATCH")

# Unpack each image once; the model steps then reuse the named container.
while read -r key ref sqsh; do
  step "$sqsh" true
done < <("${NODE_PY[@]}" get --plan "$PLAN" images)

if [[ "$MODE" == prefetch ]]; then
  while read -r id key sqsh; do
    step "$sqsh" "${LAUNCH[@]}" --model "$id" --prefetch > "$RUN/logs/$id.prefetch.log" 2>&1 &
    STEP_PIDS+=($!)
  done < <("${NODE_PY[@]}" get --plan "$PLAN" models)
  failed=0
  for pid in "${STEP_PIDS[@]}"; do wait "$pid" || failed=1; done
  grep -h -E 'cache hit|downloaded in|Error' "$RUN"/logs/*.prefetch.log || true
  echo "$(date +%T) prefetch finished, failed=$failed; cache: $(du -sh "$CACHE_DIR/hf" | cut -f1) weights"
  exit "$failed"
fi

required=$("${NODE_PY[@]}" get --plan "$PLAN" gpus)
visible=${SLURM_GPUS_ON_NODE:-0}
(( visible >= required )) || { echo "Lineup needs $required GPUs, allocation has $visible" >&2; exit 1; }

while read -r id key sqsh; do
  (
    rc=0
    step "$sqsh" "${LAUNCH[@]}" --model "$id" > "$RUN/logs/$id.log" 2>&1 || rc=$?
    echo "$rc" > "$RUN/state/$id.exited"
    echo "$(date +%T) model step $id exited with $rc; see $RUN/logs/$id.log"
  ) &
  STEP_PIDS+=($!)
done < <("${NODE_PY[@]}" get --plan "$PLAN" models)

"${NODE_PY[@]}" gateway --plan "$PLAN" --port "$GATEWAY_PORT" --key-file "$GATEWAY_KEY_FILE" \
  --backend-key-file "$RUN/backend.key" --out "$RUN/gateway.yaml"
start_gateway() {
  "$GW_BIN" -f "$RUN/gateway.yaml" >> "$RUN/logs/gateway.log" 2>&1 &
  GW_PID=$!
}
start_gateway

NODE_IP=$(python3 -c 'import socket; print(socket.gethostbyname(socket.gethostname()))')
ENDPOINT=$(printf '{"job": "%s", "node": "%s", "ip": "%s", "port": %s, "base_url": "http://%s:%s/v1", "key_file": "%s", "run": "%s"}' \
  "$SLURM_JOB_ID" "$(hostname)" "$NODE_IP" "$GATEWAY_PORT" "$NODE_IP" "$GATEWAY_PORT" "$GATEWAY_KEY_FILE" "$RUN")
echo "$ENDPOINT" > "$RUN/endpoint.json"
echo "$ENDPOINT" > "$CACHE_DIR/runs/latest-endpoint.json"
echo "gateway: http://$NODE_IP:$GATEWAY_PORT/v1 (key in $GATEWAY_KEY_FILE); models become ready one by one"

while true; do
  rc=0
  "${NODE_PY[@]}" status --plan "$PLAN" --run "$RUN" --gateway-port "$GATEWAY_PORT" \
    --key-file "$GATEWAY_KEY_FILE" --exit-code || rc=$?
  if (( rc == 3 )); then
    echo "$(date +%T) every model step has exited; ending the job"
    break
  fi
  if ! kill -0 "$GW_PID" 2>/dev/null; then
    echo "$(date +%T) agentgateway stopped; restarting (see logs/gateway.log)"
    start_gateway
  fi
  sleep 30 &
  wait $!
done
