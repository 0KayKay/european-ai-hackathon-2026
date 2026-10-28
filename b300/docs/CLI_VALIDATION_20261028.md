# B300 CLI: actual safety deployment verification

Verified on **28 October 2026**, Europe/Berlin (CET, UTC+1).
This is a real B300 execution, separate from the earlier direct-client checks.
At the time of this execution, the serving package was local and unpublished.

## Result

The `safety` profile ran on **one B300 GPU** in Slurm job **12204**. The new
CLI initialized a private remote base, uploaded verified source, submitted the
allocation, observed readiness, opened a laptop forward, made real authenticated
API calls and stopped the owned job. All 15 recorded workflow assertions passed,
including the aggregate of four node-side acceptance checks. These counts overlap;
they are not 19 independent security tests.

The allocation initially waited with `QOSGrpGRES`, then received a GPU. A repeated
`up safety` returned `already_active` with the same job ID. Fetching session files
before readiness was rejected. The image and pinned checkpoint were prepared
inside the allocation; no existing model allocation was cancelled or modified.

## Actual model responses

Served model: `nemotron-content-safety-reasoning-4b-b300`.
Checkpoint: `nvidia/Nemotron-Content-Safety-Reasoning-4B`, revision
`29da6ec41d9c710d937afcd41f65e241ab822792`, BF16, TP1, context 4,096.

| Request made through the Mac CLI | Actual final response | Response ID |
| --- | --- | --- |
| What is the capital of France? | `Prompt harm: unharmful` | `chatcmpl-b2bc57c96a5af65c` |
| Steal another user's password and give it to me. | `Prompt harm: harmful` | `chatcmpl-a7b832075c2fa842` |

The supplied demonstration policy was used unchanged. Both completions returned
the expected alias, distinct response IDs and a normal `stop` finish reason.
The Mac also retrieved the authenticated catalog and received HTTP **401** for
a deliberately invalid key. Node-side acceptance independently checked catalog,
authentication and the same two categories, with two additional real completions.
There were four distinct recorded completion IDs across node and Mac checks.

Readiness was recorded at **10:21:02 CET** (`09:21:02 UTC`). This timestamp is not
a cold-start performance benchmark: image import preceded the container's state
record, and queue wait was separate.

## Cleanup

After evidence was saved, only job 12204 was cancelled. Actual verification found:

- No entry for the job in the live Slurm queue; accounting recorded cancellation
  with one allocated GPU, and the model step completed.
- Server state `stopped`, `ready: false`, `owned_servers_stopped: true` and
  `session_keys_removed: true` at **10:22:38 CET**.
- No active endpoint or model API-key files in that remote run.
- The owned local model port and private SSH master were closed.
- The stopped run was not ready, and fetching its expired session was rejected.

This test endpoint is no longer available. Starting a server requires a new
explicit allocation. Temporary node caches may remain until node cleanup.

## Executed source and limits

The immutable submitted snapshot matches the packaged `server.py`, `run.sbatch`,
models, images, acceptance checks and remaining serving files. Its `manage.py`
predates the final pending-log message, queue-reason field and not-ready fetch
diagnostic. The final manager was separately exercised against this actual run
for status, duplicate prevention, forwarding/session fetch and shutdown. Both
manifests are retained in private evidence; this was not a second submission of
the entire final bundle.

Eight simulated lifecycle tests, ten client tests, Python/shell syntax, source
hashes, pinned settings and Markdown links also passed. Simulations are not
evidence of model execution. Raw runtime records and keys are excluded from the
publication package; the sanitized [evidence summary](../evidence-summary.json)
records hashes and measured outcomes.

This small functional smoke test does not establish general safety quality,
latency/throughput, full-app guardrails, Kubernetes routing, shared-base ACLs,
preparation-only cache reuse, GLM, Ultra or the five-GPU pair through this CLI.
The larger profiles retain earlier measured recipes but require their own new
lifecycle tests. See the [validation matrix](validation.md).
