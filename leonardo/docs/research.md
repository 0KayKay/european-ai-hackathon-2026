# Bringing large models up on Leonardo: research notes

These notes collect the investigation behind the model profiles: what failed on Leonardo's A100s, why, and
how it was solved. The work was done by Junghwa Lee ([@Jules0315](https://github.com/Jules0315)) between 8
and 19 October 2026 and documented in [Jules0315/Guardrails-vllm](https://github.com/Jules0315/Guardrails-vllm)
(commit `298432c`), which has the original scripts and evidence. This page summarises it for anyone adding a
model or a runtime. [history.md](history.md) has the timeline; [how-it-works.md](how-it-works.md) explains the
launcher that came out of it.

## Starting point

Leonardo's GPU nodes have four NVIDIA A100 64 GB each (compute capability 8.0, "SM80"), a host driver from
the 535 series (CUDA 12.2), shared Lustre storage, InfiniBand between nodes, and no internet. Several kernels these models rely on
are only available for newer GPUs (SM90) in stock vLLM. Most of the work was finding which combination of
vLLM build, kernels and settings runs them correctly on SM80.

## Findings per model

### Qwen3-235B-A22B-FP8 (8 Oct, then 19 Oct)

- **First run on stock vLLM 0.31.0** (8 Oct, through the starter kit): two nodes, TP4 within a node and
  PP2 across nodes, no Ray. FP8 checkpoints have no native FP8 path on A100; vLLM falls back to Marlin
  weight-only FP8 kernels with BF16 activations. 2,048-token context, four sequences. Inference worked
  inside the cluster and through an SSH tunnel; authentication rejection and four concurrent requests were
  checked. This configuration is the `qwen235b-stock` profile; it has not run through the current launcher.
- **Slow loading on Lustre:** the default memory-mapped safetensors loading read shards with random access,
  which is slow on Lustre. `--safetensors-load-strategy eager` reads each shard sequentially into host RAM
  first. Node and cache conditions differed between attempts, so no speed-up figure is claimed; eager
  loading is used for every large profile since.
- **On the shared backport runtime** (19 Oct, job 61389137): the same layout plus the Qwen3 reasoning parser,
  the Hermes tool parser and `enable_thinking: false` passed all seven API checks; healthy after 7.0 minutes.
  This is the `qwen235b` profile.
- Checkpoint: revision `39eb2b06…`, about 239 GB in 48 shards.

### GLM-5.3-Flash (13 Oct, then 18 Oct)

- **Stock vLLM could not run it on A100.** GLM uses sparse MLA attention (an MLA variant with an index that
  selects the top-k keys). The stock image offered no sparse-MLA backend usable on SM80; the compatibility
  check below lists which of FlashMLA sparse, FlashAttention MLA sparse and the FlashInfer variants support
  the GPU.
- **Community SM80 backport.** The community build of vLLM 0.13.1 for SM80
  (`lazymio/vllm-backport`, pinned by digest `0fc33cd7…`) adds a Triton sparse-MLA kernel that runs on A100.
  The image names no source commit, so a source-to-binary match was not verified independently.
- **Broken TileLang.** The backport image ships an incomplete TileLang 0.1.12, and GLM's mHC layers use a
  TileLang kernel. The official wheel of the same version, SHA-256 pinned, is installed into a separate Python
  overlay without touching any other package; the install checks that `tilelang` and `tilelang.webgpu` import.
  The mHC TileLang kernel was then checked numerically on an A100 against vLLM's PyTorch reference.
- **Compatibility check without weights.** Before downloading 328 GB, a GPU job checked: CUDA
  initialisation with a BF16 matmul, that vLLM knows the model architecture, which sparse-MLA backend vLLM
  selects, a Triton sparse-MLA result against a PyTorch FP32 reference, the mHC kernel against its PyTorch
  reference, and which of the stock backends support the GPU. This is now `cli.py diagnose compat --profile <profile>`.
- Settings that worked: TP4/PP2, Triton sparse MLA, Marlin FP8 weights, BF16 activations and KV cache,
  text-only input, 2,048-token context, four sequences, eager safetensors loading, GLM tool and reasoning
  parsers. Healthy after 775 s and 566 s in two early runs, and 801 s through the shared launcher (job
  61299807). NCCL used InfiniBand with GPUDirect RDMA.
- A forced tool call answers with `finish_reason: "stop"`, not `"tool_calls"`: clients must look at
  `tool_calls` directly.
- Checkpoint: revision `91b69215…`, 328,337,455,672 bytes in 62 shards.

### DeepSeek-V4.1-Flash-1017 (18 Oct)

- **One tensor-parallel group over both nodes** (TP8, PP1) with expert parallelism and the
  `allgather_reducescatter` all-to-all backend. Before the model starts, the `nccl_ep` hook runs a collective
  correctness test on all 8 GPUs across both nodes.
- **Engram CPU offload** (`--engram-config {"cpu_offload": true}`) keeps the model's Engram tables in host
  memory. The `warm_engram` hook reads those rows once into the page cache on every node before startup.
- FP8 MLA KV cache (`fp8_ds_mla`), lazy safetensors loading, DeepSeek tokenizer, reasoning and tool parsers,
  `--language-model-only`, two sequences.
- The slowest start of the three: healthy after 1,547 s (job 61300901), so its debug-queue test needs more
  than 30 minutes.

### Qwen3-0.6B (7 Oct, then 20 Oct)

The plumbing test of the starter kit on stock vLLM 0.31.0, on one GPU and on two nodes. It became the
`qwen06b` profile and the first model validated with the current launcher (job 61559851).

## Problems that apply to every model

| Problem | Cause | Fix in this repository |
| --- | --- | --- |
| CUDA errors at startup | The host driver (535, CUDA 12.2) is older than the CUDA the images are built for | `VLLM_ENABLE_CUDA_COMPATIBILITY=1` loads the images' forward-compatibility libraries (`serve/node.py`) |
| Kernel caches fail to write | FlashInfer, TileLang, Triton and others default to read-only or tiny locations inside the container | Every cache is redirected to a private writable folder per user, profile and runtime |
| Package installs run out of space | The contained `/tmp` is small | Installs use staging space in the team folder (`install/runtime.sbatch`) |
| "AF_UNIX path too long" | Unix sockets in deep work-folder paths exceed the 108-byte limit | The runtime temporary folder is bound to the short path `/tmp/inference-tmp` |
| Two jobs collide on ports | API and distributed-master ports were derived from the job id with overlapping ranges (job suffixes 8000-8999) | Separate ranges (master 29000-29999, API 31000-40999), checked for all 10,000 suffixes in the tests |

## How the models were validated

Each model passed the same seven checks against its real API: HTTP 401 without a key, the model catalog,
exact arithmetic, an English answer (also read by a person), SSE streaming ending in `[DONE]`, a forced
tool call with the right JSON arguments (inspected, never executed), and four concurrent requests. The
checks ran at startup inside the job and twice more through an SSH tunnel from a laptop, 60 seconds apart.
Each validated run recorded the exact code hashes, the checkpoint revision and the timings; the evidence is in
[validation/](../validation/validated_code.json), with checkpoint paths, keys and raw cluster logs left out.

Not validated: other users' accounts running servers, sustained or workshop-scale load, long context
(every profile is capped at 2,048 tokens except `qwen06b`), multimodal input, running moderation models next
to a main model, and unattended operation.

## Adding a model or runtime: lessons

- Check compatibility on one GPU before downloading weights: `cli.py install <profile> --config-only`
  fetches the runtime and only the model's configuration files, then `cli.py diagnose compat --profile <profile>` runs
  the checks above.
- Prefer stock vLLM; use a community build only when stock vLLM has no kernel for SM80, and pin it by digest.
- Repair a broken package in a separate overlay with a hash-pinned wheel instead of rebuilding the image.
- Try eager safetensors loading for large checkpoints on Lustre (Qwen, GLM); DeepSeek's profile uses lazy
  loading.
- Keep context and sequence counts small for the first run, then raise them once the model works.
- Record the job id, timings and check results in `validation/` before relying on a configuration.

## Upstream work this builds on

[vLLM](https://github.com/vllm-project/vllm), the
[community SM80 backport](https://github.com/wtdcode/vllm-backport), [TileLang](https://github.com/tile-ai/tilelang),
and the model releases
[Qwen3-235B-A22B-FP8](https://huggingface.co/Qwen/Qwen3-235B-A22B-FP8),
[GLM-5.3-Flash](https://huggingface.co/zai-org/GLM-5.3-Flash) and
[DeepSeek-V4.1-Flash-1017](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017). Code, weights and
containers come from their sources under their own licences.
