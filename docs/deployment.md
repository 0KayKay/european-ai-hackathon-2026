# Deploy the hackathon app

Infrastructure and Secrets are prepared manually. Application deployments run through
[Deploy to GKE](../.github/workflows/deploy-gke.yml) when you push to `main`, or when you
manually run that workflow on `main`. Do not apply the application overlay directly with
its placeholder `v1` image tags.

## 1. Select the correct project

The existing cluster, registry, DNS and managed certificate can be reused. For a new
cluster, first apply [the Terraform stack](../infrastructure/terraform/gke) and configure
the DNS and GitHub OAuth callback described in the [README](../README.md#gke).

```sh
export HACKATHON_PROJECT=european-ai-hackathon-2026
export HACKATHON_CONTEXT=gke_european-ai-hackathon-2026_europe-west1_hackathon-2026
gcloud container clusters get-credentials hackathon-2026 \
  --region europe-west1 --project "$HACKATHON_PROJECT" --billing-project "$HACKATHON_PROJECT"
```

Use this explicit context for every command below. Existing `github-oauth`, `gateway-internal`
and `local-db` Secrets must be retained: `local-db` holds the running database password,
despite its historical name. Changing that Secret alone does not change the database password.
Confirm the GitHub OAuth callback is
`https://european-ai-hackathon-2026.codify.ch/api/auth/callback/github`, and review the
`ALLOWED_EMAILS` list in `infrastructure/base/local.yaml` for your participants.

Allow GitHub Actions to manage the new NetworkPolicies. Run this once as a cluster administrator:

```sh
kubectl --context "$HACKATHON_CONTEXT" apply -f infrastructure/gke/github-deployer-rbac.yaml
```

## 2. Bootstrap Phoenix authentication before pushing

Phoenix remains private. Its GKE image is pinned to the previously running version,
20.26.1. Native authentication adds a separate Phoenix login; the app's GitHub login
does not grant Phoenix access. The NetworkPolicy permits only backend Pods in the
same namespace to reach Phoenix on HTTP/OTLP port 6006; operators use a localhost tunnel.
The gRPC collector port is not permitted by this policy because this app uses HTTP.

Enabling authentication briefly interrupts trace collection from the old backend until
the new authenticated collector is deployed. Existing application and trace data is retained.

Create the authentication Secret once. Keep these values in your password manager;
do not regenerate them on each deployment or commit them. If this Secret already exists,
reuse it and skip generation.

```sh
umask 077
export HACKATHON_PRIVATE="$(mktemp -d)"
python3 - <<'PY'
import os
import secrets
from pathlib import Path

folder = Path(os.environ['HACKATHON_PRIVATE'])
(folder / 'phoenix-auth.env').write_text(
    'PHOENIX_SECRET=a1' + secrets.token_hex(32) + '\n'
    'PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD=Phoenix-a1-' + secrets.token_hex(32) + '\n'
)
print('Private credentials saved in', folder / 'phoenix-auth.env')
PY
kubectl --context "$HACKATHON_CONTEXT" -n default create secret generic phoenix-auth \
  --from-env-file="$HACKATHON_PRIVATE/phoenix-auth.env"
kubectl --context "$HACKATHON_CONTEXT" -n default apply \
  -f infrastructure/gke/phoenix-network-policy.yaml
kubectl --context "$HACKATHON_CONTEXT" -n default patch deployment phoenix \
  --type=strategic --patch-file infrastructure/gke/phoenix.yaml
kubectl --context "$HACKATHON_CONTEXT" -n default rollout status deployment/phoenix
kubectl --context "$HACKATHON_CONTEXT" -n default port-forward --address 127.0.0.1 \
  service/phoenix 16006:6006
```

Open **http://127.0.0.1:16006**, log in as `admin@localhost` with the generated initial
password, and create a **system API key** for backend trace ingestion in Phoenix settings.
The initial-password variable is used only when the admin account is first created;
if an admin account already exists, use its current password or Phoenix's password-reset flow.
For this cluster's completed bootstrap, the built-in default password was replaced with
the strong password stored in `phoenix-auth`. Retrieve it locally when needed:

```sh
kubectl --context "$HACKATHON_CONTEXT" -n default get secret phoenix-auth \
  -o jsonpath='{.data.PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD}' | base64 --decode
```

The collector key is already stored in `phoenix-collector`; do not recreate either Secret
when continuing deployment on this cluster.

In another terminal, set `HACKATHON_CONTEXT` and `HACKATHON_PRIVATE` to the values above,
then save that API key without putting it in shell history:

```sh
python3 - <<'PY'
import os
from getpass import getpass
from pathlib import Path

key = getpass('Phoenix system API key: ').strip()
if not key:
    raise SystemExit('API key must not be empty')
path = Path(os.environ['HACKATHON_PRIVATE']) / 'phoenix-collector-key'
path.write_text(key)
path.chmod(0o600)
PY
kubectl --context "$HACKATHON_CONTEXT" -n default create secret generic phoenix-collector \
  --from-file=api-key="$HACKATHON_PRIVATE/phoenix-collector-key"
```

Use a collector key rather than sharing the administrator password or `PHOENIX_SECRET`
with the backend. The GKE backend requires this Secret; deployment fails if it is absent.
The exporter explicitly sends `Authorization: Bearer <key>` to Phoenix. Local Tilt keeps
its existing unauthenticated Phoenix configuration and does not require these Secrets.

The browser origin is restricted to `http://127.0.0.1:16006` and `http://localhost:16006`.
Use this port for the tunnel. It binds only to your computer and requires GKE port-forward
permission. Do not publish Phoenix or bind this tunnel to `0.0.0.0`. GKE Autopilot enforces
NetworkPolicies; this policy protects Phoenix ingress, not every service in the cluster.

## 3. Push and deploy through GitHub Actions

Check the repository variable `GCP_WORKLOAD_IDENTITY_PROVIDER` is the existing provider:

```text
projects/363640862423/locations/global/workloadIdentityPools/hackathon-github/providers/github
```

Leave `LEONARDO_ENABLED` unset or `false` while CINECA approval is pending. Push the tested
changes to `main`; GitHub Actions builds four images tagged with that commit SHA, applies
the selected overlay, and waits for all application rollouts. The frontend load-balancer
BackendConfig now allows **900 seconds**. Challenge runs have a **300-second** deadline,
and individual HARD guardrail checks have a **30-second** deadline and fail closed.
DeepTeam's target client allows 330 seconds per challenge attempt; an entire scan can
still exceed 900 seconds if sufficiently large. Smaller scans should be used until scans
are moved to background jobs.

Gemini 3 agents and the guardrail judge request `low` reasoning. Gateway 1.6 omits Gemini's
thinking configuration for `none`, so the earlier `thinking=False` did not actually select
a lower level. The judge also requests a small structured decision and does not perform
SDK retries. No challenge guardrail, completion condition, or call limit is removed.

After the workflow finishes:

```sh
kubectl --context "$HACKATHON_CONTEXT" -n default get deployments,pods
kubectl --context "$HACKATHON_CONTEXT" -n default get deployment backend \
  -o jsonpath='{.spec.template.spec.containers[0].image}'
kubectl --context "$HACKATHON_CONTEXT" -n default get backendconfig frontend \
  -o jsonpath='{.spec.timeoutSec}'
gcloud compute backend-services describe k8s1-6f63e394-default-frontend-80-7e755bb5 \
  --global --project "$HACKATHON_PROJECT" --billing-project "$HACKATHON_PROJECT" \
  --format='value(timeoutSec)'
```

The backend image tag must match the pushed commit and both timeouts must be 900. The
generated backend-service name above belongs to the existing cluster; after recreating
the Ingress, find its replacement in the Ingress's `ingress.kubernetes.io/backends` annotation.
Allow time for the load-balancer controller to reconcile the BackendConfig.

Open **https://european-ai-hackathon-2026.codify.ch**, sign in, and run each challenge at
each difficulty. Reload saved attempts and confirm the results remain. Run a small DeepTeam
scan. In the private Phoenix tunnel, verify a fresh trace appears in `hackathon-2026`,
and an unauthenticated browser shows the Phoenix login. Test a request lasting over
two minutes if one is available. Healthy Pods alone do not prove authentication,
trace export, or the end-to-end timeout setting.

## 4. Activate Leonardo only after CINECA approval

No Leonardo connection is enabled or started by these changes. The prepared
[gke-leonardo overlay](../infrastructure/gke-leonardo/kustomization.yaml) adds the link,
model routes and a NetworkPolicy. Its public WebSocket accepts only the configured
Chisel user, restricted to one reverse port; the model-router port is available only
to agentgateway Pods. The router additionally requires its bearer token, and the
Leonardo client pins the Chisel server fingerprint.

After approval, configure your own `LEONARDO_USER`, `LEONARDO_BASE`, and, if needed,
`LEONARDO_HOST`, as described in [the Leonardo guide](../leonardo/README.md#installation).
Do not share personal CINECA credentials. Prepare the model assets and router with that CLI.

On your laptop, generate the link credentials into private files:

```sh
umask 077
export HACKATHON_LINK_PRIVATE="$(mktemp -d)"
docker run --rm \
  jpillora/chisel:1.13.0@sha256:e3e1f6e3563a643ff2c484e110685144c4a24b8fe5711cb7a0d236a4e3080d97 \
  server --keygen - > "$HACKATHON_LINK_PRIVATE/server.key"
python3 - <<'PY'
import json
import os
import secrets
from pathlib import Path

folder = Path(os.environ['HACKATHON_LINK_PRIVATE'])
auth = 'leonardo:' + secrets.token_hex(32)
(folder / 'users.json').write_text(json.dumps({auth: ['^R:0.0.0.0:9000$']}))
(folder / 'link_auth').write_text(auth + '\n')
PY
./leonardo/cli.py router up --hours 12
./leonardo/cli.py router token > "$HACKATHON_LINK_PRIVATE/router-token"
kubectl --context "$HACKATHON_CONTEXT" -n default create secret generic leonardo-link \
  --from-file=server.key="$HACKATHON_LINK_PRIVATE/server.key" \
  --from-file=users.json="$HACKATHON_LINK_PRIVATE/users.json"
kubectl --context "$HACKATHON_CONTEXT" -n default create secret generic leonardo-router \
  --from-file=token="$HACKATHON_LINK_PRIVATE/router-token"
```

The first router start initializes its token while the site's cluster link is still off.
Verify the token file is nonempty before creating its Secret.

Set the GitHub repository variable **`LEONARDO_ENABLED=true`**, then manually run
**Deploy to GKE on `main`** (or push to `main`). Do this only after creating both Secrets.
After rollout, read the fingerprint:

```sh
kubectl --context "$HACKATHON_CONTEXT" -n default logs deployment/leonardo-link
```

In the team's `$BASE/inference-site.json` on Leonardo, set:

```json
"cluster_link": {
  "enabled": true,
  "server": "https://european-ai-hackathon-2026.codify.ch/leonardo-link",
  "fingerprint": "COPY_THE_SERVER_FINGERPRINT",
  "proxy": null,
  "remote_port": 9000
}
```

Use only the CINECA-approved proxy if one is required. Install the client and restart
the router with the private link credentials:

```sh
./leonardo/cli.py install --link-client
# Wait for the install job to finish successfully before starting the router.
./leonardo/cli.py router up --restart --hours 12 \
  --link-auth-file "$HACKATHON_LINK_PRIVATE/link_auth"
./leonardo/cli.py up qwen06b --qos boost_qos_dbg --time 00:30:00
./leonardo/cli.py status
./leonardo/cli.py logs router
```

Once the model is `READY` and the tunnel is connected, set the app's model field to
`qwen3-0.6b-leonardo` for a short connectivity test. For real challenge testing, start a
larger tool-capable profile and use its exact "Served as" name from the Leonardo guide.
The current large-model profiles have 2,048-token contexts: complex challenge prompts
and tool history can exceed this, so successful connectivity does not establish
challenge compatibility. Increase and GPU-validate profile context limits before using
those profiles for the full challenge suite. Guardrail judging continues through Gemini.

Router jobs renew periodically, model jobs expire, and the debug model above expires
after 30 minutes. Plan reservations and production job durations for the event; GKE
does not start or renew GPU model jobs. To disable the connection, first turn off the
site's `cluster_link.enabled` and restart its router, set the GitHub variable to `false`
and deploy again. Kustomize does not prune the previously created link resources: after
that deployment, manually remove the `leonardo-link` Deployment/BackendConfig/NetworkPolicy
and `leonardo-link`/`leonardo-router` Services if they are no longer needed.
