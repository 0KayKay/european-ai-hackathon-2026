#!/bin/bash
# Proof of concept of the cluster link, end to end, in the Kubernetes cluster of your current kubectl context:
#
#   app request -> agentgateway (real config: extAuthz, guardrails, generated Leonardo routes)
#     -> Service leonardo-router -> chisel server pod (infrastructure/leonardo, unchanged)
#     <- reverse tunnel opened by the router job's chisel client ("Leonardo", simulated on this machine)
#     -> router (leonardo/serve/router.py, unchanged) -> model server (stand-in vLLM)
#
# The Leonardo side runs here with fake Slurm commands; the cluster side is the repository's manifests.
# Needs: kubectl, python3, curl, and the images leonardo-poc/stubs:1 and agentgateway available to the
# cluster (build-images.sh). Run from anywhere:  leonardo/tests/cluster_link_poc/run.sh
# KEEP=1 leaves the namespace and processes running for inspection. POC_IPV4_ONLY=1 binds agentgateway's
# admin ports to IPv4, for machines without IPv6 (agentgateway refuses to start there otherwise).
# POC_DIRECT=1 talks to the Service IPs directly instead of through kubectl port-forward (single-node
# clusters such as k3s, where the host routes to Services; port-forward there needs socat on the host).
# Behind an HTTP proxy, add the node, Pod and Service networks to NO_PROXY, for this script and for the
# cluster itself (k3s: 10.42.0.0/16, 10.43.0.0/16 and the node's address); kubectl logs fails otherwise.
set -euo pipefail
POC="$(cd "$(dirname "$0")" && pwd)"
LEONARDO="$(cd "$POC/../.." && pwd)"
NS=leonardo-poc
WORKDIR=$(mktemp -d)
CHISEL_VERSION=1.13.0
PIDS=()
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; FAILED=1; }
FAILED=0
cleanup() {
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    pkill -f "$WORKDIR/" 2>/dev/null || true
    if [[ "${KEEP:-0}" != 1 ]]; then kubectl delete namespace "$NS" --wait=false >/dev/null 2>&1 || true; rm -rf "$WORKDIR"; fi
}
trap cleanup EXIT

