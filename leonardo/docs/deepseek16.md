# DeepSeek on sixteen GPUs

`deepseek41-16gpu` adds the engine configuration that completed Leonardo job
`61481897` on 20 October 2026. It is separate from the existing eight-GPU
`deepseek41` profile, including its API model name and private cache namespace.

| Setting | `deepseek41` | `deepseek41-16gpu` |
| --- | --- | --- |
| Nodes / A100 GPUs | 2 / 8 | 4 / 16 |
| Tensor / pipeline parallelism | 8 / 1 | 8 / 2 |
| Expert parallelism | enabled | enabled |
| Engram tables | CPU offload | GPU-resident (`cpu_offload=false`) |
| Active sequences | 2 | 8 |
| Context / batched-token limit | 2,048 / 2,048 | 2,048 / 2,048 |
| Safetensors load strategy | lazy | native background prefetch |
| Pre-start hooks | EP probe, Engram warming | none (the probe supports PP1 only) |
| Runtime | `vllm-backport-sm80-0.13.1` | `vllm-backport-sm80-0.13.1-ppkv1` |
| API model name | `deepseek-v4.1-flash-1017-leonardo` | `deepseek-v4.1-flash-1017-16gpu-leonardo` |
| Direct-client port | 18084 | 18086 |

Both profiles use the same pinned checkpoint and image. The new profile requests
32 CPUs and 480 GB host memory **per node**, for up to two hours. Coordinate its
four-node allocation with the team before starting it.

## Required pipeline-parallel KV cache patch

The pinned backport can allocate KV tensors for layers absent from a worker's
projected cache group, leading to `StopIteration` during PP startup. The narrow
import hook filters allocations to `group.layer_names`, preserving empty group
positions. It does not change weights, precision or model computations.

The two patch files under `serve/runtime_patches/pp_kv_empty_groups/` are copied
byte-for-byte from the runtime used in the successful job. This is a local
workaround, not a claim that a released upstream version contains the fix.

The hook only intercepts `vllm.v1.core.kv_cache_utils`, accepts source SHA-256
`3326435f857c903711939d4f6b2776395262729b464b3973949c1234a163ff3f`, and requires
exactly one matching function and allocation loop. It compiles verified source
instead of loading an old `.pyc`. Unknown source raises an import error; failure
to install the hook terminates Python. The hook runs inside the modern-Python
vLLM container; the installer and CLI remain compatible with Leonardo's login
Python. An existing different overlay `sitecustomize.py` is rejected rather
than overwritten.

The runtime catalog pins the patch file hashes. Installation verifies them,
imports the patched allocator on CPU before registration, and records all
overlay hashes. Run preparation checks the installed patch hashes again. When
the matching base runtime is already installed, the installer checks its hashes,
reuses its immutable SIF and copies its overlay into staging. Otherwise it pulls
the same pinned image and installs the pinned TileLang wheel. Registration
creates a separate runtime; the base runtime is never patched in place.

## Use from the repository root

Use the same `LEONARDO_USER`, `LEONARDO_HOST` and `LEONARDO_BASE` as the team CLI.
If the checkpoint is already installed, it is reused; there is no new weights
download for this profile. The extra patched runtime must still be installed:

```bash
./leonardo/cli.py install deepseek41-16gpu
./leonardo/cli.py status
./leonardo/cli.py logs <install-job-id> --follow
```

Once installation is ready, start a coordinated validation run:

```bash
./leonardo/cli.py up deepseek41-16gpu
./leonardo/cli.py logs deepseek41-16gpu --follow
./leonardo/cli.py status
```

The historical cold start was about 15 minutes excluding queue time. The default
two-hour allocation leaves room for startup and checking the new path. A
30-minute debug allocation may expire before a cold start and tests finish.
The server must pass the same seven `chat_tools` API checks before `READY`.
The two-hour limit is a Slurm resource request, not a guarantee of availability.

Start the router and SSH forwarding as in the [main guide](../README.md#call-the-models).
Use base URL `http://127.0.0.1:18000/v1`, your private router token as the API key,
and model `deepseek-v4.1-flash-1017-16gpu-leonardo`. Requests should include
`"chat_template_kwargs": {"thinking": false}`. The router port stays 18000;
18086 is only for the optional direct-to-server client.

Stop the model allocation when finished:

```bash
./leonardo/cli.py down deepseek41-16gpu
```

`down` requests immediate Slurm cancellation; it does not provide request draining.
The pinned backport defaults to a zero-second engine shutdown wait and can log
`EngineDeadError` plus TCPStore/DistNetworkError warnings during that stop. The
[shutdown investigation](../validation/runs/2026-10-20-deepseek41-16gpu-cli/shutdown-root-cause.json)
and [CPU reproduction](../validation/runs/2026-10-20-deepseek41-16gpu-cli/cpu-abort-diagnosis.json)
trace this to forced engine teardown and output-handler cancellation order. In
the measured run the warnings followed the operator cancellation and all Slurm
steps, endpoints and the tunnel were removed. Errors during serving still need
investigation. Graceful draining and persistent IPC-leak checks remain unvalidated.

The generated optional agentgateway route includes the new API model name. This
does not enable the cluster link or change the app's default model.

## What was validated

The [historical evidence](../validation/runs/2026-10-20-deepseek41-16gpu/README.md)
records seven passing startup API checks, an actual-function CPU allocator
regression, and 14 English finite load bursts with 422/422 successful requests.
Each request produced 128 completion tokens. The client ran in a CPU-only
Singularity process on the allocated inference head node over localhost HTTP.

The [fresh CLI/router run](../validation/runs/2026-10-20-deepseek41-16gpu-cli/README.md)
validated this separate profile, model name and patched runtime using commit
`5fccaf4a21c2c1146b5dff07395b7ad4f1cb6d25` and CLI release `a4c0ab387e5527fe`. Installer job `61574015` built and
registered the separate overlay from verified existing base assets and imported
the patched real allocator on CPU. It reused the existing pinned checkpoint.

GPU job `61574938` allocated four nodes/sixteen GPUs with `boost_qos_dbg` and a
30-minute limit. Health was reached after 1096.03 seconds, excluding queue
wait; all seven startup checks passed before `READY`. From the laptop, two more
seven-check suites passed through the unmodified `cli.py forward` and CPU router
job `61574567`. These included authenticated model selection, SSE content and
`[DONE]`, forced function-call JSON, and four concurrent requests. The function
was not executed. Model and router shutdown, endpoint withdrawal and tunnel
cleanup were verified. The cluster link remained disabled. Intentional cancellation
also produced an AsyncLLM output-handler traceback and TCPStore/DistNetworkError
warnings after the cancellation marker. Resource/endpoint cleanup passed; see
[shutdown observations](../validation/runs/2026-10-20-deepseek41-16gpu-cli/shutdown-observations.json)
for the separate teardown diagnostics.

All executed `serve/` bytes and the profile are preserved in the fresh evidence.
The profile's embedded `validation` description predates this rerun; the linked
run and `validation/validated_code.json` record the current status without
changing the GPU-tested source bytes.

This was a short functional test with one operator and English prompts, not a
new load benchmark. The earlier 422-request burst test does not establish
sustained capacity, multi-account behavior, long-context performance or a
CPU-offload load result. A fresh image/weights download, cluster link/GKE and
unattended operation were not tested by this rerun.
