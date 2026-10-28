# B300 Slurm jobs and remote control

The laptop CLI orchestrates `serve/manage.py` on the authenticated login node.
All heavy preparation and inference occur inside `serve/run.sbatch` on the
allocated compute node. These are candidate workflows; see [validation](validation.md).

| Action | GPUs | Work inside allocation | Outcome |
| --- | --- | --- | --- |
| `install safety` | 1 | Image import and pinned safety weights | Temporary cache receipt; no API. |
| `install ultra` / `install nemotron` / `install glm53` | 4 / 5 / 8 | Selected image/checkpoint preparation | Temporary cache receipt; no API. |
| `up safety` | 1 | Preparation, BF16 safety vLLM, four acceptance checks | Bounded authenticated safety API. |
| `up safeguard` | 1 | Preparation, native MXFP4/BF16 GPT-OSS-Safeguard, four checks | Policy-based JSON verdict API. |
| `up ultra` | 4 | Preparation, NVFP4 Ultra TP4/PP1, seven checks | Bounded Ultra API. |
| `up nemotron` | 5 | Both checkpoints; safety on GPU 5, Ultra on GPUs 1–4 | Two separate keys/APIs in one allocation. |
| `up glm53` | 8 | Native GLM TP8/PP1, original seven checks | Retained GLM API; no PP guard overlay. |

The pair/Ultra/GLM profiles request 64 CPUs and 960G host memory. Standalone
safety and Safeguard request 16 CPUs and 128G. One coordinator task does not mean one GPU.
Maximum duration is four hours; both classifiers default to two hours. Queue wait is
outside allocation time, while import/download/loading/tests consume it.

The manager supplies ordinary `sbatch` arguments: account, partition, QoS,
`--gpus=N`, CPUs, host memory, unique job name, private log/run directory,
`--signal=B:TERM@120` and the bounded time. `--reuse-job` additionally selects the
old cache's node after verifying owner, image, termination and old cleanup.

## Control without the laptop CLI

Use an already copied complete release on the cluster. Set the real base path
in the JSON, and run from its parent directory containing `serve/`:

```bash
source /etc/profile.d/modules.sh
module load slurm
python3 serve/check_release.py
python3 serve/manage.py '{"action":"init","base":"/replace/with/actual/base","account":"eai-hackathon","partition":"hackathon","qos":"eai-team09"}'
python3 serve/manage.py '{"action":"up","base":"/replace/with/actual/base","profile":"safety","time":"00:30:00"}'
python3 serve/manage.py '{"action":"status","base":"/replace/with/actual/base"}'
```

`init` is once per base. The actual account/QoS may differ. The manager creates
the private snapshot, `RUN_CONFIG.json` and submission receipt, then submits
`sbatch ... PRIVATE_RUN/serve/run.sbatch PRIVATE_RUN`. Do not submit the bare
batch script without a prepared private run, and do not repeat a command whose
submission outcome is uncertain. `fetch` returns keys over the private SSH channel
for the laptop CLI; do not run it into a public terminal or log.

## Inspecting and cancelling

`status` reads current `squeue` and this login's private runs. `NOT_IN_QUEUE` is
not by itself a successful model outcome: inspect `state.json`, `slurm.log` and
Slurm accounting when diagnosing a terminated allocation. An expired ready file
does not make an endpoint live.

`down` accepts a profile, run ID or recorded job ID, verifies ownership/name,
requests `scancel`, then checks actual queue disappearance. `cleanup_pending`
means more checking is required. The batch exit trap removes only that run's
endpoint/known key files; Python also stops its owned vLLM process groups.
No shared SSH master or another allocation is cancelled.

Reuse requires a completed prior run's `cache.json`, `owned_servers_stopped=true`
and `session_keys_removed=true`. A killed process without those records is not
automatically treated as a clean reusable cache; inspect it first. Temporary
scratch is not a durable model installation.
