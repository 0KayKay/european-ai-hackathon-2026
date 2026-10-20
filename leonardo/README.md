# Leonardo inference

Large open-weight models served with vLLM on CINECA's Leonardo (Slurm, Singularity, A100 64 GB), behind an
OpenAI-compatible API with a per-run key. Built by Junghwa ([@Jules0315](https://github.com/Jules0315)) on
top of the team's starter kit; copied and consolidated from
[Jules0315/Guardrails-vllm](https://github.com/Jules0315/Guardrails-vllm) at commit `298432c`.

| Profile | Model | Served as | Layout | Cold start | Local port |
| --- | --- | --- | --- | --- | --- |
| `qwen235b` | Qwen3-235B-A22B-FP8 | `qwen3-235b-a22b-fp8-leonardo` | 2 nodes, TP4 / PP2 | 7 min | 18085 |
| `glm53` | GLM-5.3-Flash | `glm-5.3-flash-leonardo` | 2 nodes, TP4 / PP2 | 13 min | 18083 |
| `deepseek41` | DeepSeek-V4.1-Flash-1017 | `deepseek-v4.1-flash-1017-leonardo` | 2 nodes, TP8 + expert parallel | 26 min | 18084 |

Each server takes 2 full nodes (8 GPUs) for up to 2 hours, with a 2,048-token context and 4 concurrent
sequences (DeepSeek: 2). All three passed real GPU runs with 7/7 API checks; see [validation](#validation).

## Layout

```text
serve/        the launcher: profiles, Slurm job, readiness gate, client.  Use this.
bringup/      stock-vLLM kit: probe nodes, check InfiniBand, serve small models on 1 GPU
install/      one-time setup of the runtime image and checkpoints in the team folder
validation/   evidence from the GPU runs and the hashes of the code that passed them
docs/         how-it-works.md (read before changing serve/), history.md
tools/, tests/  local checks; no cluster needed
```

## Run a server (operator, on Leonardo)

Use your own Leonardo login. Coordinate with the team: one big server at a time.

```bash
ssh YOUR_LOGIN@login01-ext.leonardo.cineca.it
git clone https://github.com/CodifyAG/european-ai-hackathon-2026.git   # once; needs a GitHub token if private
cd european-ai-hackathon-2026 && git pull --ff-only

BASE="$WORK/team_vllm"            # the team folder with the installed runtime and weights
MODEL=qwen235b                    # or glm53, deepseek41
RUN_DIR=$(bash leonardo/serve/prepare_run.sh "$MODEL" --base "$BASE") && cd "$RUN_DIR"
```

`team_vllm` is the public placeholder. Use the team's actual folder name, which you can get from Junghwa.
Preparing checks every asset, makes a private copy of the code in `$HOME`, and creates a fresh API key.
It does not submit anything yet.

```bash
python3 inference.py cpu-check    # optional: imports torch/vllm/tilelang in the container, no GPU
bash submit_server.sh --dry-run   # shows the sbatch command
bash submit_server.sh             # add --reservation NAME on workshop days
tail -f *-common-*.out            # wait for INFERENCE_READY
cat .runtime/endpoint.json        # node, port, model, expected end time
cat .runtime/api_key              # share privately with teammates who need it
scancel "$(cat .runtime/job_id)"  # stop it when done
```

The job publishes `endpoint.json` only after all 7 API checks pass. It stops itself if the model dies or
`/health` fails 3 times, and about 90 seconds before the time limit. For a short test, the debug QoS
(30 minutes) usually starts much sooner than the profile default (`boost_qos_lprod`, 2 hours). Switch the
job while it is still pending:

```bash
scontrol update JobId="$(cat .runtime/job_id)" QOS=boost_qos_dbg TimeLimit=00:30:00
```

## Call it (client, on your laptop)

You need your own Leonardo login, the operator's `endpoint.json` and the session key. No GPU or container
is needed.

```bash
cd leonardo/serve
MODEL=qwen235b PROFILE="profiles/$MODEL.json" KEY_FILE=".runtime/$MODEL-api-key"
python3 client.py key    --profile "$PROFILE" --key-file "$KEY_FILE"     # hidden prompt, never overwrites
python3 client.py tunnel --profile "$PROFILE" --endpoint endpoint.json --user YOUR_LOGIN   # keep open
```

In a second terminal, with the same variables set:

```bash
python3 client.py test --profile "$PROFILE" --key-file "$KEY_FILE"   # the same 7 checks
python3 client.py chat --profile "$PROFILE" --key-file "$KEY_FILE"
curl http://127.0.0.1:18085/v1/chat/completions -H "Authorization: Bearer $(cat "$KEY_FILE")" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-235b-a22b-fp8-leonardo","messages":[{"role":"user","content":"Hi"}],
       "chat_template_kwargs":{"enable_thinking":false}}'
```

`*.runtime/`, `endpoint.json` and key files are git-ignored. Keep them out of shared folders anyway.

## Small models and debugging: `bringup/`

`bringup/` runs stock vLLM (any tag from Docker Hub) without the backport runtime. Use it to probe a node,
measure NCCL over InfiniBand, or serve a model that fits on one GPU, such as a moderation classifier:

```bash
cd leonardo/bringup
MODEL_ID=org/small-model sbatch 00_prepare.sbatch                         # download on the CPU queue
MODEL_ID=org/small-model ENDPOINT_NAME=moderation sbatch 02_vllm_single.sbatch
LEO_USER=YOUR_LOGIN TEAM=<team folder> LOCAL_PORT=8001 ./tunnel.sh moderation   # on your laptop
```

Its endpoint files are in the shared `$WORK/$TEAM/endpoints/`, and its key in `~/.config/leonardo-vllm/`.
Unlike `serve/`, it does not create a fresh key per run. See [bringup/README.md](bringup/README.md).

## Fresh install (only for a new team folder or a new model)

The team's installation already exists, so skip this unless you are setting up from scratch. All jobs run
on the CPU-only serial queue, which has internet. Set `SBATCH_ACCOUNT` and `BASE` first.

```bash
cd leonardo/install
sbatch 01_pull_runtime.sbatch                 # SM80 vLLM backport image -> $BASE/staging
sbatch 02_install_tilelang.sbatch             # TileLang 0.1.12 overlay (after 01 finishes)
sbatch 03_download_checkpoint.sbatch qwen235b # -> $BASE/checkpoints/qwen235b; also glm53, deepseek41
python3 register_runtime.py --id vllm-backport-sm80-0.13.1 \
  --sif "$BASE/staging/vllm-backport-sm80-0.13.1/image.sif" \
  --overlay "$BASE/staging/vllm-backport-sm80-0.13.1/python-overlay" \
  --destination "$BASE/runtimes/vllm-backport-sm80/0.13.1"
cp ../serve/site.example.json "$BASE/inference-site.json"    # then set "account"
mkdir -p "$BASE/users"                                       # and grant teammates access
```

These scripts are consolidated from the original GLM and Qwen preparation jobs and have not been re-run in
this exact form.

## Add a model

1. Download it with `install/03_download_checkpoint.sbatch` (set `MODEL_ID` and `MODEL_REVISION`) and add
   it to `checkpoints` in `$BASE/inference-site.json`.
2. Copy a profile in `serve/profiles/` and adjust it. [docs/how-it-works.md](docs/how-it-works.md#profiles)
   lists the rules the launcher enforces.
3. Run `python3 tools/update_manifest.py`, then a debug-QoS run on Leonardo. Record the result in
   `validation/`.

The test suite expects tool-calling and chat, so a pure classifier needs `bringup/`, or its own checks.

## Local checks

```bash
cd leonardo
python3 tools/check_repository.py              # syntax, manifest, evidence, links, drift from validated code
python3 -m unittest discover -s tests          # serve/: simulated Slurm, container and HTTP
python3 -m unittest discover -s bringup/tests  # bringup/: simulated Slurm, SSH and HTTP
```

These run in CI. They test orchestration, not GPU kernels or model quality.

## Validation

[validation/validated_code.json](validation/validated_code.json) lists the exact `serve/` bytes that ran on
GPUs. Qwen ran on exactly today's code. GLM and DeepSeek ran on a version where only the API port line in
`serve_model.sbatch` differed. `check_repository.py` lists any file that has drifted since. Every run used
one operator account; other accounts, sustained load and unattended operation are untested. Details are in
[docs/history.md](docs/history.md).

## Connecting the GKE cluster (next step)

Today, access is a person's SSH tunnel to one node and port. To let the backend on GKE use these models:

- **Where it plugs in.** Agentgateway ([infrastructure/base/agentgateway.yaml](../infrastructure/base/agentgateway.yaml))
  currently routes every model to Vertex AI. A Leonardo model becomes an OpenAI-compatible backend there,
  with the session key as its credential and the served name (for example `qwen3-235b-a22b-fp8-leonardo`)
  as its model.
- **Endpoint discovery.** `serve/` writes `endpoint.json` into the operator's private `$HOME` run
  directory, and node and port change with every job. A bridge needs a stable way to find it, such as
  the reverse-proxy job from the pre-hackathon notes, which reads endpoints from the team folder (as
  `bringup/` already publishes them).
- **The tunnel.** Option A is a bridge pod holding an SSH tunnel through a login node; it needs a
  credential CINECA allows for that purpose. Option B has the job dial out through the hackathon proxy on
  login node 7; it needs the organisers' approval. Neither is in this repository yet.
