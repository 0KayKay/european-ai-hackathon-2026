#!/bin/bash
# Answers the two questions the router depends on, from the CPU queue it runs in:
#   1. Can this node reach a port on a GPU node?  TARGET=lrdnXXXX:PORT (start a probe first)
#   2. Can it reach the internet, directly or via a proxy?  PROBE_PROXY=http://host:port (optional)
#      LINK_URL=https://.../leonardo-link (optional): the cluster link server in Kubernetes, through the same
#      proxy if one is given
# Submitted by `cli.py diagnose reach --target ... [--proxy ...] [--link-url ...]`. By hand:
#   TARGET=lrdnXXXX:PORT sbatch -A <account> leonardo/diagnostics/reach.sbatch
#SBATCH --job-name=reach
#SBATCH --partition=lrd_all_serial
#SBATCH --output=%x-%j.out
#SBATCH --cpus-per-task=1
#SBATCH --mem=2000M
#SBATCH --time=00:10:00
set -uo pipefail
echo "node: $(hostname -s)"
if [[ -n "${TARGET:-}" ]]; then
  curl -sS -m 10 -o /dev/null -w "GPU node http://${TARGET}/ -> %{http_code}\n" "http://${TARGET}/" || echo "GPU node ${TARGET}: UNREACHABLE"
fi
curl -sS -m 10 -o /dev/null -w 'direct https://pypi.org -> %{http_code}\n' https://pypi.org || echo 'direct internet: NO'
if [[ -n "${PROBE_PROXY:-}" ]]; then
  curl -sS -m 10 -x "$PROBE_PROXY" -o /dev/null -w 'via proxy https://pypi.org -> %{http_code}\n' https://pypi.org || echo 'via proxy: NO'
fi
if [[ -n "${LINK_URL:-}" ]]; then
  # The WebSocket handshake the chisel client sends: the chisel server answers 101 on any path and keeps the
  # connection open (curl then times out, as expected). /health is no use here: chisel serves it only at its
  # root, which the Ingress does not route to it.
  CODE=$(curl -sS -m 10 -o /dev/null -w '%{http_code}' ${PROBE_PROXY:+-x "$PROBE_PROXY"} -H 'Connection: Upgrade' \
         -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
         -H 'Sec-WebSocket-Protocol: chisel-v3' "$LINK_URL" 2>/dev/null)
  if [[ "$CODE" == 101 ]]; then echo "cluster link server $LINK_URL -> 101: REACHABLE"
  else echo "cluster link server $LINK_URL -> ${CODE:-000}: NOT the link server (101 expected; 000 = no connection)"; fi
fi
