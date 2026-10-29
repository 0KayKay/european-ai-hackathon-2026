> Historical operator configuration reference. The new candidate `serve/` runtime is documented in the [main guide](../README.md); the original experiment directories mentioned below are not bundled verbatim.

# B300 operator notes: recorded GLM and Nemotron configurations

Prepared: 27 October 2026; updated 28 October, Europe/Berlin. These notes describe
the local sources and saved actual executions. The original documentation step
did not deploy a server. The separately authorized 28 October connection check
used fresh job `12174`; it did not repeat the 280-call comparison.

## Resource planning

The recorded partition is `hackathon`, account `eai-hackathon`, QoS `eai-team09`.
The observed team GPU limit was eight; verify the current allocation before
starting another job. GLM uses eight GPUs; the two Nemotron servers together
use five. These complete deployments cannot run concurrently under that limit.

GPU allocation, container preparation, checkpoint download, engine loading,
acceptance testing and endpoint publication are distinct phases. A job marked
`RUNNING` does not establish API readiness. `QOSGrpGRES` was an observed GPU-quota
queue reason. Inspect the current job instead of resubmitting a duplicate.

The earlier launchers requested one node, one coordinator task, 64 CPUs, 960 GiB
host RAM and at most four hours. `--ntasks=1` is not a one-GPU request. Container
preparation, downloads, compilation and inference belong inside the allocation.

## GLM: verified functional configuration

The table and command below record the **historical** 2,048-token run, which served GLM as
`glm-5.3-flash-leonardo`. The current launcher serves it as `glm-5.3-flash-b300`
and configures 262,144 tokens, 32 sequence slots and 16,384-token chunked prefill
on the same TP8/PP1 layout; those settings are not yet GPU-validated. See
[GLM context sizing](glm-context.md) for the KV cache measurement at 65,536 tokens, the vLLM startup lines to check and the two-TP4-server option.

| Setting | Actual successful job 11393 |
| --- | --- |
| Checkpoint | `zai-org/GLM-5.3-Flash` |
| Revision | `91b69215804f2d2036a8867f6ce44848e278dcda` |
| Weight shards / bytes | 62 / 328,337,455,672 |
| Parallelism | One node, eight B300 GPUs, TP8/PP1, native model implementation |
| API alias | `glm-5.3-flash-leonardo` |
| Listen address | `127.0.0.1:8000` on the compute node |
| Engine dtype / KV | BF16 requested / `auto` |
| Context / sequence slots | 2,048 / 4 |
| Runtime image | `vllm/vllm-openai:glm53-flash`, pinned index digest below |
| Recorded installed vLLM | `0.28.1rc1.dev580+g385dce36b` |

Image index digest:

```text
sha256:819ec9c063412e5730d1b0e82046ba540d1bf991f3c4f661a849aae8a0c52374
```

The successful server command, with only the node-local checkpoint path replaced
by an operator-supplied variable, was:

```bash
# Inside the pinned runtime, in an allocated eight-GPU B300 job.
# GLM_CHECKPOINT must point to the verified snapshot of the pinned revision.
# VLLM_API_KEY must already contain a private per-session key.
vllm serve "$GLM_CHECKPOINT" \
  --served-model-name glm-5.3-flash-leonardo \
  --host 127.0.0.1 --port 8000 \
  --tensor-parallel-size 8 --pipeline-parallel-size 1 \
  --distributed-executor-backend mp --dtype bfloat16 \
  --gpu-memory-utilization 0.90 --max-model-len 2048 \
  --max-num-seqs 4 --max-num-batched-tokens 2048 \
  --enforce-eager --disable-custom-all-reduce \
  --limit-mm-per-prompt '{"image":0,"video":0}' \
  --safetensors-load-strategy eager \
  --reasoning-parser glm45 --tool-call-parser glm47 \
  --enable-auto-tool-choice --kv-cache-dtype auto
```

This is a reference engine command, **not a complete installation script**. It
requires the allocated resources, pinned runtime, downloaded checkpoint and
writable cache setup first. The command has not been re-executed as part of this
documentation package. Do not run the historical TP4/PP2 default: that B300
attempt failed because the official wrapper did not support the required
pipeline interface. The successful TP8/PP1 run used no private PP patch.

The original project launcher, `Cluster_B300/glm53-comparison/run.py`, starts the
engine, performs its selected evaluation and stops the server in `finally`,
deleting its temporary key. It does not implement a retained colleague service.
The original API evaluation invocation is:

```bash
# From the original glm53-comparison deployment directory, on the cluster.
B300_PARALLELISM=tp8-pp1 B300_EVALUATION=api \
  sbatch --parsable --output=slurm-%j.log run-gpu8.sbatch
```

