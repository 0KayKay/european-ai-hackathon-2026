# One B300 node, many models

`node.sbatch` takes a whole 8-GPU B300 node, starts every model in
[`lineup.json`](lineup.json) as its own vLLM server and puts one
[agentgateway](https://agentgateway.dev) in front of them, so the node behaves
like one OpenAI-compatible API with one key. Weights, container images and
compile caches go to a persistent cache folder, so only the first start
downloads anything.

This folder is self-contained: it does not use the laptop CLI or `../serve/`,
it only reuses what those runs established about the cluster: Enroot/Pyxis containers,
the site's mandatory mounts, the pinned vLLM image and model revisions.

> **Status: not yet run on the cluster.** Everything here was checked locally
> (lineup validation, gateway configuration against agentgateway's v1.6.0
> schema, unit tests). The first real job is the test. What is and is not
> backed by earlier B300 runs is listed [below](#what-is-tested-and-what-is-not).

## What runs where

| GPU | Lineup id | Model (API name) | API | Memory share |
| --- | --- | --- | --- | --- |
| 0–3 | `glm` | `glm-5.3-flash` (alias `glm-5.3-flash-b300`), TP4 | chat, tools, reasoning | 0.90 each |
| 4 | `safety` | `nemotron-content-safety-reasoning-4b` | chat (classifier) | 0.12 |
| 4 | `safeguard` | `gpt-oss-safeguard-20b` | chat (policy classifier) | 0.25 |
| 5 | `embed` | `bge-m3` | `/v1/embeddings` | 0.08 |
| 5 | `rerank` | `bge-reranker-v2-m3` | `/v1/rerank`, `/v2/rerank`, `/v1/score` | 0.08 |
| 6 | `fast` | `qwen3-30b-a3b-instruct-2507` | chat, tools | 0.90 |
| 7 | `whisper` | `whisper-large-v3-turbo` | `/v1/audio/transcriptions` | 0.08 |
| 7 | `vision` | `qwen3-vl-30b-a3b-instruct` | chat with images | 0.82 |

"Memory share" is vLLM's `--gpu-memory-utilization` on a 288 GB B300. Models
sharing a GPU start one after another (vLLM sizes itself from the memory still
free), and the plan refuses a lineup whose shares on any GPU exceed 0.95.
Anything left over is KV cache: vLLM logs the real number at startup and the
job copies it into `status.json`.

Disabled variants, selected with `B300_MODELS` instead of `glm`:

- `glm-fast`: GLM on GPUs 0–3 with the report's tuning: CUDA graphs, FP8 KV
  cache, MTP speculative decoding (3 tokens), 8,192-token prefill chunks.
  Not yet tried on B300.
- `glm-tp8`: GLM alone on all eight GPUs. This is the TP8/PP1 layout from
  `../serve/`, the only GLM layout that has run on B300. Its context settings
  (65,536 tokens, 16 slots) are the ones `../serve/` now configures. The
  recorded runs used 2,048 tokens and 4 slots.

## Quick start

On the login node, from a checkout of this branch:

```bash
# 1. Fill the cache once: images, weights (~480 GB for the default lineup), gateway binary.
#    It needs no GPU work, so ask for less than a node.
MODE=prefetch sbatch --gpus=1 --cpus-per-task=16 --mem=128G --time=02:00:00 b300/node/node.sbatch

# 2. Serve the default lineup (whole node, up to four hours).
sbatch b300/node/node.sbatch

# Variants and settings are environment variables (sbatch passes them on;
# --export=ALL,VAR=a,b would split the comma list):
B300_MODELS=glm-fast,safety,safeguard,embed,rerank,fast,whisper,vision sbatch b300/node/node.sbatch
B300_MODELS=glm-tp8 sbatch b300/node/node.sbatch
CACHE_DIR=/some/other/folder sbatch b300/node/node.sbatch
```

The account, partition and QoS in the `#SBATCH` header are the recorded team
values (`eai-hackathon`, `hackathon`, `eai-team09`). Command-line options
override them, for example `--reservation=NAME` or `--qos=...`.

Skipping step 1 also works: the serving job downloads whatever is missing, but
then download time comes out of the four hours.

### Watching it start

```bash
tail -f b300-node-JOBID.log      # in the submit directory: one line per model state change
CACHE=/storage/hackathon_teams/eai-team09/experiment
cat $CACHE/runs/JOBID/status.json  # per-model state, port, GPUs and vLLM's KV cache size
tail -f $CACHE/runs/JOBID/logs/glm.log
```

The small models are usually ready within minutes. GLM took about 14 minutes
to become ready in the earlier TP8 runs. A `READY` line means vLLM's `/health`
answered. `GATEWAY http 200` means agentgateway is up and accepted the key.
If a model fails, the others keep serving. The job ends when its time is up,
when you `scancel` it, or when every model has exited.

## Calling the API

The job writes the address to `$CACHE/runs/latest-endpoint.json` (readable by
the team, no secrets) and to `endpoint.json` in the run folder. The client key
is `$CACHE/keys/gateway.key`. It is created on the first run and kept, so it
stays the same across jobs. It is mode 600; to share it with the team's group,
run `chmod g+r` on it.

From the login node:

```bash
EP=$(python3 -c "import json;print(json.load(open('$CACHE/runs/latest-endpoint.json'))['base_url'])")
KEY=$(cat $CACHE/keys/gateway.key)
curl -s $EP/models -H "Authorization: Bearer $KEY"
```

From a laptop, forward the port through the login node. This uses the AXIS SSH
command from the [main guide](../README.md#configure-the-cli); `NODE_IP` comes from `latest-endpoint.json`:

```bash
$(cat b300/.runtime/axis-ssh-command) -N -L 18080:NODE_IP:18080
# in another terminal:
export OPENAI_BASE_URL=http://127.0.0.1:18080/v1 OPENAI_API_KEY=...the gateway key...
```

Examples (any OpenAI SDK works the same way):

```bash
H=(-H "Authorization: Bearer $OPENAI_API_KEY" -H 'Content-Type: application/json')
curl -s $OPENAI_BASE_URL/chat/completions "${H[@]}" -d '{"model":"glm-5.3-flash",
  "messages":[{"role":"user","content":"2+2?"}],"chat_template_kwargs":{"reasoning_effort":"low"}}'
curl -s $OPENAI_BASE_URL/embeddings "${H[@]}" -d '{"model":"bge-m3","input":["hello","world"]}'
curl -s $OPENAI_BASE_URL/rerank "${H[@]}" -d '{"model":"bge-reranker-v2-m3","query":"capital of France",
  "documents":["Paris is in France","Berlin is in Germany"]}'
curl -s $OPENAI_BASE_URL/audio/transcriptions -H "Authorization: Bearer $OPENAI_API_KEY" \
  -F model=whisper-large-v3-turbo -F file=@speech.wav
```

The safety models are called as in the [main guide](../README.md#call-the-models):
Nemotron returns `Prompt harm: harmful/unharmful`, Safeguard returns
`{"violation": true|false}` for a policy in the system message.

## How it works

1. **Plan.** `node.py plan` checks the selected lineup for unique names and
   ports, existing GPU indexes, at most one rerank and one transcription model,
   and memory shares of at most 0.95 per GPU. It writes `plan.json` and records
   which models must wait for another on the same GPU.
2. **Images.** Each image in the plan is imported once with `enroot import`
   into `$CACHE/images/KEY-DIGEST.sqsh`, the same pinned Docker Hub image the
   `../serve/` jobs use. Docker itself is not involved: Pyxis runs the
   squashfs file through `srun --container-image`.
3. **Steps.** Every model is its own `srun --overlap` step. The steps share
   the allocation's CPUs, memory and GPUs, and `launch.py` picks the model's
   GPUs with `CUDA_VISIBLE_DEVICES`. Each image is unpacked once into a named
   container that all its steps reuse. The site's Slurm prolog and Python hook
   mounts are kept. `--container-writable` is on by default so FlashInfer can
   write its kernel files (the read-only problem `../serve/cubin_overlay.py`
   works around). Set `CONTAINER_WRITABLE=0` to turn it off.
4. **Weights.** `launch.py` looks for `$CACHE/hf/complete/REPO@REVISION.json`.
   If present, the snapshot is opened offline. If missing, the model is
   downloaded into `$CACHE/hf/hub`, checked for every shard in its index, and
   the marker is written with the resolved commit. vLLM then always runs with
   `HF_HUB_OFFLINE=1`. To refresh a `main` model, delete its marker.
5. **Serving.** vLLM listens on `127.0.0.1:81xx` with a per-job internal key.
   agentgateway, a checksum-pinned static binary on the batch host, listens on
   `0.0.0.0:18080`. It accepts only the client key, stored in its config as a
   SHA-256 hash, and adds the internal key towards vLLM. Chat and embeddings go
   through agentgateway's LLM routes by `model`; rerank and audio are routed by
   path.
6. **Watching.** Every 30 s `node.py status` probes each `/health` and the
   gateway's `/v1/models`, writes `status.json` and prints changes. A crashed
   gateway is restarted.

Cache layout under `CACHE_DIR` (default `/storage/hackathon_teams/eai-team09/experiment`):

```text
hf/hub/                 Hugging Face cache (weights)
hf/complete/            one marker per finished repo@revision
images/                 enroot squashfs images
bin/                    agentgateway binary
compile/IMAGE/MODEL/    vLLM, Triton, FlashInfer, DeepGEMM caches (PERSIST_COMPILE_CACHE=0 keeps them in /tmp)
keys/gateway.key        client key (mode 600)
runs/JOBID/             private per job: plan, gateway config, logs, status, endpoint
runs/latest-endpoint.json
```

The cache is created with group read/write (umask 007) so teammates reuse it.
Run folders and keys are private.

## Changing the lineup

Edit `lineup.json`. Each model has `id`, `name` (and optional `aliases`),
`repo`, `revision`, `image`, `gpus`, `memory`, `port`, `api`
(`chat|embed|rerank|transcribe`), the extra vLLM `args`, and optionally `env`
and `enabled`. Tensor parallelism is the number of GPUs listed. Check your
change before submitting:

```bash
python3 b300/node/node.py plan --out /tmp/plan.json            # enabled models
python3 b300/node/node.py plan --models glm-tp8 --out /tmp/plan.json
python3 -m unittest discover -s b300/node -p 'test_*.py' -v
```

The `images` map also contains the official `vllm/vllm-openai:v0.32.0` image
(as `vllm-0.32`) for models that need a newer vLLM. That image has not been
run on B300, and its CUDA version may need a newer driver than the nodes have.
Each job snapshots the lineup, so editing it never changes a running job.

## What is tested and what is not

Backed by earlier B300 runs (see [validation](../docs/validation.md)):

- the GLM image, GLM-5.3-Flash at TP8/PP1, GPT-OSS-Safeguard in the same image
- the Nemotron safety model, then in the vLLM 0.22 image
- Enroot/Pyxis with the site mounts, and compute-node internet for downloads

New here and not yet run on the cluster:

- **GLM at TP4.** The only earlier attempt with four GPUs used TP4/PP2 and
  failed for an unrelated reason. Memory is comfortable at TP4: about 82 GB of
  weights per GPU. If TP4 fails, `glm-tp8` is the fallback.
- **Several overlapping `srun` steps** sharing one allocation, named
  containers, and `--container-writable`.
- **The newer models in the GLM image**: Qwen3, Qwen3-VL, Whisper, BGE.
  Their revisions are `main`; the job records the resolved commit in each marker,
  so you can pin it afterwards.
- **agentgateway on the node.** Its config is validated against the v1.6.0
  schema, but the binary has not run with it yet. Rerank and audio use path
  routes; check that `/v1/audio/transcriptions` passes multipart uploads.
- **Cache throughput** of `/storage/hackathon_teams/eai-team09` and its real
  size. The default lineup needs about 480 GB of weights plus one vLLM image
  (tens of GB as squashfs).

## Differences from the deep-research report

- **Memory.** The report assumes 256 GB usable per GPU. The B300s have 288 GB,
  so its memory sums are conservative.
- **GLM's KV cache.** The report's architecture details (45 layers, 6.3 KB per
  token, 1M context at FP8) are unverified. The default uses 131,072 tokens and
  64 sequences. vLLM's startup line in `status.json` gives the real capacity;
  raise `--max-model-len` / `--max-num-seqs` in `lineup.json` from there.
- **Tuning flags.** `--speculative-config` MTP, `--kv-cache-dtype fp8`, CUDA
  graphs and smaller prefill chunks are in `glm-fast`, not in the default. The
  default stays close to the flags that have run on B300. DeepGEMM and the MLA
  attention kernels are chosen by vLLM automatically on Blackwell.
- **NCCL.** No NCCL variables are set. `NCCL_NET_GDR_LEVEL` and
  `NCCL_CROSS_NIC` only affect traffic between nodes, and NCCL already uses
  NVLink between the GPUs it is given.
- **Auxiliary models.** Instead of Llama-Guard-3 (gated), Qwen2.5-32B and
  Qwen2-VL-72B, the lineup uses:
  - the two safety models this repo already evaluated
  - BGE-M3 plus a reranker
  - a Qwen3 MoE for fast chat
  - Qwen3-VL plus Whisper
  
  Several small models share a GPU because one 288 GB GPU per 10 GB model
  wastes most of it.
- **Gateway.** agentgateway instead of LiteLLM. It needs no Kubernetes: it is
  one binary with a config file, the same gateway the app already uses in GKE.
- **Capacity numbers.** The report's Little's-law figures (about 260
  concurrent users) are not used anywhere. Measure under load once the node is
  up.

## Troubleshooting

| Symptom | Look at |
| --- | --- |
| `Invalid lineup` right away | the printed conflicts; fix `lineup.json` or `B300_MODELS` |
| a model `EXITED` | `runs/JOBID/logs/ID.log`; common causes are an unsupported architecture in this image (try `"image": "vllm-0.32"`) or memory share too small |
| `Free memory on device ... less than desired` | shares on that GPU are too high or another process uses it; lower `memory` |
| step never starts | `slurm-allocation.txt` and the Slurm version: steps rely on `srun --overlap` |
| read-only file errors in FlashInfer | keep `CONTAINER_WRITABLE=1`; if the site forbids it, use `../serve/` for Nemotron models |
| `GATEWAY http 401` | `GATEWAY_KEY_FILE` changed during the job; restart the job |
| gateway up but a model returns 5xx | that model is still `starting` or exited (`status.json`) |
| download fails | network from the compute node, `HF_TOKEN` for gated repos, free space in `CACHE_DIR` (`df` at the top of the job log) |
