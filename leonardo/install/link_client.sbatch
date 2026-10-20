#!/bin/bash
# Download the pinned chisel client (serve/catalog.json "tools") to $BASE/tools. The router uses it
# for the cluster link, which stays off until the organisers approve it.
# Submitted by `cli.py install --link-client` on the CPU queue, which has internet.
#SBATCH --cpus-per-task=1
#SBATCH --mem=2000M
#SBATCH --time=00:10:00
set -euo pipefail
BASE="${BASE:?}"
RELEASE="${LEONARDO_RELEASE:?submit with cli.py install, or set LEONARDO_RELEASE to the leonardo/ folder}"
read -r VERSION URL SHA_GZ SHA < <(python3 -c 'import json, sys
t = json.load(open(sys.argv[1]))["tools"]["chisel"]
print(t["version"], t["url"], t["sha256_gz"], t["sha256"])' "$RELEASE/serve/catalog.json")
mkdir -p "$BASE/tools"
TMP=$(mktemp -d "$BASE/tools/.chisel-XXXXXX")
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/chisel.gz" "$URL"
printf '%s  %s\n' "$SHA_GZ" "$TMP/chisel.gz" | sha256sum -c -
gunzip "$TMP/chisel.gz"
printf '%s  %s\n' "$SHA" "$TMP/chisel" | sha256sum -c -
chmod 755 "$TMP/chisel"
"$TMP/chisel" --version
mv "$TMP/chisel" "$BASE/tools/chisel-$VERSION"
echo "LINK_CLIENT_READY $BASE/tools/chisel-$VERSION"
