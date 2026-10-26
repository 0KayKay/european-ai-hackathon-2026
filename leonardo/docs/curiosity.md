# Moving to Curiosity v2 while Leonardo is in maintenance

Leonardo goes into maintenance on day 3 or 4 of the hackathon. The organisers offer NVIDIA's
**Curiosity v2** cluster for that time. This page compares the two, lists what our Leonardo setup can
take along and what has to change, and gives a plan for the first hour on the new cluster. It is based
on NVIDIA's slide deck "Connecting to Curiosity v2" (last updated 8 Sept 2026) and on this folder's code.
**Nothing here has run on Curiosity yet.**

## Summary

- **Slurm, yes; Singularity, probably.** Curiosity runs Slurm 25.05. The deck lists **Apptainer**,
  rootless Docker and Enroot/Pyxis as container options. Our launcher already accepts `apptainer` in place
  of `singularity`, so the job structure can stay as it is.
- **The large-model runtime does not carry over.** All large profiles use `vllm-backport-sm80-0.13.1`, a
  community build for A100 (SM 8.0). A DGX-B300 is Blackwell Ultra (compute capability 10.3), so that
  image is useless there. We need an upstream vLLM image built for CUDA 12.9+ or 13. The stock
  `vllm-0.31.0` runtime is a CUDA 13 build, so it is the first candidate. On Leonardo it served Qwen,
  but it has never been tried with GLM or DeepSeek.
- **The hardest multi-node work goes away.** One DGX-B300 has 8 GPUs with about 288 GB each, roughly
  2.3 TB per node. Two Leonardo nodes had 8 × 64 GB = 512 GB. Every model we serve fits on **one node,
  usually on 2–4 GPUs**, so the multi-node layouts, the PP KV patch, the NCCL/EP smoke hook and the
  Engram warm-up are no longer needed. The context and batch limits we set to fit A100 memory (2,048
  tokens, 2–4 sequences, `--enforce-eager`) can be raised a lot.
- **Storage is the real problem.** The team gets **300 GB** shared and **50 GB** per user. That is
  not enough for one of our large checkpoints plus the 100 GiB reserve that `install/checkpoint.sbatch`
  requires: Qwen3-235B-FP8 is about 239 GB and GLM-5.3 is 328 GB. Node-local `/tmp` and `/raid` are
  wiped when the job ends. Either the organisers give us more space, or weights are downloaded at the
  start of each job.
- **There is no known CPU queue.** On Leonardo, installs and the router run on `lrd_all_serial`, a CPU
  partition with internet. Curiosity has a CPU-only login node, where only Slurm commands are allowed,
  and 10 GPU nodes. If GPU nodes can be shared and have internet, it is a settings change
  (`init --cpu-partition <GPU partition>`). Otherwise we skip the router and forward to one server.
- **Access goes through Axis.** SSH runs via `ssh.axisapps.io` with a one-time key as the username.
  `scp` works according to the deck. **Port forwarding (`ssh -L`), which `cli.py forward` uses, is not
  confirmed.**
- **Two deck sections are missing.** The table of contents lists "Common scheduler commands" and
  "Containers (Apptainer, Rootless Docker, Enroot/Pyxis)", but the PDF skips those slides. The
  "Curiosity v2 Doc" app in the Axis portal probably has them. Read it first.

Expected effort, once the open questions are answered: **about half a day** to get one large model
serving through our CLI on one B300 node. Most of that time is the image pull and the weight download.
For a model that only needs the stock runtime, very little code changes.

## Side by side

