> For the large models (Qwen3-235B, GLM-5.3, DeepSeek-V4.1) use [`serve/`](../README.md) instead.
> This kit runs **stock vLLM** on one GPU or across nodes: use it to probe a new setup, debug the
> network, or serve small models such as moderation classifiers. It is the evolved version of the
> original `leonardo-vllm` starter kit.

# Bring-up kit: stock vLLM on Leonardo

Each step answers one question before the next one adds complexity.

| Step | Script | Question it answers | Runs on |
|---|---|---|---|
| 0 | `00_prepare.sbatch` | Can we get the vLLM image and a model onto the cluster? | `lrd_all_serial` (CPU-only preparation) |
| 1 | `01_probe.sbatch` | What do GPU nodes look like (interfaces, IB, driver)? Can login node and laptop reach a port on a compute node? | 1 GPU, debug QOS |
| 2 | `02_vllm_single.sbatch` | Does vLLM run in the container on 1 GPU and answer through the tunnel? | 1 GPU, debug QOS |
| 3 | `03_nccl_check.sbatch` | Do GPUs on 2 nodes talk over InfiniBand from inside the container? | 2 nodes x 4 GPUs |
| 4 | `04_vllm_multinode.sbatch` | One vLLM instance across 2 nodes (TP=4 per node, PP=2 across) | 2 nodes x 4 GPUs |

## Setup (login node, once)

```bash
# code lives in $HOME, images/models/caches go to $WORK/<team>
ssh YOUR_LOGIN@login01-ext.leonardo.cineca.it
git clone https://github.com/CodifyAG/european-ai-hackathon-2026.git
cd european-ai-hackathon-2026/leonardo/bringup
export TEAM=<YOUR_TEAM>           # common.sh defaults to team_guardrails
# account: scripts use YOUR_PROJECT from the hackathon guide. To override without editing:
#   export SBATCH_ACCOUNT=<account>      (env vars beat #SBATCH lines)
# on-site days with a reservation:
#   export SBATCH_RESERVATION=<name>
chmod +x *.sh
```

Always submit from this folder (`sbatch 0X_...sbatch`); logs land here as `<job>-<id>.out`.

## Step 0 - image and model

```bash
sbatch 00_prepare.sbatch          # ~30-90 min, mostly the image pull
```

Check the log for: vLLM/torch/CUDA versions, whether `libibverbs` exists in
the image (needed in step 3), and the model download.
If `curl https://huggingface.co` prints no `200`, the serial partition has no
internet for you: run the same commands on the login node inside `tmux`, or
ask the organisers.

`VLLM_TAG=latest` is fine for a first try; pin a real tag afterwards
(`VLLM_TAG=v0.x.y sbatch 00_prepare.sbatch`) so the image doesn't change under you.

## Step 1 - probe + static page

```bash
sbatch 01_probe.sbatch
squeue --me                        # wait for R
cat probe-<jobid>.out              # interface names, IB state, driver
curl http://<lrdnXXXX>:<port>/     # from the login node, values are in the log
```

From your laptop (keep it running in its own terminal):

```bash
LEO_USER=YOUR_LOGIN TEAM=<YOUR_TEAM> ./tunnel.sh probe
open http://127.0.0.1:8000/        # "Hello from lrdnXXXX"
```

If the direct forward fails but `curl` from the login node worked, fall back to
a double hop: `ssh -L 8000:localhost:<port> YOUR_LOGIN@login01-ext... ssh -N -L <port>:localhost:<port> <lrdnXXXX>`.

## Step 2 - vLLM on one GPU

```bash
ENDPOINT_NAME=qwen-single sbatch 02_vllm_single.sbatch
tail -f vllm-single-<jobid>.out    # ends with a smoke test reply
```

Laptop:

```bash
LEO_USER=YOUR_LOGIN TEAM=<YOUR_TEAM> ./tunnel.sh qwen-single
VLLM_API_KEY=$(ssh YOUR_LOGIN@login01-ext.leonardo.cineca.it cat .config/leonardo-vllm/api_key) \
  ./smoke_test.sh http://127.0.0.1:8000
```

The server listens on `0.0.0.0` so the login node can reach it, which means
every Leonardo user can reach it too. That is why it always runs with an API
key (`~/.config/leonardo-vllm/api_key`, created on first run, mode 600).

## Step 3 - multi-node network check

```bash
sbatch 03_nccl_check.sbatch
```

Read the end of the log:

The task launcher imports vLLM to initialize CUDA compatibility paths, then
executes a fresh Python interpreter so the dynamic linker uses the new paths.
Importing vLLM within the benchmark interpreter alone is insufficient.
An all-reduce correctness failure exits the job with an error.

* `NET/IB` (ideally with `GDRDMA`) -> InfiniBand works from the container.
* `NET/Socket` -> NCCL fell back to TCP. Usually the image lacks
  `libibverbs` + the mlx5 provider: build `Dockerfile.ib`, push it to your
  registry, re-run step 0 with `VLLM_IMAGE=docker://<registry>/vllm-ib:<tag>`.
