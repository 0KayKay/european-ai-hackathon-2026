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

In local mode, backend and DeepTeam use `local-user` only for requests without a session cookie.
Signed-in requests keep their verified user ID, including in guardrail logs. Earlier challenge attempts
saved under `local-user` remain under that identity and do not appear in the signed-in user's dashboard.

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

### Gateway guardrail webhooks

The `guardrail` FastAPI service implements `POST /request` and `POST /response` from the
[guardrail webhook specification](https://raw.githubusercontent.com/solo-io/gloo-gateway-use-cases/refs/heads/main/ai-guardrail-webhook-server/docs/gloo-ai-gateway-guardrail-webhook-openapi.yaml).
The global LLM policy calls both hooks for every model, including Leonardo and streaming responses.
Valid payloads return a pass action unchanged; this service currently provides observation only.
Gateway webhook failures use the default fail-closed behavior, so guardrail must be healthy for model calls.

Inspect events with `kubectl logs -f deployment/guardrail`. Each event includes the phase, user and model,
request/correlation IDs, received headers, message roles, counts, and content sizes. Prompt and response text
is omitted; authorization, cookies, API keys, tokens, secrets, and passwords in header names are redacted.
Backend agents, the challenge judge, and DeepTeam attach user/model context to their model requests.
User metadata in these logs is supplied by internal callers, not independently authenticated by guardrail.
Additional headers may contain sensitive context, so restrict access to these logs.

Tilt forwards the service to `http://localhost:5003`; `/api/health` checks readiness and `/docs` shows its API.
Outside Tilt, run `cd guardrail && uv sync --locked && uv run uvicorn hackathon_guardrail.main:app --port 5003`.
The deployed service stays internal to the cluster. Set `LOG_FORMAT=json` for structured logs and
`LOG_LEVEL` to select verbosity, using the same logging and error-handling base as the backend.

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

## Guardrail Arena

The arena evaluates the prompt-injection levels automatically: an attacker generates attacks against a level, a
benign-case generator produces normal requests, every case runs to completion, and the results show how many
attacks got through and how many legitimate requests the guardrails wrongly blocked. Specification, contracts and
status are in [docs/spec/config-driven-arena/](docs/spec/config-driven-arena/SPEC.md); the manual test checklist is
[TESTING.md](docs/spec/config-driven-arena/TESTING.md).

- **Levels are config.** Each level × difficulty is described in [backend/levels/](backend/levels/README.md)
  (agent, tools and data, secret, guardrails and their positions, success check, red and green team, a model and
  parameters per LLM role). Edit the files and restart the backend; the UI is generated from them. Levels 1–3
  reproduce the legacy challenges; `hidden-mailbox-output-judge` is an example of a new level without code.
- **Where.** After sign-in, **Arena** in the playground header (`#/arena`). Pick a level and difficulty to see the
  config source, the execution graph and a node inspector with prompts and models. **Run evaluation** starts a
  run; the run page shows progress, live counts, a live sample of the running case and the generated inputs; once
  finished it shows attack success rate, false-positive rate, breakdowns and a filterable case list. Every case
  opens a step-by-step replay on the graph. Runs can be stopped and exported as JSON.
- **Runs and datasets.** Red cases come from the deepteam service (single-turn types are generated up front;
  multi-turn types are driven turn by turn through an internal backend callback). Green cases come from the
  level's generator model. The single-turn cases of a run (indirect red cases, all green cases) are stored as
  datasets; a new run can reuse a stored dataset of the same level unchanged to compare guardrail changes on the
  same data. Runs, cases with all events and datasets are stored in Postgres and survive reloads and restarts
  (a run that was active when the backend stopped is marked *interrupted*).
- **Settings** (backend; set in `infrastructure/base/local.yaml`, see `backend/.env.example`):
  `ARENA_STORE` (`postgres` default, `memory` for a backend without database), `DEEPTEAM_URL`
  (`http://deepteam:8080`), `ARENA_CALLBACK_URL` (base URL under which deepteam reaches the backend,
  `http://backend:8000`). Model calls go through agentgateway with the backend's internal key. The callback path
  `/api/v1/arena/internal/` is answered with 404 by the frontend proxy and refused by the backend when a request
  arrives through a proxy; only in-cluster calls reach it.
- **Limitations.** The deepteam service does not implement the arena endpoints yet
  (`/internal/arena/generate`, `/internal/arena/multi-turn`, see
  [TESTING.md](docs/spec/config-driven-arena/TESTING.md#what-still-needs-a-live-check)); until then red-team
  generation fails visibly and only the green team runs. Execution is serial per run. Configs are edited as files,
  not in the UI; runs are not compared with each other. The manual challenge mode keeps using the legacy levels.

## GKE

The [Terraform stack](infrastructure/terraform/gke) creates a GKE Autopilot cluster in `europe-west1` with private
nodes, an Artifact Registry repository, a reserved public IP for the frontend, and a **separate static outbound IP** for
Cloud NAT. Agentgateway uses Workload Identity to reach Vertex AI. The outbound IP is shared by workloads using that
NAT; the future custom LLM can allowlist it without changing the gateway model configuration yet.

Terraform creates cloud infrastructure; the [GKE Kustomize overlay](infrastructure/gke) deploys the application. It
removes the local development database Secret and the gateway credentials file. All Kubernetes Services stay
`ClusterIP`. The only public Ingress routes to the frontend Caddy service, which proxies authenticated API traffic.
Phoenix has no public route. GKE enables its native login and restricts collector traffic
to the backend with a NetworkPolicy. Complete the [deployment runbook](docs/deployment.md)
before pushing: it covers the required Phoenix Secrets, deployer permissions, private
Phoenix access, and the optional Leonardo connection.

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

Deploy application images by pushing to `main`. [GitHub Actions](.github/workflows/deploy-gke.yml)
builds and pushes all five images tagged with the commit SHA, applies the GKE overlay,
and verifies rollouts. Keep infrastructure and Secret preparation manual. The `v1` tags
in the checked-in overlay are placeholders replaced by the workflow.

The reserved `frontend_ip` needs an A record for `european-ai-hackathon-2026.codify.ch` before its managed HTTPS
certificate activates. Then add `https://european-ai-hackathon-2026.codify.ch/api/auth/callback/github` to the GitHub
OAuth App's callback URLs. GKE does not supply a dynamic HTTPS hostname suitable for this OAuth flow. The GKE frontend
will be usable after DNS and the certificate are ready; use local Tilt for previews in the meantime.

### DeepTeam

You can access it `http://localhost:5002/docs#/default/start_run_api_deepteam_runs_post once your cluster runs. Logs are available here:
http://localhost:10350/r/deepteam/overview 



### Challenge parity

Challenges 1–3 have independent Easy, Medium, and Hard progress, saved in PostgreSQL's
`challenge_attempts` table (created automatically on first use). The sidebar lists saved chats
and submissions; choosing one restores its result or lets you resume challenge 1. Email and offer
attacks are submitted only as untrusted tool data under a server-defined task. Email delivery and
purchase approval are simulated. Failed email attempts mask credentials before returning them to
the browser; completed attempts reveal the recovered secret.

Hard mode normalizes user, tool, and assistant text. Challenge 1 adds an input judge; challenge 2
adds outgoing-mail authorization and output judges; challenge 3 adds an assessment-output judge.
These checks run in the backend so they can evaluate each challenge at its actual trust boundary.
When upgrading a running gateway, apply the updated gateway ConfigMap and restart the gateway to
remove the previous request webhook. Each run permits 10 model/tool calls, with 50 of each per chat.

Set `CHALLENGE_START` and `CHALLENGE_END` to timezone-qualified ISO timestamps to enable an event
window, or `CHALLENGE_CLOSED=true` to stop submissions. Defaults leave the exercises open. Verified
`@codify.ch` and `@lapiscode.com` users bypass the window. Competition administration is disabled;
`/api/v1/challenges/admin` returns 404. Local backend mode retains its shared local identity while
reading a signed-in user's name from the verified session when available.

The frontend streams individual guardrail checks with their stage, applicable rule, and a short
explanation. Failed email attempts also mask these explanations before they reach the browser.
Saved attempts restore the runtime messages, model settings, submitted fields, results, and guardrail
details. “Not solved” means the agent ran without the attacker achieving the objective. The offer
preview embeds the original PDF with a separate open-PDF link.

The existing generic playground is unchanged; no challenge-specific practice playgrounds are added.
The current model configuration is retained, so attack difficulty can differ from the archived models.
