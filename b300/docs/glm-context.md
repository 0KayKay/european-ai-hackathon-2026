# GLM-5.3-Flash context sizing on B300

Prepared 28 October 2026. **Status: configured, not yet GPU-validated.** Every
recorded GLM run (11393, 12342, 12482) used the earlier 2,048-token / 4-slot
settings. The values below are a sizing estimate until a fresh `glm53` start
reports its KV cache size.

## Configured engine settings

| Setting | Previous (validated) | Current (not yet GPU-validated) |
| --- | --- | --- |
| GPUs / parallelism | 8 B300, TP8/PP1 | 8 B300, TP8/PP1 (unchanged) |
| `--max-model-len` | 2,048 | 65,536 |
| `--max-num-seqs` | 4 | 16 |
| `--max-num-batched-tokens` | 2,048 | 16,384 (chunked prefill) |
| `--gpu-memory-utilization` | 0.90 | 0.90 (unchanged) |
| `--kv-cache-dtype` | `auto` | `auto` (unchanged) |

All other engine flags (`--enforce-eager`, `--disable-custom-all-reduce`, the
parsers, eager safetensors loading) are unchanged. The values live in
[`serve/models.json`](../serve/models.json) (`glm.max_model_len` and
`glm.engine_args`) and [`profiles.json`](../profiles.json);
`tools/check_repository.py` asserts that they agree. A 65,536-token prompt is
prefilled in four 16,384-token chunks, so long prompts do not stall short
decode requests for a whole prefill.

## Memory estimate per GPU

| Item | Per GPU |
| --- | ---: |
| B300 HBM | 288 GB |
| Usable at `--gpu-memory-utilization 0.90` | ~259 GB |
| BF16 weights, 328.3 GB / 8 ranks | ~41 GB |
| Activations, NCCL buffers, sampler, fragmentation | ~10–20 GB |
| **Left for KV cache** | **~200 GB** |

GLM-5.3-Flash uses MLA-style (sparse) attention. Its compressed latent KV cache
is not split across tensor-parallel ranks: every TP rank holds the full latent
cache for its sequences. TP8 therefore does not multiply KV capacity; the
per-GPU budget is the deployment's capacity.

At roughly 1.3 KB per layer per token (latent plus rotary key in BF16, plus the
sparse-attention indexer key) and an estimated 80–120 layers, one token costs
about 100–160 KB. That gives on the order of **1.3–1.9 M cached tokens**.
The worst case of 16 sequences × 65,536 tokens = 1,048,576 tokens fits, with
roughly 20–80% headroom.

These architecture figures are estimates: `config.json` of the pinned revision
could not be read from the environment where this change was prepared. Public
sources report `max_position_embeddings` = 1,048,576, so 65,536 is well inside
the model's positional range. If the real cache is smaller than the worst case,
vLLM still starts (each single sequence fits) and preempts or queues requests
under load instead of failing.

## What to check on the next start

After `./b300/cli.py up glm53`, the vLLM engine output goes to
`glm.server.log` in your private run folder, `BASE/users/LOGIN/runs/RUN_ID/` on
the login node (`./b300/cli.py logs glm53` shows only the batch log). Once the
engine has loaded, send these two startup lines and the job ID back to the team:

```bash
# On the login node; the run folder is mode 700 and readable only by you.
grep -E 'GPU KV cache size|Maximum concurrency' "$BASE/users/$USER/runs/RUN_ID/glm.server.log"
```

```text
GPU KV cache size: N tokens
Maximum concurrency for 65536 tokens per request: Nx
```

If the engine fails before these lines (for example an out-of-memory error
during KV cache allocation), send the last lines of the same log instead.

- `N tokens` ≥ 1,048,576 (concurrency ≥ 16x) confirms the estimate: all 16
  slots can hold a full 65,536-token context at once.
- Concurrency below 16x means the estimate was optimistic. The configuration
  still works, but long requests queue; lower `--max-num-seqs` to the reported
  value if predictable latency matters more than throughput.
- Concurrency of 32x or more (≥ 2.1 M tokens) leaves room for the next step:
  `--max-model-len 131072` with 16 slots. Between 16x and 32x, 131,072 is only
  safe with `--max-num-seqs 8`.

Changing these values means editing `serve/models.json` and `profiles.json`,
running `python3 b300/tools/update_manifest.py`, then
`python3 b300/tools/check_repository.py`. Record the observed lines and the job
ID in [validation status](validation.md) once a run passes.

## Option: two TP4 servers

The eight GPUs can instead host two four-GPU servers, so different models play
attacker and defender (for example GLM on four GPUs and Nemotron Ultra, already
a four-GPU TP4 profile, on the other four). At TP4 each GPU holds ~82 GB of GLM
weights, leaving ~160 GB, roughly 1.0–1.5 M tokens of KV cache, so 16 × 65,536
is borderline; use `--max-num-seqs 8` there until the startup lines confirm more.

This is a documented option only; no profile exists for it. GLM TP4/PP1 has not
been run on B300 (only TP4/PP2 was tried, and it failed), and one profile
currently runs in a single container image, while GLM and Ultra use different
images. The practical route would be a new four-GPU GLM profile (a `glm` model
entry with `tp` 4) started as its own job next to the existing `ultra` profile,
rather than one combined profile; both jobs together must stay within the
team's eight-GPU limit.
