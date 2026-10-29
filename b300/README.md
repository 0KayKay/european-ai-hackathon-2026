# B300 inference

## What is this for?

Run GLM, Nemotron and GPT-OSS-Safeguard on the Curiosity B300 cluster and call them through
authenticated OpenAI-compatible APIs from your laptop. This is the B300
counterpart of the repository's `leonardo/README.md`: configure access,
prepare models, start a server, wait for readiness, forward ports and call APIs.

**28 October 2026: team review candidate on `codex/b300-cli-team-guide`.** Actual B300/Mac lifecycle
checks passed for GLM, standalone Ultra, the Ultra + Safety pair and
GPT-OSS-Safeguard, with verified owned-resource cleanup. The earlier
standalone Safety smoke test is preserved, and Safety was also checked
in the current pair. **21 local tests passed.** See the
[actual all-profile report](docs/CLI_ALL_PROFILES_20261028.md) and
[coverage matrix](docs/validation.md). GLM was also connected through the public GKE link
and agentgateway using an overlapping step in existing job `12482`. Shared-base ACLs,
preparation-only installation, another laptop and a fresh `up --link` still need separate checks.

## Get the team branch

For a new checkout:

```bash
git clone --branch codex/b300-cli-team-guide --single-branch https://github.com/CodifyAG/european-ai-hackathon-2026.git
cd european-ai-hackathon-2026
python3 b300/cli.py profiles
```

For an existing checkout with a clean working tree:

```bash
git fetch origin
git switch --track origin/codex/b300-cli-team-guide
```

Continue with the setup instructions below using your own AXIS access. Credentials,
runtime files and model weights are not distributed with this branch. The test
servers were stopped after verification; start an allocation before making API calls.

| Profile | Models / API aliases | GPUs | Default laptop ports |
| --- | --- | --- | --- |
| `glm53` | GLM-5.3-Flash: `glm-5.3-flash-leonardo` | 8, TP8/PP1 | 18083 |
| `nemotron` | Ultra and safety together | 5 total | 18085, 18086 |
| `ultra` | `nemotron-ultra-550b-b300`, official 550B NVFP4 | 4, TP4/PP1 | 18085 |
| `safety` | `nemotron-content-safety-reasoning-4b-b300`, BF16 | 1, TP1/PP1 | 18086 |
| `safeguard` | `gpt-oss-safeguard-20b-b300`, native MXFP4/BF16 | 1, TP1/PP1 | 18087 |

GLM keeps its historically tested `leonardo` alias. Configured contexts are
65,536 tokens for GLM, 8,192 for Ultra and Safeguard, and 4,096 for safety. GLM
has 16 sequence slots with 16,384-token chunked prefill; Ultra and safety have
four; Safeguard has one. The GLM 65,536/16 settings are **not yet GPU-validated**
(the recorded GLM runs used 2,048/4); see [GLM context sizing](docs/glm-context.md)
for the memory estimate and the startup log lines to report. The observed team limit was eight GPUs: GLM and the Nemotron pair cannot
run together under that limit. Verify current project/QoS limits before use.

## How does it work?

The laptop CLI submits ordinary Slurm jobs using your own AXIS SSH account.
Preparation and inference run inside a compute allocation. Large images, weights
and compiler caches use temporary node-local `/tmp`; small logs and run records
stay under the remote base's private `users/LOGIN/` folders.

