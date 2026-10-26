# Qwen 235B, GLM-5.3 and DeepSeek eight-GPU CLI/router validation

Completed Leonardo validation on 21 October 2026 using commit `96f998e410a4c129c7be2c00a20d0374a740e968` and
unmodified CLI release `a4c0ab387e5527fe`. Each model ran on two nodes / eight GPUs
with its unchanged profile. Existing verified runtime/checkpoints were mapped
into a separate private base; `cli.py install` found them installed. The cluster
link stayed disabled. Resource accounting and readiness timestamps are preserved.

| Profile | GPU job | Health after (s) | Startup | CPU client/router | Laptop/CLI/router |
| --- | --- | ---: | ---: | ---: | ---: |
| `qwen235b` | `61591887` | 805.77 | 7/7 | 14/14 | 7/7 |
| `glm53` | `61591889` | 545.54 | 7/7 | 14/14 | 7/7 |
| `deepseek41` | `61591892` | 1471.21 | 7/7 | 14/14 | 7/7 |

Each seven-check suite covers authentication, catalog, arithmetic, English
response, SSE content and DONE, forced function-call JSON, and four concurrent
requests. The function is not executed. One suite gates READY. Two additional
suites run in an independent CPU Slurm client through the actual CPU router:
21 checks per model / 63 checks. The laptop also passed 21 checks through
unmodified `cli.py forward`. The shared router was job `61591661`; the remote
client was CPU job `61591819`. No model inference ran on the CPU client.

The [earlier attempts](earlier-attempts.json) disclose incomplete runs. Two
Qwen and two GLM debug allocations, plus one DeepSeek debug allocation, passed
startup but expired before successful router-client checks: initial SSH forward
disconnection/delayed attachment, then repeated laptop sleep and lost SSH forwarding. The first DeepSeek production
attempt was cancelled while still pending (Priority). These attempts do not
count as full passes. The completed reruns reuse their private compile caches.
The independent CPU client ensures API checks and resource cleanup continue
without a live laptop. Slurm's QoS limited active debug model jobs to two.

Per-model directories contain API suites, lifecycle records, exact executed
profile and serve manifest, resource accounting, log diagnostics and cleanup
verification. All completed model allocations/steps ended and each model
was removed from the router. [Shared cleanup](shared-cleanup.json) records
router, local tunnel/listener and transient-key removal. Private paths,
operator identifiers and node names are redacted; credentials are omitted.

These are short functional checks with one operator and English prompts.
Startup timings are observed runs, not cold-start benchmarks. Cancellation
can produce the pinned runtime's known teardown diagnostics; per-run summaries
separate observations before/after cancellation. This does not validate
sustained capacity, long context, other accounts, graceful request draining,
persistent IPC leakage, GKE or the cluster link.