| | Leonardo Booster (CINECA) | Curiosity v2 (NVIDIA) |
| --- | --- | --- |
| GPU nodes | thousands, 4 × A100 64 GB each | **10 × DGX-B300**, 8 × B300 (~288 GB HBM3e) each |
| GPU architecture | Ampere, SM 8.0 | Blackwell Ultra, SM 10.3, native FP8/FP4 |
| Host driver | 535: needs CUDA forward compatibility | new, probably ≥ 580 (check): forward compatibility not needed |
| Scheduler | Slurm | Slurm 25.05 (`slurm/slurm/25.05` module) |
| Partitions / QoS | `boost_usr_prod`, `boost_qos_lprod` (2 h), `boost_qos_dbg` (30 min), `lrd_all_serial` (CPU, internet) | **unknown**: see [first hour](#first-hour-on-curiosity) |
| Login | `login0X-ext.leonardo.cineca.it`, personal CINECA login, 2FA | Axis portal → `ssh ssh.axisapps.io -l <one-time key>`, or web SSH |
| Login node | may run light work (Python 3.6) | **Slurm commands only**: no container builds, no environment setup (Ubuntu 22.04, Python 3.10) |
| Containers | Singularity | Apptainer, rootless Docker (`rootless-docker/1.75`), Enroot/Pyxis |
| Shared storage | Lustre `$WORK`, several TB | NFS, **300 GB per team**, **50 GB `$HOME` per user**, deleted at the end, no backup |
| Node-local storage | – | `/tmp` and `/raid/$SLURM_JOB_ID`, deleted when the job ends |
| Internet on compute nodes | no (CPU queue only) | **unknown** |
| Size of the shared pool | large | 10 nodes for every team at the event: expect queues and limits |

## What carries over

| Part | Carries over? | Notes |
| --- | --- | --- |
| `cli.py` (laptop) | yes, with settings | `LEONARDO_HOST=ssh.axisapps.io`, `LEONARDO_USER=<auto-login username from Axis>`. The 30-minute `ControlPersist` connection is useful here because every new connection may need a new key from the portal. `LEONARDO_BASE` points at the team folder, not `$WORK`. |
| `serve/team.py`, `inference.py`, `lifecycle.py`, `registry.py`, `test_endpoint.py`, `router.py` | yes | Standard library and generic Slurm (`sbatch`, `squeue`, `scancel`, `scontrol`). Python 3.10 on the login node is fine. |
| `serve/serve_model.sbatch`, `serve/node.py` | yes, with two caveats | Apptainer is detected (`APPTAINERENV_` prefix). Caveats: `VLLM_ENABLE_CUDA_COMPATIBILITY=1` is always set ([node.py](../serve/node.py)), which is meant for old drivers and should be switched off and tested on B300; and `/dev/infiniband` is always bound, which fails if a node lacks it. |
| Profiles, check suites, API key and registry design | yes | The checks stay valid. |
| `install/runtime.sbatch`, `install/checkpoint.sbatch` | mostly | They call `singularity` and set `SINGULARITY*` variables directly. Apptainer usually provides a `singularity` alias and still reads those variables (with a warning). They need internet on the partition they run on. The 100 GiB reserve check fails on a 300 GB quota. |
| `site.example.json`, `init` defaults | settings only | Partition, QoS and CPU partition are Leonardo names: pass `--partition`, `--qos` and `--cpu-partition` to `init`. Every job passes `--account` and `--qos`, so we need valid values for both. |
| `share` (POSIX ACLs) | maybe | `setfacl` often does not work on NFS mounts. The team folder is probably group-shared already, so this may not be needed. |
| `diagnostics/probe.sbatch`, `nccl_check.sbatch` | no, use the new scripts | The `#SBATCH` lines fix `boost_qos_dbg`, `gpu:4` and `480GB`. The Curiosity scripts below replace them. |
| Runtime `vllm-backport-sm80-0.13.1` (and `-ppkv1`) | **no** | A100-only build. GLM and DeepSeek used it for sparse MLA on SM80. On Blackwell, upstream vLLM should have sparse MLA natively; that needs checking. |
| Runtime `vllm-0.31.0` | probably | CUDA 13 build. `curiosity_node.sbatch` with `CHECK_VLLM=1` checks that it runs on SM 10.3. |
| Profiles `qwen235b`, `glm53`, `deepseek41`, `deepseek41-16gpu` | no, new B300 versions needed | Two to four nodes with TP×PP, sized for A100. On B300 they become one node, PP=1. |
| Profile `qwen06b` | yes | One GPU, stock runtime: the first smoke test. |
| Hooks `nccl_ep`, `warm_engram`, PP KV patch | not needed | They exist for multi-node A100 runs. |
| Cluster link (chisel to GKE) | unknown | It needs outbound internet from the router's node and the organisers' approval, as on Leonardo. |
| `validation/` evidence | no | This is new hardware, so every profile needs a new GPU run. |
| agentgateway routes | rename | The served names end in `-leonardo`. Use new profiles with new names, for example `-curiosity`, and re-render with `tools/render_agentgateway.py`. |

## Storage options

The team folder (300 GB) holds the runtime image (about 10 GB per SIF) and at most one large checkpoint.
In order of preference:

1. **Ask the organisers** for more shared space or a persistent scratch area. A large checkpoint is
   240–330 GB.
2. **Download the weights into `/raid/$SLURM_JOB_ID` at the start of each job**, if GPU nodes have
   internet. DGX nodes have fast local NVMe, and loading from it is quicker than from NFS. At 500 MB/s a
   239 GB checkpoint takes about 8 minutes; `curiosity_node.sbatch` measures the real speed. This needs a
   small change: a "fetch into node-local scratch" step before `node.py` starts vLLM, and a
   `model_path` under `/raid`.
3. **Choose smaller checkpoints.** One B300 GPU holds an FP8 70B model with room to spare. We could
   also use the FP8 or FP4 version of a large model instead of BF16.

`checkpoint.sbatch` requires the checkpoint size plus 100 GiB free. On 300 GB this fails for Qwen3-235B
(239 GB). Lower the reserve, or download outside the team folder.

## A B300 profile sketch (not run)

This is how `qwen235b` might look on one node with 4 GPUs: native FP8, no Marlin, no eager mode, a much
larger context. The numbers are a starting point for the first test run, not validated settings.

```json
{
  "id": "qwen235b-b300",
  "served_model": "qwen3-235b-a22b-fp8-curiosity",
  "runtime": "vllm-0.31.0",
  "resources": {"nodes": 1, "gpus_per_node": 4, "cpus_per_task": 32, "memory": "400GB", "time": "02:00:00"},
  "parallelism": {"tensor": 4, "pipeline": 1},
  "engine_args": ["--quantization", "fp8", "--gpu-memory-utilization", "0.90",
                  "--max-model-len", "32768", "--max-num-seqs", "32",
                  "--reasoning-parser", "qwen3", "--tool-call-parser", "hermes", "--enable-auto-tool-choice"]
}
```

Rough memory: the weights take about 239 GB. 4 × 288 GB × 0.9 leaves about 800 GB for the KV cache. Even
2 GPUs would work. GLM-5.3 (328 GB BF16) fits on 4 GPUs as well. Whether stock vLLM serves GLM-5.3 and
DeepSeek-V4.1 (the `deepseek_v41` tokenizer and parsers, `--engram-config`, sparse MLA) has to be checked
in the image. With `CHECK_VLLM=1`, `curiosity_node.sbatch` greps for those options.

If a team gets only part of a node, `gpus_per_node: 2` or `4` lets two models share one node, provided the
partition does not hand out whole nodes. `curiosity_login.sh` shows the `OverSubscribe` and `Exclusive`
settings.

## First hour on Curiosity

Before the day starts, open the **Curiosity v2 Doc** app in the Axis portal and read the scheduler and
container pages that the PDF leaves out. Then:

1. **Log in** through the Axis portal ("Curiosity-v2-login", desktop SSH client). Copy the hostname and
   auto-login username, then `scp` both scripts across:

   ```bash
   scp leonardo/curiosity/curiosity_login.sh leonardo/curiosity/curiosity_node.sbatch <auto-login username>@ssh.axisapps.io:~
   ```

2. **On the login node** (Slurm queries only; a few seconds):

   ```bash
   bash curiosity_login.sh 2>&1 | tee curiosity-login.txt
   ```

   Note down the account, the GPU partition, the QoS, node sharing, the Pyxis/Apptainer modules and
   where the team folder is.

3. **On a GPU node** (1 GPU, 30 minutes):

   ```bash
   TEAM_DIR=<team folder> sbatch -A <account> -p <partition> curiosity_node.sbatch
   ```

   While it runs, test the two connections the router depends on: `curl http://<node>:<port>/` from the
   login node, and `ssh -N -L 18000:<node>:<port> ssh.axisapps.io -l <key>` from the laptop. The log prints
   both commands. If the first one succeeds, run it again with `CHECK_VLLM=1` and `--time=01:00:00`.

4. **Decide on the path** from the answers:

   | Finding | Then |
   | --- | --- |
   | Apptainer works with `--nv`, GPU nodes have internet, nodes can be shared | `cli.py init --account A --partition P --qos Q --cpu-partition P`, then `install qwen06b`, `up qwen06b`, `router up`, `forward`. This is the existing flow with settings only. |
   | No internet on GPU nodes | Pull the image and download weights on the laptop or another machine and `scp`/`rsync` them in. That is slow for hundreds of GB: use small models, or ask the organisers. |
   | Apptainer missing, Pyxis works | Change `node.py` and the install scripts to `srun --container-image=... --container-mounts=...`. This is about half a day of work. Enroot is NVIDIA's own path on DGX clusters, so it is the most likely to work. |
   | `ssh -L` through Axis is blocked | Serve the app from the cluster side (cluster link, if approved), or use the web SSH and `curl` for demos only. |
   | Nodes are allocated whole | Run the router inside the model server job instead of as its own job (a code change), or skip it: `serve/client.py tunnel` forwards to one server directly. |

5. **First real test:** `qwen06b` on one GPU with `--time 00:30:00`. Then one large model on one node.

## Questions for the organisers

1. Which Slurm account, partition and QoS do we use? What are the time and GPU limits per team? Are
   GPU nodes shared or allocated whole?
2. Do compute nodes have internet (Hugging Face, Docker Hub, nvcr.io), directly or through a proxy?
3. Where is the team folder, and can it be larger than 300 GB for one day? Is there persistent
   scratch space, or does `/raid` survive between jobs?
4. Does Axis allow SSH port forwarding (`ssh -L`) to compute nodes?
5. Is Apptainer installed on the compute nodes? (The deck lists it, but there is no module for it.)
6. Can a job keep running for a whole day, for example a router that renews itself every 4 hours?
7. May a job open an outbound connection to our GKE cluster (the chisel cluster link)?

## What needs to change in the code (not done yet)

These are the changes for the paths above. They wait until the first hour has answered the questions,
because each one depends on an answer.

1. Make `VLLM_ENABLE_CUDA_COMPATIBILITY` a setting in `inference-site.json`, off on Curiosity
   ([node.py](../serve/node.py), [nccl_task.sh](../diagnostics/nccl_task.sh)).
2. Bind `/dev/infiniband` only if it exists ([node.py](../serve/node.py)).
3. Let the install scripts use `apptainer` when it is the only container tool, as `node.py` already does
   ([runtime.sbatch](../install/runtime.sbatch), [checkpoint.sbatch](../install/checkpoint.sbatch)).
4. Make the checkpoint free-space reserve a setting.
5. Add B300 profiles (`*-curiosity`) on an upstream runtime, update the manifest, and render the
   agentgateway routes again.
6. Depending on the answers: download weights into node-local scratch per job, a Pyxis launcher, or the
   router inside the model server job.

Each change in `serve/` invalidates part of the recorded Leonardo validation
(`tools/check_repository.py` reports the drift). Keep Curiosity changes in separate settings, profiles
and runtimes where possible, so that the Leonardo profiles stay byte-identical for when Leonardo returns.

## Before the cluster account is removed

According to the deck, `$HOME` and the team folder are deleted, with no backup, when access ends. Copy
validation evidence and logs out with `scp` before the end of the hackathon. Keys and tokens are never
copied out.
