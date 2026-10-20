#!/bin/bash
# Step 1: find out what a GPU node looks like from the inside, then serve a
# static web page for ~15 minutes so you can test login-node -> compute-node
# and laptop -> compute-node (tunnel) reachability without vLLM in the way.
#SBATCH --job-name=probe
#SBATCH --account=YOUR_PROJECT
#SBATCH --partition=boost_usr_prod
#SBATCH --qos=boost_qos_dbg
#SBATCH --nodes=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=120GB
#SBATCH --time=00:20:00
#SBATCH --output=%x-%j.out
set -uo pipefail
source "${SLURM_SUBMIT_DIR:-.}/common.sh"

section() { printf '\n===== %s =====\n' "$1"; }

section "identity"
echo "hostname: $(hostname) / short: $(hostname -s)"
echo "resolved IP of own hostname: $(node_ip)"
grep PRETTY_NAME /etc/os-release
uname -r

section "network interfaces"
ip -br addr
section "routes"
ip route
section "InfiniBand devices"
ls /sys/class/infiniband 2>/dev/null || echo "no /sys/class/infiniband"
for p in /sys/class/infiniband/*/ports/*; do
  [[ -e "$p/state" ]] && echo "$p: $(cat "$p/state") $(cat "$p/rate" 2>/dev/null)"
done
command -v ibv_devinfo >/dev/null && ibv_devinfo -l
ls -l /dev/infiniband 2>/dev/null | head
echo "locked-memory limit (RDMA wants unlimited): $(ulimit -l)"
lsmod 2>/dev/null | grep -E 'nvidia_peermem|nv_peer_mem' || echo "nvidia_peermem not listed (GPUDirect RDMA may be off)"

section "GPU"
nvidia-smi
nvidia-smi topo -m

section "container runtime"
"$CTR" --version
df -h /dev/shm /tmp | sed 1d

section "outbound internet (expect failure without proxy)"
curl -sS -m 8 -o /dev/null -w 'direct https://pypi.org -> %{http_code}\n' https://pypi.org || true
if [[ -n "${PROBE_PROXY:-}" ]]; then
  curl -sS -m 8 -x "$PROBE_PROXY" -o /dev/null -w 'via proxy https://pypi.org -> %{http_code}\n' https://pypi.org || true
fi

section "static web server"
PORT=$(pick_port)
claim_endpoint probe || exit 1
WWW="${BASE}/www-${SLURM_JOB_ID}"
mkdir -p "$WWW"
cat > "$WWW/index.html" <<EOF
<!doctype html><title>Leonardo probe</title>
<h1>Hello from $(hostname -s)</h1>
<p>Slurm job ${SLURM_JOB_ID}, port ${PORT}, started $(date -Is).</p>
EOF
trap 'remove_endpoint probe; rm -rf "$WWW"' EXIT

cd "$WWW" || exit 1
if command -v python3 >/dev/null; then
  python3 -m http.server "$PORT" --bind 0.0.0.0 &
elif [[ -s "$SIF" ]]; then
  "$CTR" exec --cleanenv --bind "$BASE" "$SIF" python3 -m http.server "$PORT" --bind 0.0.0.0 &
else
  echo "no python3 on host and no image yet"; exit 1
fi
SRV=$!
sleep 2
curl -sS -m 5 "http://127.0.0.1:${PORT}/" >/dev/null && echo "local check OK"
write_endpoint probe "$(hostname -s)" "$PORT"
cat <<EOF

Test it now:
  on a login node:  curl http://$(hostname -s):${PORT}/
  from your laptop: TEAM=${TEAM} LEO_USER=${USER} ./tunnel.sh probe   then open http://127.0.0.1:8000/
Server stays up for ~15 minutes.
EOF
sleep 900
kill "$SRV" 2>/dev/null
