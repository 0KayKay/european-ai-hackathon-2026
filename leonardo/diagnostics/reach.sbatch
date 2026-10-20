#!/bin/bash
# Answers the two questions the gateway depends on, from the queue it runs in (lrd_all_serial):
#   1. Can this node reach a port on a GPU node?  TARGET=lrdnXXXX:PORT (start probe.sbatch first)
#   2. Can it reach the internet, directly or via a proxy?  PROBE_PROXY=http://host:port (optional)
#      TUNNEL_URL=https://.../leonardo-tunnel (optional): the Kubernetes tunnel endpoint's /health
# Submit from this folder:  TARGET=lrdn1234:31234 sbatch reach.sbatch
#SBATCH --job-name=reach
#SBATCH --account=YOUR_PROJECT
#SBATCH --partition=lrd_all_serial
#SBATCH --cpus-per-task=1
#SBATCH --mem=2000M
#SBATCH --time=00:10:00
#SBATCH --output=%x-%j.out
set -uo pipefail
echo "node: $(hostname -s)"
if [[ -n "${TARGET:-}" ]]; then
  curl -sS -m 10 -o /dev/null -w "GPU node http://${TARGET}/ -> %{http_code}\n" "http://${TARGET}/" || echo "GPU node ${TARGET}: UNREACHABLE"
fi
curl -sS -m 10 -o /dev/null -w 'direct https://pypi.org -> %{http_code}\n' https://pypi.org || echo 'direct internet: NO'
if [[ -n "${PROBE_PROXY:-}" ]]; then
  curl -sS -m 10 -x "$PROBE_PROXY" -o /dev/null -w 'via proxy https://pypi.org -> %{http_code}\n' https://pypi.org || echo 'via proxy: NO'
fi
if [[ -n "${TUNNEL_URL:-}" ]]; then
  curl -sS -m 10 ${PROBE_PROXY:+-x "$PROBE_PROXY"} -w ' <- tunnel server /health %{http_code}\n' "${TUNNEL_URL%/}/health" || echo 'tunnel server: UNREACHABLE'
fi
