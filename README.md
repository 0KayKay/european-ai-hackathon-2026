# European AI Hackathon

Local stack: React/Vite served by Caddy → agentgateway → FastAPI, with Better Auth and PostgreSQL 18.
Terraform for GKE is in `infrastructure/terraform/gke`; the local Tilt stack remains separate.
Open-weight models (Qwen3-235B, GLM-5.3, DeepSeek-V4.1) run on CINECA's Leonardo; see
[leonardo/README.md](leonardo/README.md).

## Prerequisites

- Docker with a local Kubernetes cluster (Kind or Docker Desktop Kubernetes)
- `kubectl`, [Tilt](https://docs.tilt.dev/install.html), and [just](https://just.systems/man/en/)

For Kind, install `kind`, run `kind create cluster --name hackathon` once, then select it with
`kubectl config use-context kind-hackathon`.
For Docker Desktop Kubernetes, select `docker-desktop` instead.
Tilt refuses unknown or remote contexts by default.

For running the backend outside Tilt, copy `backend/.env.example` to `backend/.env`. It connects to agentgateway at
`localhost:4000` through the Tilt port forward.
Set `AGENTGATEWAY_API_KEY` in that private file to the value of the `gateway-internal` Secret's `key` field.

!!Important: for local development this setup currently only supports gemini-3.8-flash which is set up in
`infrastructure/base/agentgateway.yaml`. This uses you local auth, so you need to sign in to
`european-ai-hackathon-2026` and have the required permissions to invoke the model. This will change later to use a
model from the Leonardo cluster ([leonardo/](leonardo/README.md)).

## GitHub sign-in

The GitHub OAuth App client ID is configured in `infrastructure/base/local.yaml`. Set its homepage to
`http://localhost:5000` and callback URL to `http://localhost:5000/api/auth/callback/github`. Store its client secret,
plus a random Better Auth secret of at least 32 characters, in a private env file outside this repository:

```text
client_secret=YOUR_GITHUB_CLIENT_SECRET
auth_secret=YOUR_RANDOM_32_CHARACTER_SECRET
```

Keep the values unquoted; `kubectl --from-env-file` stores quote characters literally.

Create the Kubernetes Secret before starting Tilt:

```sh
kubectl create secret generic github-oauth --from-env-file=/path/to/private/github-oauth.env
kubectl create secret generic gateway-internal --from-literal=key="$(openssl rand -hex 32)"
```

The active frontend uses Better Auth's same-origin, HttpOnly session cookie. The auth service stores users and sessions
in the local Postgres database and runs its schema migrations at startup. `ALLOWED_EMAILS` in
`infrastructure/base/local.yaml` admits verified GitHub email addresses at `codify.ch`, `lapiscode.com`, and
`calenora.com`; it also accepts comma-separated exact addresses and `*@domain` entries. An empty list denies everyone.
The auth service checks the list on sign-in and on protected requests, including existing sessions. FastAPI and
agentgateway verify browser sessions through the auth service. Backend model calls use the separate `gateway-internal`
credential. `/api/health` remains public on the backend for readiness checks. To run the frontend through Vite, the auth
service is forwarded on `localhost:3005`; the browser still uses the Vite `/api/auth` proxy.

Vertex AI credentials belong on agentgateway. For local Kind, create the Secret from your Google Application Default
Credentials file, then restart the gateway:

```sh
kubectl create secret generic agentgateway-google-credentials --from-file=credentials.json="$HOME/.config/gcloud/application_default_credentials.json"
kubectl rollout restart deployment/agentgateway
```

The local cluster stores this credential file in a Kubernetes Secret. `just down` leaves that manually created Secret in
place. On GKE, use Workload Identity instead.

## Run

```sh
just up       # opens Tilt; app at http://localhost:5000
just status   # Kubernetes resources
just down     # removes services; retains the Postgres volume
```

`http://localhost:5001/api/health` checks backend readiness without sign-in; the gateway requires a session for
`/api/*`.
`just check` runs a one-shot Tilt deployment check.
The database credentials in `infrastructure/base/local.yaml` are for local development only.

### DeepTeam scans

After sign-in, use **Red team** in the frontend to start a scan. Enter a goal and AG-UI target URL. The default target
is the backend's `/api/v1/ag-ui` endpoint. The same-origin `/api/deepteam/runs` API verifies your Better Auth session
and forwards it to the backend target. The service accepts only target hosts listed in `ALLOWED_TARGET_HOSTS` in
`infrastructure/base/local.yaml` (default: `backend`), and is reachable externally only through the frontend proxy. To
call the API directly, send your session cookie:

```sh
curl http://localhost:5000/api/deepteam/runs \
  -H 'Content-Type: application/json' \
  -H 'Cookie: <your signed-in browser Cookie header>' \
  -d '{"goal":"Keep the assistant from leaking its instructions","target_url":"http://backend:8000/api/v1/ag-ui"}'
```

The default target state selects the frontend's default model in playground mode. Supply `target_state` for another
AG-UI service or model, and optionally set `model`, `vulnerabilities`, and `attacks_per_vulnerability_type`. Edit
`deepteam/hackathon_deepteam/runner.py` to change DeepTeam scenarios; `agui_target.py` only maps AG-UI messages and
usually needs no changes. The agentgateway supplies DeepTeam's simulator and evaluator model. The response can take
several minutes; keep the HTTP client connection open.

Follow scan progress with `kubectl logs -f deployment/deepteam`. DeepTeam emits JSON events for run
start/completion/failure, each AG-UI attempt and its response/error, and every scored test case. Events include the run
and user IDs, timing, scores, and short prompt/response/reason previews. They never include request headers or
credentials. Set `LOG_LEVEL` to `debug`, `info` (default), `warning`, or `error` to filter events. Treat the logs as
sensitive because attack prompts and target response previews can contain private content.

Phoenix is available at `http://localhost:6006` through the authenticated frontend proxy. The backend exports agent and
model spans to the `hackathon-2026` Phoenix project over OTLP/HTTP. Phoenix stores traces and message content in the
`phoenix` schema of the existing local PostgreSQL database; its tables are separate from the app's tables. The gateway
configuration lives in [infrastructure/base/agentgateway.yaml](infrastructure/base/agentgateway.yaml) and Kustomize
packages it into a ConfigMap for Tilt.
Backend agent traces use the AG-UI `threadId` as Phoenix `session.id` and the verified Better Auth user ID as `user.id`,
including on nested agent and model spans.

## GKE

The [Terraform stack](infrastructure/terraform/gke) creates a GKE Autopilot cluster in `europe-west1` with private
nodes, an Artifact Registry repository, a reserved public IP for the frontend, and a **separate static outbound IP** for
Cloud NAT. Agentgateway uses Workload Identity to reach Vertex AI. The outbound IP is shared by workloads using that
NAT; the future custom LLM can allowlist it without changing the gateway model configuration yet.

Terraform creates cloud infrastructure; the [GKE Kustomize overlay](infrastructure/gke) deploys the application. It
removes the local development database Secret and the gateway credentials file. All six Kubernetes Services stay
`ClusterIP`. The only public Ingress routes to the frontend Caddy service, which proxies authenticated API traffic.
Phoenix has no public route.

```sh
terraform -chdir=infrastructure/terraform/gke init
terraform -chdir=infrastructure/terraform/gke plan
terraform -chdir=infrastructure/terraform/gke apply
terraform -chdir=infrastructure/terraform/gke output frontend_ip
terraform -chdir=infrastructure/terraform/gke output agentgateway_egress_ip
gcloud container clusters get-credentials hackathon-2026 --region europe-west1 --project european-ai-hackathon-2026
```

Create fresh Secrets in the GKE cluster; use the same private `github-oauth.env` format described above. These values
stay out of Terraform state and the repository.

```sh
kubectl create secret generic github-oauth --from-env-file=/path/to/private/github-oauth.env
kubectl create secret generic gateway-internal --from-literal=key="$(openssl rand -hex 32)"
kubectl create secret generic local-db --from-literal=password="$(openssl rand -hex 32)"
```

Build and push the four application images, then apply the overlay:

```sh
gcloud auth configure-docker europe-west1-docker.pkg.dev
REPO=europe-west1-docker.pkg.dev/european-ai-hackathon-2026/hackathon-2026
docker buildx build --platform linux/amd64 -t "$REPO/backend:v1" --push backend
docker buildx build --platform linux/amd64 -t "$REPO/deepteam:v1" --push deepteam
docker buildx build --platform linux/amd64 -t "$REPO/auth:v1" --push -f frontend/Dockerfile.auth frontend
docker buildx build --platform linux/amd64 -t "$REPO/frontend:v1" --push frontend
kubectl apply -k infrastructure/gke
```

The reserved `frontend_ip` needs an A record for `european-ai-hackathon-2026.codify.ch` before its managed HTTPS
certificate activates. Then add `https://european-ai-hackathon-2026.codify.ch/api/auth/callback/github` to the GitHub
OAuth App's callback URLs. GKE does not supply a dynamic HTTPS hostname suitable for this OAuth flow. The GKE frontend
will be usable after DNS and the certificate are ready; use local Tilt for previews in the meantime.

### DeepTeam

You can access it `http://localhost:5002/docs#/default/start_run_api_deepteam_runs_post once your cluster runs. Logs are available here:
http://localhost:10350/r/deepteam/overview 


