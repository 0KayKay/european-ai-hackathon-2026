#!/bin/bash
# Build the images run.sh needs into the local Docker image store (k3s with --docker, or `kind load` them).
#   leonardo-poc/stubs:1                    the auth and backend stand-ins (stubs/)
#   cr.agentgateway.dev/agentgateway:v1.6.0 only with AGENTGATEWAY_FROM_BINARY=1: built from the official
#                                           release binary, for machines that cannot pull from that registry
set -euo pipefail
POC="$(cd "$(dirname "$0")" && pwd)"
docker build -q -t leonardo-poc/stubs:1 "$POC/stubs"
if [[ "${AGENTGATEWAY_FROM_BINARY:-0}" == 1 ]]; then
    DIR=$(mktemp -d); trap 'rm -rf "$DIR"' EXIT
    URL=https://github.com/agentgateway/agentgateway/releases/download/v1.6.0/agentgateway-linux-amd64
    curl -fsSL -o "$DIR/agentgateway" "$URL"
    echo "496ed27e6a96b55a9ad7cabe5c2be7172511f44a3cb113e533d22bf93a7f079d  $DIR/agentgateway" | sha256sum -c -
    chmod 755 "$DIR/agentgateway"
    # Plain HTTP inside the cluster only, so the image needs no CA certificates.
    printf 'FROM debian:bookworm-slim\nCOPY agentgateway /app/agentgateway\nENTRYPOINT ["/app/agentgateway"]\n' > "$DIR/Dockerfile"
    docker build -q -t cr.agentgateway.dev/agentgateway:v1.6.0 "$DIR"
fi