APIs listen on a private compute IP with a fresh key per model. The endpoint is
published only after real API acceptance checks. `forward` adds every model port
to one already-authenticated private SSH connection. This B300 laptop workflow
does not need Leonardo's separate CPU router or CPU-queue installer. With
`up --link`, the run also offers its models to the app's agentgateway in GKE
through the existing cluster link; see [Connect to agentgateway](#connect-to-agentgateway-cluster-link).

## Installation

### What you need

- macOS/Linux laptop, Python 3.9+, SSH and this checkout; no Python packages.
  The CLI uses POSIX locks, so native Windows is not implemented.
- Your own AXIS/Curiosity credentials, Slurm project membership and a writable
  remote base. Do not share personal logins.
- Login node: Python 3.9+, Slurm and `flock`; compute nodes: event Enroot/Pyxis,
  NVIDIA devices and mandatory site hooks.
- Temporary compute disk: about 328 GB of GLM weights or 362 GB for the pair,
  plus image/cache/working space. Do not put them in a 50G home or 300G share.

### Configure the CLI

Examples run from the repository root on your laptop. In the
[AXIS portal](https://axis-raplabhackathon.axisportal.io/apps), open **Curiosity
v2 Login → Use a desktop SSH client** and privately save its fresh
`ssh HOST -l USER` command. Verify the host identity through the trusted operator
process first; the CLI preserves `StrictHostKeyChecking=yes`.

```bash
mkdir -p b300/.runtime
chmod 700 b300/.runtime
# Save your own current AXIS command as b300/.runtime/axis-ssh-command.
chmod 600 b300/.runtime/axis-ssh-command
export B300_SSH_COMMAND_FILE="$PWD/b300/.runtime/axis-ssh-command"
export B300_BASE='~/b300-team'  # personal trial; expanded on the cluster
./b300/cli.py init --account eai-hackathon --partition hackathon --qos eai-team09
./b300/cli.py status
```

The shown project/QoS are recorded team values; use your assigned values.
`init` refuses to overwrite existing settings. Refresh expired AXIS commands
through your own portal. No credentials, endpoints or weights belong in Git.

### Share team settings

For a shared base, every member must use the same **actual absolute remote
path**; `~/b300-team` resolves differently per login. Initialize that path once.
Its owner can share settings and the parent for private personal runs:

```bash
export B300_BASE='/replace/with/actual/remote/team/base'
./b300/cli.py share teammate_login
./b300/cli.py share --revoke teammate_login  # later, if needed
```

Parent directories must already permit traversal. Sharing does not grant Slurm
membership or expose personal run folders, keys or node caches. Each person
uses their own AXIS login and starts/manages their own allocations.

## Usage

### Commands

| Command | What it does |
| --- | --- |
| `profiles` | Lists pinned profiles offline. |
| `init`, `share` | Creates team settings or updates named users' settings access. |
| `sync` | Uploads checksum-verified source into a private immutable release. |
| `install PROFILE` | Allocates the profile's GPUs, prepares image/weights and exits. |
| `up PROFILE` | Allocates GPUs, prepares missing assets, starts and checks APIs, then retains serving. |
| `status` | Shows your Slurm jobs and guarded readiness. |
| `logs TARGET [--follow]` | Shows your private batch log by profile, run ID or job ID. |
| `fetch TARGET` | Saves current live endpoint/keys privately on the laptop. |
| `forward TARGET` | Forwards all APIs in that run over one existing private SSH connection. |
| `query TARGET` | Calls an API through an already-open local forward. |
| `down TARGET` | Cancels only a matching owned job and checks the actual queue. |
| `link setup`, `link token` | Configures the cluster link to agentgateway in GKE; prints or installs its router token. |

`install`/`up` accept `--time HH:MM:SS`, `--node NODE`, `--reuse-job JOB_ID`.
Maximum four hours; safety and Safeguard default to two, other profiles four.
Use `./b300/cli.py COMMAND --help` for options.

### Prepare and start a model

`install` also consumes GPU allocation time. Usually use **`up`**, which prepares
missing assets in the same allocation. First validate the smallest profile:

```bash
./b300/cli.py profiles
./b300/cli.py up safety --time 00:30:00
./b300/cli.py status
./b300/cli.py logs safety --follow
```

When resources permit, select a larger deployment instead:

```bash
./b300/cli.py up nemotron
# Or, separately, with eight free GPUs:
./b300/cli.py up glm53
```

These submit real GPU jobs when executed; do not run all examples together.
Before switching profiles, stop the previous run with `down PROFILE`; closing
the log or forward terminal alone does not release the GPU allocation.
Slurm `RUNNING` is not model readiness. Wait for API checks and `ready`; startup
may take many minutes. Queue wait is separate from allocation time.

Optional preparation/cache reuse:

```bash
./b300/cli.py install safety
./b300/cli.py status
# After that job finishes and records clean server/key shutdown:
./b300/cli.py up safety --reuse-job PREPARATION_JOB_ID
```

Reuse pins the original node and requires matching image, ownership and old
cleanup. It fails if `/tmp` disappeared. Caches are neither permanent nor
automatically shared across accounts.
Reusing image/weight caches does not eliminate all GPU warmup: the measured
Ultra starts still performed runtime kernel tuning after cache reuse.

### Call the models

After the selected run is ready, terminal 1:

```bash
./b300/cli.py forward nemotron
```

Keep it open. In terminal 2:

```bash
./b300/cli.py query nemotron --model ultra --action models
./b300/cli.py query nemotron --model ultra --text 'Explain what a GPU does.'
./b300/cli.py query nemotron --model safety --action classify \
  --policy-file b300/examples/safety-policy.txt --max-tokens 128 \
  --text 'What is the capital of France?'
```

For GPT-OSS-Safeguard, start its separate one-GPU profile, wait for readiness,
and keep `forward safeguard` open before querying it:

```bash
./b300/cli.py up safeguard
./b300/cli.py status
# After ready, terminal 1:
./b300/cli.py forward safeguard
# Terminal 2:
./b300/cli.py query safeguard --model safeguard --action classify \
  --policy-file b300/examples/safety-policy.txt \
  --text 'What is the capital of France?'
./b300/cli.py down safeguard
```

Safeguard places the policy in a system message and the untrusted interaction
in a separate JSON user payload. Its final verdict is `{"violation":false}` or
`{"violation":true}`; the client rejects duplicate fields, malformed/truncated
output and non-boolean labels. It defaults to low reasoning and 2,048 output
tokens. Nemotron's verdict remains `Prompt harm: harmful` / `unharmful`.

For GLM use `forward glm53`, then `query glm53 --model glm`. For standalone
safety use `forward safety` and `query safety --model safety --action classify
--policy-file ...`. The aliases and local ports are in the table above.

OpenAI-compatible SDKs use `http://127.0.0.1:PORT/v1`, the exact alias and the
key from `b300/.runtime/sessions/TARGET/KIND.api-key`. Each model has a separate
key; there is no router token. AXIS/Hugging Face credentials are not API keys.
Raw model calls do not automatically apply the app's guardrails.

For example, with the Nemotron forward open, a Python application can use the
OpenAI SDK (`pip install openai` in that application's environment):

```python
from pathlib import Path
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:18085/v1",
    api_key=Path("b300/.runtime/sessions/nemotron/ultra.api-key").read_text().strip(),
)
response = client.chat.completions.create(
    model="nemotron-ultra-550b-b300",
    messages=[{"role": "user", "content": "Explain what a GPU does."}],
    temperature=0,
    max_tokens=256,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)
print(response.choices[0].message.content)
```

The SDK is optional; the provided CLI needs no pip packages. For GLM or safety,
use that run's port, exact alias, key and documented client/policy settings.

If ports are occupied, match explicit forward/query overrides:

```bash
./b300/cli.py forward nemotron --ultra-port 19085 --safety-port 19086
./b300/cli.py query nemotron --model ultra --local-port 19085 --text 'Hello.'
```

Use `--output b300/.runtime/response.json` on a query to save final-response
evidence without keys/hidden reasoning. Truncated or malformed classifier output
is an error, not an accepted safe verdict.

### Connect to agentgateway (cluster link)

The app's agentgateway in GKE can call a B300 run's models, for example GLM as
`glm-5.3-flash-b300`, which already has a route there. It uses Leonardo's
cluster link server ([design](../leonardo/docs/cluster-link.md),
[Kubernetes side](../infrastructure/leonardo/README.md)): agentgateway calls the
`b300-router` Service with a fixed router token, and the chisel server in GKE
forwards that to reverse port 9001. Leonardo keeps port 9000, so both can connect
at the same time. GLM's gateway alias translates to its legacy native server name,
`glm-5.3-flash-leonardo`; direct laptop clients still use the native name.

On B300, `up PROFILE --link` starts `serve/link.py` in the serving job, on the
batch host next to the model servers. Once the run's endpoint is published, it
starts a relay on `127.0.0.1` and an outbound chisel client to the link server
(`R:9001` → relay). The relay checks the router token, picks the model server by
the request's `model` and calls it with that run's per-model key, which never
leaves the node. It stops when the run's endpoint disappears or the job ends.
`status` shows the link state (`waiting_for_models`, `connecting`, `connected`,
`retrying` with chisel's error, `off`); the log is `link.log` in the run folder.
The settings, the router token and the chisel password are kept in your private
`users/LOGIN/link/`. The pinned chisel 1.13.0 is downloaded there on first use and
checked against its SHA-256.

One person's B300 run holds the link: the GKE server accepts one client on port 9001,
and the CLI refuses a second linked run of yours while one is active. The
outbound connection needs internet on the compute node, which Curiosity has.
Confirm with the organisers that a long-lived outbound connection is acceptable.

**Kubernetes side, once** (see [deployment](../docs/deployment.md#4-activate-leonardo-only-after-cineca-approval)):
create the `leonardo-link` Secret (chisel server key and `users.json` with the
`leonardo:PASSWORD` user permitted to open reverse ports 9000 and 9001) and the
`leonardo-router` Secret (router token), set
the GitHub variable `LEONARDO_ENABLED=true` and deploy. Get the router token from
B300 so the two match:

```bash
./b300/cli.py link token > "$HACKATHON_LINK_PRIVATE/router-token"   # creates it once, then prints it
# If the Secret already exists, install its token on B300 instead:
#   kubectl get secret leonardo-router -o jsonpath='{.data.token}' | base64 -d > token-file
#   ./b300/cli.py link token --token-file token-file
```

**B300 side:**

```bash
FINGERPRINT=$(kubectl logs deployment/leonardo-link | grep -o 'Fingerprint .*' | cut -d' ' -f2)
./b300/cli.py link setup --server https://european-ai-hackathon-2026.codify.ch/leonardo-link \
  --fingerprint "$FINGERPRINT" --auth-file "$HACKATHON_LINK_PRIVATE/link_auth"
./b300/cli.py up glm53 --link
./b300/cli.py status          # ready, then cluster_link: connected
```

Every new allocation needs `--link`; link settings alone do not connect a run.
A ready model without `cluster_link: connected` is not reachable from GKE.
`up PROFILE --link` refuses an already-active run that was started without the link;
stop that run before starting it again with `--link`.

`link_auth` holds `leonardo:PASSWORD`, the same user as in `users.json`. It is
sent over SSH stdin, never on a command line. Then, from your machine:

```bash
kubectl port-forward svc/agentgateway 13000:3000 &
KEY=$(kubectl get secret gateway-internal -o jsonpath='{.data.key}' | base64 -d)
curl -sS http://127.0.0.1:13000/v1/chat/completions -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"model":"glm-5.3-flash-b300",
  "messages":[{"role":"user","content":"Hi"}],"chat_template_kwargs":{"reasoning_effort":"low"}}'
```

`503` from agentgateway means the link is down; `404 model_not_found` from the
relay means the link works but this run does not serve that model. All B300
aliases (GLM, Nemotron and Safeguard) have routes in
`infrastructure/leonardo/agentgateway.yaml`, generated from `serve/models.json`
by `leonardo/tools/render_agentgateway.py`. Deploy the updated gateway config
before using the Nemotron or Safeguard aliases. Tested locally with the real
chisel 1.13.0 server and client (tunnel, token, streaming, reconnect after a
server restart, shutdown with the endpoint) and in `tests/test_link.py`.

On 28 October 2026, an overlapping Slurm step attached the link to the already-ready
GLM job `12482` in `/storage/hackathon_teams/eai-team09/base`. The public GKE link
connected, agentgateway rejected unauthenticated requests (401), and its authenticated
catalog listed all four B300 aliases. A completion through agentgateway returned `4`
for `2+2` in 1.29 seconds, with the expected GLM alias and `finish_reason: stop`.
Streaming also returned `4` and the terminal `[DONE]` event.
This verifies the deployed GLM route; a fresh `up --link`, other models through GKE,
reconnection and sustained load still need live checks.

### Stop and troubleshoot

```bash
./b300/cli.py down nemotron
./b300/cli.py status
```

`down` checks recorded job name/owner and actual queue disappearance.
`cleanup_pending` is not released resources. Closing a forward does not stop
the GPU server. Serving ends at its Slurm limit without automatic renewal.
An eight-GPU shutdown can outlast the initial 30-second wait: check `status`
until the owned job is `NOT_IN_QUEUE`, and rerun `down` to confirm completion.
Do not treat the first pending-cleanup receipt as either completed cleanup or
a failed model inference test.

- Pending / `QOSGrpGRES`: inspect occupied GPUs; avoid duplicate allocations.
- Pending / `Resources` with `--reuse-job`: the selected cache node may lack
  enough free GPUs. Either wait or stop that owned pending job, verify shutdown,
  and retry without cache reuse so Slurm can choose another node.
- Loading: inspect logs; checkpoint loading and kernel tuning take time.
- SSH host-key errors: verify identity through the trusted operator process.
- Expired AXIS access: refresh your private command file.
- HTTP 401: verify model choice and current key; `fetch` rejects expired jobs.
- Occupied ports: use `--glm-port`, `--ultra-port`, `--safety-port`, `--safeguard-port` on forwarding.
- `submission_uncertain`: inspect your private receipt and Slurm before retrying;
  a lost reply never triggers an automatic second allocation.
- Read-only cache/cubin errors: preserve writable paths, site mounts and preflight.
- Missing cache: prepare again inside an owned allocation.

## Project structure and validation

`cli.py` is the laptop entry point. `serve/manage.py` manages private records
and Slurm; `serve/run.sbatch` runs Enroot inside the allocation; `serve/server.py`
prepares pinned checkpoints and retains checked APIs. `serve/validation.py` and
`serve/vendor/` hold Nemotron and original GLM checks. `serve/cubin_overlay.py`
preserves the tested writable-cubin repair. Settings live in `b300-site.json`;
private runs in `users/LOGIN/runs/RUN_ID/` under the remote base.

- [Validation status](docs/validation.md): historical real runs versus this new CLI.
- [All-profile lifecycle verification](docs/CLI_ALL_PROFILES_20261028.md).
- [28 October CLI smoke test](docs/CLI_VALIDATION_20261028.md): actual safety API and cleanup evidence.
- [Architecture](docs/how-it-works.md) and [Slurm jobs/direct submission](docs/slurm-jobs.md).
- [Direct-client guide](docs/USER_GUIDE.md) and [actual Mac/team-account check](docs/MAC_CONNECTIVITY_20261028.md).
- [Recorded settings](docs/OPERATOR_NOTES.md), [measured results](docs/VERIFIED_RESULTS.md) and [publication draft](GITHUB_PREPARATION.md).

After changing serving source:

```bash
python3 b300/tools/update_manifest.py
python3 b300/tools/check_repository.py
python3 -m unittest discover -s b300/tests -v
python3 -m unittest discover -s b300/clients -p 'test_*.py' -v
```

The actual profile report separates live model evidence from local fixtures.
Model/API lifecycle checks passed; shared-base access and other untested
features remain outside that result. This branch is a review candidate; it has
not been merged into `main`.