* Rough expectations: intra-node bus bandwidth well above 100 GB/s; across
  nodes, each GPU has its own 100 Gb/s port, so ~10 GB/s+ means IB is working
  and low single digits means sockets.
* Hangs at init: set `NCCL_SOCKET_IFNAME` / `GLOO_SOCKET_IFNAME` to the
  interface that carries the node's own IP (see the probe output).

## Step 4 - vLLM across 2 nodes

```bash
ENDPOINT_NAME=qwen-multi sbatch 04_vllm_multinode.sbatch
LEO_USER=YOUR_LOGIN TEAM=<YOUR_TEAM> ./tunnel.sh qwen-multi
```

Swap the model with `MODEL_ID=org/model sbatch 00_prepare.sbatch` then
`MODEL_ID=org/model sbatch 04_vllm_multinode.sbatch`. Extra flags:
`VLLM_EXTRA_ARGS="--enable-prefix-caching"`.

Sizing on Leonardo (4x A100 64 GB per node): a 70B model in bf16 (~140 GB)
fits on **one** node with TP=4. Spanning nodes only pays off for models that
don't fit; for more throughput run several single-node replicas instead.

## Multiple models and endpoint ownership

Use a distinct `ENDPOINT_NAME` for each service (for example `qwen-main` and
`moderation`). If omitted, a stable name is derived from the job name and full
`MODEL_ID`, with a checksum to distinguish sanitized model names. The job log
prints the exact name. Pass it explicitly to `tunnel.sh`; the tunnel no longer
defaults to `vllm-single`.

```bash
MODEL_ID=<main-model> ENDPOINT_NAME=qwen-main sbatch 04_vllm_multinode.sbatch
MODEL_ID=<moderation-model> ENDPOINT_NAME=moderation sbatch 02_vllm_single.sbatch
# Run these tunnels in separate terminals, using separate local ports:
LEO_USER=YOUR_LOGIN TEAM=<YOUR_TEAM> LOCAL_PORT=8000 ./tunnel.sh qwen-main
LEO_USER=YOUR_LOGIN TEAM=<YOUR_TEAM> LOCAL_PORT=8001 ./tunnel.sh moderation
```

Download each model with step 0 first. Set the corresponding `MODEL_ID` again
when submitting inference jobs. A served model name is separate from its
endpoint name; API calls must use `SERVED_NAME` or the name in `/v1/models`.

Jobs claim an endpoint name before starting. A second job with the same name
fails instead of replacing the first service. The `.env` file is published
atomically only after both health and inference checks pass. The publishing
job alone can remove its endpoint and lock. Different tunnel processes also
have separate SSH control sockets, so stopping one leaves the others running.

A hard kill or node failure may leave `.NAME.lock` or `NAME.env` in
`$WORK/$TEAM/endpoints`. Read the lock's `owner` and the endpoint's `JOBID`,
then verify the old job is finished using `squeue`/`sacct` before removing
those specific stale files. Do not clear a lock belonging to a running job.
Normal exit and TERM/INT cleanup release the claim. A replacement job with
the same `ENDPOINT_NAME` can then publish its new address, which the tunnel
follows automatically.

## Smoke-test success criteria

`smoke_test.sh` now fails on HTTP errors, malformed/error JSON, missing models,
or a chat response with no generated content/reasoning text. With `SERVED_NAME`
set, it also requires that exact model to be listed. Inference jobs stop and
clean up if this test fails; they do not publish an endpoint for a failed test.
Short Qwen responses containing reasoning text only are accepted because they
still demonstrate successful generation.

Local regression tests use simulated HTTP, Slurm/container commands and SSH;
they do not log in to Leonardo or allocate GPUs:

```bash
python3 -m unittest discover -s tests -v
```

## Knobs (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `TEAM` | `team_guardrails` | folder under `$WORK` (serve/ calls the same folder `BASE`) |
| `VLLM_TAG` / `VLLM_IMAGE` | `latest` / `docker://vllm/vllm-openai:$VLLM_TAG` | image |
| `MODEL_ID` | `Qwen/Qwen3-0.6B` | HF repo, downloaded in step 0 |
| `MAX_MODEL_LEN`, `GPU_MEM_UTIL` | `8192`, `0.90` | vLLM memory settings |
| `VLLM_EXTRA_ARGS` | empty | appended to `vllm serve` |
| `ENDPOINT_NAME` | derived from job name and full model ID | unique service endpoint name, stable across restarts |
| `CUDA_COMPAT` | `auto` | use the image's CUDA forward-compat libs when the host driver is older than R580 |
| `NCCL_*`, `GLOO_*` | unset | forwarded into the container |

## What to send back when something breaks

The full `.out` log of the failing step, plus from `probe-<id>.out`:
the `network interfaces`, `InfiniBand devices` and `GPU` sections.
Strip the API key and any proxy password before sharing.