Those server files are intentionally not bundled here. Use the existing
operator-controlled deployment checkout; verify it against the recorded source
before submitting. The command above submits a new allocation when deliberately
executed by an operator; it was not run for this handoff.

For sustained colleague access, a separately bounded service lifecycle and a
verified route into the compute-node/container namespace are still needed.
Changing the bind address alone does not validate routing or authorization.
Publish an endpoint only after authentication, expected model ID and actual
requests succeed from the intended client route.

## Nemotron: verified five-GPU service configuration

| Setting | Ultra | Safety specialist |
| --- | --- | --- |
| Repository | `nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-NVFP4` | `nvidia/Nemotron-Content-Safety-Reasoning-4B` |
| Revision | `02462641f13d3af838b904f48195b9bb8a1e4ebc` | `29da6ec41d9c710d937afcd41f65e241ab822792` |
| Weight shards / bytes | 113 / 352,308,689,576 | 2 / 9,942,783,112 |
| Weight format | Official NVIDIA NVFP4 | BF16 |
| GPU allocation | 4, TP4/PP1 | 1, TP1/PP1 |
| Private-IP port | 18085 | 18086 |
| Context / slots | 8,192 / 4 | 4,096 / 4 |

Both ran in official `vllm/vllm-openai:v0.22.0`, image digest:

```text
sha256:0fec7ec5f3e6bc168e54899935fb0557da908a4832a1dbc88e2debcf2f889416
```

Ultra uses expert parallelism, `qwen3_coder` for tool calls and `nemotron_v3`
for reasoning parsing. Both engines request BF16 dtype and GPU memory utilization
0.85; this does not convert the Ultra checkpoint's NVFP4 weights to a BF16
checkpoint. The v0.22 logging flag used is `--no-enable-log-requests`.

The operator-controlled source is `Cluster_B300/nemotron/`. Its `run.sbatch`
requests five GPUs; `server.py` verifies the checkpoint metadata, starts the two
models on disjoint GPUs, runs real acceptance checks, then writes
`runs/JOB_ID/endpoint.json` and distinct mode-600 session keys. It can keep both
APIs alive within the allocation. The archived job `11962` retained them only
until the original **20:17 CET** deadline; later repairs did not extend it.

From that existing deployment directory, an operator can deliberately submit:

```bash
sbatch --parsable --output=slurm-%j.log run.sbatch
# Save the returned job ID; inspect runs/JOB_ID/state.json before distributing access.
```

No operator SSH credentials, private `remote.py` state, job receipt or session
endpoint is distributed in this package. The included client and validation
helper are identical to the local deployment sources. Both were exercised from
the Mac in the [28 October verification](MAC_CONNECTIVITY_20261028.md). That
separate test preserves new response evidence and corrects the interpretation
of the overwritten historical check.

## Storage and site runtime requirements

GLM needs approximately 328 GB of weight storage; the Nemotron pair needs
approximately 362 GB, before runtime and compilation caches. Use owned
compute-node scratch for the tested workflow. Team shared storage was documented
as 300G and the personal home as 50G. `/tmp` is temporary and may disappear when
the job ends; this was not a permanent weight installation.

Inside the container, set writable scratch locations for TMPDIR, XDG, Hugging
Face, Triton, Torch, CUDA, TVM/TileLang and FlashInfer caches. Host-side cache paths
are not automatically valid container paths. In later site configurations, the
following read-only bind mounts were required to preserve the site Slurm prolog
and Python hook:

```text
/cm/shared/apps/slurm/scripts:/cm/shared/apps/slurm/scripts:ro
/cm/shared/apps/python_hooks:/cm/shared/apps/python_hooks:ro
```

The successful Nemotron deployment also copied the installed `flashinfer_cubin`
package byte-for-byte into private writable scratch and verified those bytes
before preflight. Setting only `FLASHINFER_CUBIN_DIR` did not solve the read-only
bundled-package failure. The operator must retain `cubin_overlay.py` and its
preflight in a complete Nemotron deployment; the client-only package does not
replace that runtime repair.

Reuse a node-local cache only after checking ownership, the old job's termination,
server/key cleanup, checkpoint revision and available storage. A cache on one
node is not automatically accessible on a different node.

## Readiness, sharing and shutdown

Before sharing, verify the current allocation, authenticated catalog, exact
returned model, a real completion, and the intended colleague connection path.
Give colleagues a fresh endpoint and appropriate key through the private team
process, never through GitHub. Do not advertise an old IP as a durable endpoint.

Cancel only the explicitly identified owned allocation, then check actual queue
termination and remove its expired keys, endpoint and owned forwards. Do not
close another user's allocation or a shared SSH master. Keep result artifacts
separate from credentials. Export required nonsecret results before event access
or temporary scratch is removed.

