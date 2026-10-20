# History, validation and what was consolidated

## Timeline

| Date | What happened |
| --- | --- |
| 7 Oct | Starter kit (`leonardo-vllm`, now `bringup/`): stock vLLM 0.31 with Qwen3-0.6B on 1 GPU and on 2 nodes × 4 GPUs (TP4/PP2, no Ray). NCCL used InfiniBand with GPUDirect RDMA. Reached from a laptop through `ssh -L` via the login node. |
| 8 Oct | Qwen3-235B-A22B-FP8 on stock vLLM 0.31: 2 nodes, TP4/PP2, Marlin FP8 weight-only kernels, 2,048-token context, 4 sequences. Eager safetensors loading after slow mmap reads on Lustre. |
| 13 Oct | GLM-5.3-Flash. Stock vLLM had no usable sparse-MLA backend on A100, so it switched to the community SM80 backport (vLLM 0.13.1) and repaired TileLang in a separate overlay. |
| 17 Oct | Junghwa published the work as [Jules0315/Guardrails-vllm](https://github.com/Jules0315/Guardrails-vllm), with personal paths and accounts replaced by placeholders (`team_vllm`, `YOUR_PROJECT`). |
| 18 Oct | Common framework: one launcher, model profiles, shared runtime. GLM (job 61299807) and DeepSeek-V4.1 (job 61300901) passed 7/7 API checks at startup and in 2 SSH-tunnel repeats. |
| 19 Oct | Qwen3-235B moved onto the common runtime (job 61389137, 7/7 ×3). The API port base moved from 21000 to 31000 after it was found that API and master ports could collide for job suffixes 8000–8999. |
| 20 Oct | Copied into this repository and consolidated (below). |

Startup times to a healthy API (cold caches, excluding queue): Qwen 7.0 min, GLM 13.3 min, DeepSeek 25.8 min.

## Compatibility problems that were solved

1. No A100 sparse-MLA backend for GLM in stock vLLM: use the pinned SM80 backport image
   `lazymio/vllm-backport@sha256:0fc33cd7…`. The image names no source commit, so a source-to-binary match
   was not independently verified.
2. Incomplete TileLang in that image: install the official 0.1.12 wheel (SHA-256 pinned) into a separate
   Python overlay. mHC was checked numerically against PyTorch on an A100.
3. Read-only cache locations: FlashInfer, TileLang, Triton and other caches are redirected to writable storage.
4. Too little contained `/tmp` during installs: use work storage.
5. Unix socket path-length errors: the runtime temp dir is bound to a short container path.
6. Host driver 535 vs CUDA 13 images: `VLLM_ENABLE_CUDA_COMPATIBILITY=1`.

## Validation scope

Evidence is in [`validation/`](../validation/validated_code.json): per-run startup times, all three API suites
per model, and checkpoint metadata digests. Absolute checkpoint paths are redacted; keys and raw logs were
never published.

Validated: one operator account, two nodes and eight A100s per model, short functional API checks. Every
server was cancelled afterwards, so no endpoint or key from these runs is live.

Not validated yet:

- Other team accounts running servers or connecting as clients (read access was granted, but no run was made).
- Sustained or workshop-scale load, long context (all profiles cap at 2,048 tokens), multimodal input.
- Running moderation models next to a main model.
- Unattended operation: anything other than a person's own SSH login. CINECA usernames are personal;
  persistent tunnels or proxy routes need CINECA's agreement.

## What the consolidation changed

The upstream repository kept every release as an immutable snapshot folder. In this repository, Git is
the history, so only the current version stays in the tree:

| Upstream path | Here |
| --- | --- |
| `releases/inference/2026-10-19-docs1` | `serve/`, execution files byte-identical |
| `releases/inference/2026-10-18`, `-18-docs1`, `-19-r2` | Removed: same code (only the port line differs), evidence kept in `validation/` |
| `qwen/` | `bringup/`, without `05`/`06` (superseded by `install/03` and the `qwen235b` profile) |
| `releases/glm53/operator-2026-10-13` `02`, `05`, `03` | `install/01`, `02`, `03`, generalised to any profile and the `$BASE` layout |
| `releases/glm53/operator-…`, `candidates/glm53/…`, `releases/glm53-tools/…` | Removed: GLM-only launcher, per-user candidate and helpers, all replaced by `serve/` |
| `tools/register_runtime.py` | `install/register_runtime.py` |
| `GPU_VALIDATION.json` (two files) | `validation/validated_code.json` |
| Docs (8 files) | `README.md`, `docs/how-it-works.md`, this page |

Nothing is lost: the original repository stays unchanged at
[Jules0315/Guardrails-vllm](https://github.com/Jules0315/Guardrails-vllm/tree/298432c789de33d28a4cb500352a0714d8bac2c4)
(this folder was copied from commit `298432c`). For example, the GLM GPU compatibility preflight
(`preflight.py`, which checks the sparse-MLA backend and mHC numerics without loading weights) is at
`candidates/glm53/per-user-2026-10-13/preflight.py` there.

The install scripts were rewritten for the generic layout and have not yet been re-run in this form.
Expect to debug paths on the first fresh install. The team's existing installation on Leonardo does not
need them.
