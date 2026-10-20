#!/bin/bash
# Download the pinned chisel client (serve/catalog.json "tools") to $BASE/tools for the gateway's
# outbound tunnel. Only needed once the organisers have approved that route.
#SBATCH --job-name=install-chisel
#SBATCH --account=YOUR_PROJECT
#SBATCH --partition=lrd_all_serial
#SBATCH --cpus-per-task=1
#SBATCH --mem=2000M
#SBATCH --time=00:10:00
#SBATCH --output=%x-%j.out
set -euo pipefail
BASE="${BASE:-${WORK:?}/team_vllm}"
CATALOG="${SLURM_SUBMIT_DIR:?}/../serve/catalog.json"
read -r VERSION URL SHA_GZ SHA < <(python3 -c 'import json, sys
t = json.load(open(sys.argv[1]))["tools"]["chisel"]
print(t["version"], t["url"], t["sha256_gz"], t["sha256"])' "$CATALOG")
mkdir -p "$BASE/tools"
TMP=$(mktemp -d "$BASE/tools/.chisel-XXXXXX")
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/chisel.gz" ${HTTPS_PROXY:+-x "$HTTPS_PROXY"} "$URL"
printf '%s  %s\n' "$SHA_GZ" "$TMP/chisel.gz" | sha256sum -c -
gunzip "$TMP/chisel.gz"
printf '%s  %s\n' "$SHA" "$TMP/chisel" | sha256sum -c -
chmod 755 "$TMP/chisel"
"$TMP/chisel" --version
mv "$TMP/chisel" "$BASE/tools/chisel-$VERSION"
echo CHISEL_READY "$BASE/tools/chisel-$VERSION"
