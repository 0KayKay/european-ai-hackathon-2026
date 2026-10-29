# GLM on sixteen GPUs, long context

`glm53-16gpu` serves GLM-5.3-Flash on four nodes with a 131,072-token context.
It is separate from the eight-GPU `glm53` profile, which stays unchanged, and
has its own API model name and direct-client port. **Status: configured, not yet
GPU-validated.** The context fit below is an estimate until a first start reports
its KV cache size.

| Setting | `glm53` | `glm53-16gpu` |
| --- | --- | --- |
| Nodes / A100 64 GB GPUs | 2 / 8 | 4 / 16 |
| Tensor / pipeline parallelism | 4 / 2 | 4 / 4 |
| `--max-model-len` | 2,048 | 131,072 |
| `--max-num-seqs` | 4 | 8 |
| `--max-num-batched-tokens` | 2,048 | 8,192 (chunked prefill) |
| Runtime | `vllm-backport-sm80-0.13.1` | `vllm-backport-sm80-0.13.1-ppkv1` |
| API model name | `glm-5.3-flash-leonardo` | `glm-5.3-flash-16gpu-leonardo` |
| Direct-client port | 18083 | 18089 |

Everything else is copied from `glm53`: `--gpu-memory-utilization 0.90`,
`TRITON_MLA_SPARSE` attention, `--enforce-eager`, eager safetensors loading, the GLM
reasoning and tool parsers, the chat template settings (`reasoning_effort: low`,
`clear_thinking: true`), 32 CPUs and 480 GB host memory **per node**, two hours,
and an 80-minute startup timeout. Both profiles use the same `glm53` checkpoint;
the new profile also pins its revision (`91b69215…`) and its 62 shards.

The runtime is the pipeline-parallel KV cache patched runtime from the
[sixteen-GPU DeepSeek profile](deepseek16.md#required-pipeline-parallel-kv-cache-patch):
the same pinned image and TileLang wheel, plus the allocation guard that keeps
pipeline stages from allocating KV tensors for layers they do not hold.

## Memory estimate per GPU

| Item | Per GPU |
| --- | ---: |
| A100 64 GB, usable as reported by vLLM | 63.4 GiB |
| At `--gpu-memory-utilization 0.90` | ~57 GiB |
| BF16 weights, 305.8 GiB / 16 GPUs | ~19 GiB |
| Non-torch memory | ~1 GiB |
| Peak activation, 8,192 batched tokens | ~2 GiB |
| **Left for KV cache** | **~35 GiB** |

The model config has 45 layers, `kv_lora_rank` 512, `qk_rope_head_dim` 0 and a
sparse-attention indexer (`index_topk` 2048). In the worst case, with the latent
cache in BF16 and replicated on every TP rank, a token costs 45 × 512 × 2 B ≈ 46 KB
plus ≈ 6 KB of indexer keys, ≈ 52 KB for the whole model. PP4 puts about a quarter
of the layers on each GPU (at most 12 of 45), so a token costs about 13–14 KB per
GPU. 35 GiB then holds about **2.7–2.9 M tokens**; the worst case of
8 × 131,072 = 1,048,576 tokens fits with about 2.6–2.8× margin.

The B300 measurement for the same checkpoint (TP8, 29 October 2026, see
[GLM context on B300](../../b300/docs/glm-context.md)) used only ≈ 12.7 KB per token
per GPU for all 45 layers, about a quarter of the worst case. If that carries
over, each Leonardo GPU needs ≈ 3.2 KB per token and has about four times more
room than estimated. 131,072 tokens are well inside `max_position_embeddings`
(1,048,576).

## Start it

Use the same `LEONARDO_USER`, `LEONARDO_HOST` and `LEONARDO_BASE` as the team CLI.
The `glm53` checkpoint is reused, so there is no new weights download. If
`./leonardo/cli.py status` does not list the patched runtime (for example because
`deepseek41-16gpu` was never installed), install it first:

```bash
./leonardo/cli.py install glm53-16gpu
./leonardo/cli.py logs <install-job-id> --follow
```

Then start the server; coordinate the four-node allocation with the team:

```bash
./leonardo/cli.py up glm53-16gpu
./leonardo/cli.py logs glm53-16gpu --follow
./leonardo/cli.py status
```

The server must pass the same seven `chat_tools` API checks before `READY`. Call
it through the router as in the [main guide](../README.md#call-the-models), with
model `glm-5.3-flash-16gpu-leonardo`; the router port stays 18000. The generated
optional agentgateway route includes the new name. Stop it with
`./leonardo/cli.py down glm53-16gpu`.

## What to send back

Once the engine has loaded, send the job ID and these lines (one per pipeline
stage is fine):

```bash
./leonardo/cli.py logs glm53-16gpu --lines 2000 | \
  grep -E 'Free memory on device|GPU KV cache size|Maximum concurrency for'
```

```text
Free memory on device (…/63.42 GiB) on startup. … Actual usage is … GiB for consumed memory (weights + non-torch), … GiB for peak activation …
GPU KV cache size: N tokens
Maximum concurrency for 131,072 tokens per request: Nx
```

- Concurrency ≥ 8x confirms the estimate: all eight slots can hold a full context.
- Below 8x the profile still works, but long requests queue; lower
  `--max-num-seqs` to the reported value.
- If the engine fails before these lines (for example out of memory while
  allocating the KV cache, or a `StopIteration` during pipeline startup), send
  the last lines of the same log instead.

Record a passing run in `validation/validated_code.json` before calling this
profile validated.
