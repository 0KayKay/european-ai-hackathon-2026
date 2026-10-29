# GLM-5.3-Flash context sizing on B300

Updated 29 October 2026. Two GLM profiles exist; both use the same pinned
checkpoint, 8 B300 GPUs with TP8/PP1, and the API name `glm-5.3-flash-leonardo`
(gateway alias `glm-5.3-flash-b300`). Run only one of them at a time.

| Setting | `glm53` (model `glm`) | `glm53-long` (model `glm-long`) |
| --- | --- | --- |
| `--max-model-len` | 65,536 | 262,144 |
| `--max-num-seqs` | 16 | 32 |
| `--max-num-batched-tokens` | 16,384 (chunked prefill) | 16,384 (chunked prefill) |
| `--gpu-memory-utilization` / `--kv-cache-dtype` | 0.90 / `auto` | 0.90 / `auto` |
| Status | KV cache measured at startup (below) | Not yet GPU-validated |

All other engine flags (`--enforce-eager`, `--disable-custom-all-reduce`, the
parsers, eager safetensors loading) are the same. The values live in
[`serve/models.json`](../serve/models.json) and [`profiles.json`](../profiles.json);
`tools/check_repository.py` asserts that they agree. The earlier recorded GLM runs
(11393, 12342, 12482) used 2,048 tokens / 4 slots.

`glm-long` has `"kind": "glm"`: the server runs it exactly like `glm`, with the same
seven-check GLM suite, key file (`glm.api-key`), log (`glm.server.log`), endpoint
entry, `forward`/`query --model glm` and laptop port 18083. Clients and the gateway
need no change.

## Measured on B300 (`glm53`, 65,536 tokens)

From the vLLM log of a `glm53` start on 29 October 2026, per GPU:

| Item | Per GPU |
| --- | ---: |
| Total memory | 267.69 GiB |
| Weights + non-torch memory | 39.28 GiB |
| Peak activation (16,384 batched tokens) | 2.85 GiB |
| **KV cache in use** | **198.79 GiB** |

```text
GPU KV cache size: 16,784,237 tokens
Maximum concurrency for 65,536 tokens per request: 256.11x
```

That is ≈ 12.7 KB per token per GPU. The model config has 45 layers,
`kv_lora_rank` 512, `qk_rope_head_dim` 0, `index_topk` 2048 and
`max_position_embeddings` 1,048,576. A BF16 latent cache replicated on every TP
rank would cost 45 × 512 × 2 B ≈ 46 KB plus ≈ 6 KB of indexer keys, ≈ 52 KB per
token; the measured cost is about a quarter of that. The earlier estimate in this
file (80–120 layers, 1.3–1.9 M tokens) was about ten times too pessimistic.

## `glm53-long`: 262,144 tokens, 32 slots

The KV cache size depends on memory, not on `--max-model-len`, and the batched-token
limit and so the activation peak are unchanged, so `glm53-long` should again report
about 16.8 M tokens. The worst case of 32 × 262,144 = 8,388,608 tokens fits with a 2×
margin (expected concurrency ≈ 64x). 262,144 tokens are a quarter of the model's
positional range. A 262,144-token prompt is prefilled in sixteen 16,384-token chunks.

Start it (after `down glm53` if that is running):

```bash
./b300/cli.py up glm53-long --link     # or without --link for laptop-only use
./b300/cli.py status                    # ready, then cluster_link: connected
```

## What to send back

The vLLM engine output goes to `glm.server.log` in your private run folder,
`BASE/users/LOGIN/runs/RUN_ID/` on the login node (`./b300/cli.py logs PROFILE`
shows only the batch log). Once the engine has loaded, send these lines and the job
ID back to the team:

```bash
# On the login node; the run folder is mode 700 and readable only by you.
grep -E 'Free memory on device|GPU KV cache size|Maximum concurrency for' \
  "$BASE/users/$USER/runs/RUN_ID/glm.server.log"
```

- For `glm53-long`, `Maximum concurrency for 262,144 tokens per request` ≥ 32x
  confirms that all 32 slots can hold a full context at once.
- Below 32x the configuration still works, but long requests queue; lower
  `--max-num-seqs` to the reported value if predictable latency matters more.
- If the engine fails before these lines (for example out of memory while
  allocating the KV cache), send the last lines of the same log instead.

Changing these values means editing `serve/models.json` and `profiles.json`,
running `python3 b300/tools/update_manifest.py`, then
`python3 b300/tools/check_repository.py`. Record the observed lines and the job
ID in [validation status](validation.md) once a run passes.

## Option: two TP4 servers

The eight GPUs can instead host two four-GPU servers, so different models play
attacker and defender (for example GLM on four GPUs and Nemotron Ultra, already
a four-GPU TP4 profile, on the other four). At TP4 each GPU holds ~82 GB of GLM
weights, leaving ~160 GB of KV cache per GPU: roughly 7–13 M tokens from the
measured TP8 bytes, depending on whether the cache is split across TP ranks.

This is a documented option only; no profile exists for it. GLM TP4/PP1 has not
been run on B300 (only TP4/PP2 was tried, and it failed), and one profile
currently runs in a single container image, while GLM and Ultra use different
images. The practical route would be a new four-GPU GLM profile (a `glm` kind
model entry with `tp` 4) started as its own job next to the existing `ultra`
profile, rather than one combined profile; both jobs together must stay within
the team's eight-GPU limit.