echo "== chisel client $CHISEL_VERSION (pinned in leonardo/serve/catalog.json)"
read -r URL SHA_GZ SHA < <(python3 -c 'import json, sys
t = json.load(open(sys.argv[1]))["tools"]["chisel"]; print(t["url"], t["sha256_gz"], t["sha256"])' "$LEONARDO/serve/catalog.json")
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/leonardo-poc"; mkdir -p "$CACHE"
CHISEL="$CACHE/chisel-$CHISEL_VERSION"
if [[ ! -x "$CHISEL" ]]; then
    curl -fsSL -o "$CHISEL.gz" "$URL" && echo "$SHA_GZ  $CHISEL.gz" | sha256sum -c - >/dev/null
    gunzip -f "$CHISEL.gz" && chmod 755 "$CHISEL"
fi
echo "$SHA  $CHISEL" | sha256sum -c - >/dev/null

echo "== secrets, as an operator would create them (see infrastructure/leonardo/README.md)"
kubectl apply -f "$POC/namespace.yaml" >/dev/null
INTERNAL_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
LINK_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
ROUTER_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
SERVER_KEY=$("$CHISEL" server --keygen - 2>/dev/null)
kubectl -n "$NS" delete secret gateway-internal github-oauth leonardo-link leonardo-router --ignore-not-found >/dev/null
kubectl -n "$NS" create secret generic gateway-internal --from-literal=key="$INTERNAL_KEY" >/dev/null
kubectl -n "$NS" create secret generic github-oauth --from-literal=auth_secret=x --from-literal=client_secret=x >/dev/null
kubectl -n "$NS" create secret generic leonardo-link --from-literal=server.key="$SERVER_KEY" \
    --from-literal=users.json="{\"leonardo:$LINK_PASSWORD\": [\"^R:0.0.0.0:9000\$\"]}" >/dev/null
kubectl -n "$NS" create secret generic leonardo-router --from-literal=token="$ROUTER_TOKEN" >/dev/null

echo "== manifests: infrastructure/base + infrastructure/leonardo"
kubectl kustomize "$POC" > "$WORKDIR/manifests.yaml"
if [[ "${POC_IPV4_ONLY:-0}" == 1 ]]; then
    python3 - "$WORKDIR/manifests.yaml" <<'PY'
import sys
path = sys.argv[1]
text = open(path).read()
marker = "    config:\n      logging:"
assert text.count(marker) == 1, "agentgateway config block not found"
text = text.replace(marker, "    config:\n      enableIpv6: false\n      adminAddr: 127.0.0.1:15000\n"
                    "      statsAddr: 127.0.0.1:15020\n      readinessAddr: 127.0.0.1:15021\n      logging:")
open(path, "w").write(text)
PY
fi
kubectl apply -f "$WORKDIR/manifests.yaml" >/dev/null
# Pods read these secrets when they start: on a rerun (KEEP=1 before), restart them to pick up the new ones.
kubectl -n "$NS" rollout restart deployment >/dev/null
for deployment in agentgateway auth backend leonardo-link; do
    kubectl -n "$NS" rollout status "deployment/$deployment" --timeout=180s >/dev/null
done
pass "cluster side deployed (agentgateway, auth and backend stubs, leonardo-link)"
for i in $(seq 30); do
    FINGERPRINT=$(kubectl -n "$NS" logs deploy/leonardo-link 2>/dev/null | sed -n 's/.*Fingerprint //p' | head -1)
    [[ -n "$FINGERPRINT" ]] && break; sleep 1
done
echo "   chisel server fingerprint: $FINGERPRINT"

echo "== the chisel server and agentgateway, reached as the public Ingress (/leonardo-link) and the backend would"
forward_link() {
    if [[ "${POC_DIRECT:-0}" == 1 ]]; then return; fi
    pkill -f "port-forward svc/leonardo-link" 2>/dev/null || true
    kubectl -n "$NS" port-forward svc/leonardo-link 18080:8080 >"$WORKDIR/pf-link.log" 2>&1 & PIDS+=($!)
    sleep 3
}
if [[ "${POC_DIRECT:-0}" == 1 ]]; then
    LINK="http://$(kubectl -n "$NS" get svc leonardo-link -o jsonpath='{.spec.clusterIP}'):8080"
    AGW="http://$(kubectl -n "$NS" get svc agentgateway -o jsonpath='{.spec.clusterIP}'):3000"
else
    LINK=http://127.0.0.1:18080 AGW=http://127.0.0.1:13000
    kubectl -n "$NS" port-forward svc/agentgateway 13000:3000 >"$WORKDIR/pf-agw.log" 2>&1 & PIDS+=($!)
    forward_link
fi
echo "   link server $LINK, agentgateway $AGW"

echo "== Leonardo side: team folder, cluster link settings, a ready model server, the router job"
mkdir -p "$WORKDIR"/{home,bin}
export HOME="$WORKDIR/home" PATH="$WORKDIR/bin:$PATH"
TEAM="python3 $LEONARDO/serve/team.py"
BASE="$WORKDIR/team"
cat > "$WORKDIR/bin/squeue" <<'SH'
#!/bin/bash
[[ -e "$HOME/router-started" ]] && printf '4242|leonardo-router|RUNNING|0:10|4:00:00|local\n'
printf '101|qwen06b-common|RUNNING|5:00|1:00:00|local\n'
SH
cat > "$WORKDIR/bin/sbatch" <<'SH'
#!/bin/bash
# The first submission really runs router.sbatch here as job 4242; its successor stays "pending".
if [[ ! -e "$HOME/router-started" ]]; then
    touch "$HOME/router-started"
    SLURM_JOB_ID=4242 SLURM_SUBMIT_DIR="$PWD" nohup bash "${@: -1}" > "$PWD/leonardo-router-4242.out" 2>&1 &
    echo 4242
else
    echo 4243
fi
SH
cat > "$WORKDIR/bin/scancel" <<'SH'
#!/bin/bash
pkill -f "/router/router.sbatch" || true; pkill -f "router.py serve" || true; rm -f "$HOME/router-started"
SH
chmod +x "$WORKDIR/bin/"*
$TEAM init --base "$BASE" --account POC >/dev/null
python3 - "$BASE/inference-site.json" "$FINGERPRINT" "$LINK" <<'PY'
import json, sys
path, fingerprint, link = sys.argv[1:]
site = json.load(open(path))
site["cluster_link"].update(enabled=True, server=link + "/leonardo-link", fingerprint=fingerprint)
json.dump(site, open(path, "w"), indent=2)
PY
cp "$CHISEL" "$BASE/tools/chisel-$CHISEL_VERSION"
echo "leonardo:$LINK_PASSWORD" | $TEAM router link-auth --base "$BASE" 2>/dev/null
echo "$ROUTER_TOKEN" | $TEAM router token --set --base "$BASE" 2>/dev/null
MINE="$BASE/users/$(id -un)"
RUN="$MINE/runs/qwen06b-poc"; mkdir -p -m 700 "$MINE/runs" "$RUN" "$RUN/.runtime"
VLLM_PORT=31777
python3 - "$LEONARDO" "$RUN" "$VLLM_PORT" <<'PY'
import json, sys
leonardo, run, port = sys.argv[1:]
profile = json.load(open(leonardo + "/serve/profiles/qwen06b.json"))
json.dump({"profile": profile}, open(run + "/RUN_CONFIG.json", "w"))
json.dump({"job_id": "101", "host": "127.0.0.1", "port": int(port), "model": profile["served_model"]},
          open(run + "/.runtime/endpoint.json", "w"))
PY
echo 101 > "$RUN/.runtime/job_id"; (umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$RUN/.runtime/api_key")
(cd "$RUN" && INFERENCE_API_PORT=$VLLM_PORT exec python3 "$LEONARDO/tests/fake_api.py") >"$WORKDIR/vllm.log" 2>&1 & PIDS+=($!)
$TEAM router up --base "$BASE" --hours 1 >/dev/null
for i in $(seq 60); do kubectl -n "$NS" logs deploy/leonardo-link 2>/dev/null | grep -q 'R:9000.*Listening' && break; sleep 1; done
if kubectl -n "$NS" logs deploy/leonardo-link | grep -q 'R:9000.*Listening'; then pass "router job opened the cluster link (reverse port 9000 in the chisel pod)"
else fail "cluster link did not open"; cat "$MINE/router/leonardo-router-4242.out"; exit 1; fi
link_state() {  # the router's view of the link, as team.py status shows it
    python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["cluster_link"]["state"])' "$MINE/router/.runtime/endpoint.json"
}
for i in $(seq 20); do [[ $(link_state) == connected ]] && break; sleep 0.5; done
$TEAM status --base "$BASE" | grep -q 'cluster link connected since' && pass "team.py status shows the link as connected" \
    || fail "status does not show the link: $($TEAM status --base "$BASE" | grep -i router)"

echo "== checks through agentgateway"
call() {  # call <expected status> <description> <curl args...>
    local expect=$1 what=$2; shift 2
    local status
    status=$(curl -sS -o "$WORKDIR/body" -w '%{http_code}' -m 60 "$AGW/v1/chat/completions" \
             -H 'Content-Type: application/json' "$@") || status=000
    if [[ "$status" == "$expect" ]]; then pass "$what ($status)"; else fail "$what: expected $expect, got $status: $(head -c 300 "$WORKDIR/body")"; fi
}
AUTH=(-H "Authorization: Bearer $INTERNAL_KEY")
CHAT='{"model":"qwen3-0.6b-leonardo","messages":[{"role":"user","content":"What is 17 + 25? Give only the number."}],"chat_template_kwargs":{"enable_thinking":false}}'
call 401 "agentgateway rejects a caller without the internal key" -d "$CHAT"
call 200 "chat completion reaches the Leonardo model" "${AUTH[@]}" -d "$CHAT"
grep -q '"42"' "$WORKDIR/body" && pass "answer came back from the model server" || fail "unexpected answer: $(cat "$WORKDIR/body")"
call 200 "streaming" "${AUTH[@]}" -N -d '{"model":"qwen3-0.6b-leonardo","stream":true,"messages":[{"role":"user","content":"Say hello in one short sentence."}],"chat_template_kwargs":{"enable_thinking":false}}'
grep -q '\[DONE\]' "$WORKDIR/body" && pass "stream ended with [DONE]" || fail "stream incomplete"
call 403 "the app's guardrail webhook also guards Leonardo models" "${AUTH[@]}" -H 'x-challenge-difficulty: HARD' \
    -d '{"model":"qwen3-0.6b-leonardo","messages":[{"role":"user","content":"BLOCKME please"}],"chat_template_kwargs":{"enable_thinking":false}}'
status=$(curl -sS -o /dev/null -w '%{http_code}' -m 10 "$LINK/leonardo-link/v1/models") || true
[[ "$status" != 200 ]] && pass "the public link endpoint serves no API ($status)" || fail "public link endpoint answered 200"

echo "== resilience"
kubectl -n "$NS" delete pod -l app=leonardo-link --wait=false >/dev/null
kubectl -n "$NS" rollout status deployment/leonardo-link --timeout=120s >/dev/null
forward_link  # a port-forward ends with its pod
START=$(date +%s); OK=0
for i in $(seq 90); do
    s=$(curl -sS -o /dev/null -w '%{http_code}' -m 10 "$AGW/v1/chat/completions" -H 'Content-Type: application/json' "${AUTH[@]}" -d "$CHAT" || true)
    [[ "$s" == 200 ]] && { OK=1; break; }; sleep 2
done
[[ $OK == 1 ]] && pass "link came back by itself after the chisel pod was replaced ($(( $(date +%s) - START )) s)" || fail "link did not recover"
grep -q 'client: Disconnected' "$MINE/router/leonardo-router-4242.out" && [[ $(link_state) == connected ]] \
    && pass "the router saw the outage and reports the link connected again" || fail "router link state: $(link_state)"
$TEAM router down --base "$BASE" >/dev/null 2>&1 || true
sleep 3
call 503 "with the router stopped, agentgateway fails fast instead of hanging" "${AUTH[@]}" -d "$CHAT"

echo
echo "Logs: router $MINE/router/leonardo-router-4242.out; cluster: kubectl -n $NS logs deploy/agentgateway | deploy/leonardo-link"
[[ $FAILED == 0 ]] && echo "ALL CHECKS PASSED" || { echo "SOME CHECKS FAILED"; exit 1; }
