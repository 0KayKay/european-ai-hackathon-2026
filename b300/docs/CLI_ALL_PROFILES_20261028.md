# B300 model profiles: actual CLI lifecycle verification

Date: **28 October 2026**, Europe/Berlin (CET, UTC+1).

**Status: passed.** Execution was verified locally before branch publication.
The candidate is prepared for team review on `codex/b300-cli-team-guide`.

The new CLI was exercised from the Mac using a freshly issued AXIS command
and one provided cluster account. Jobs ran sequentially, with bounded Slurm
limits and owned-resource cleanup after each test. No unrelated allocation
or shared SSH master was changed.

## Actual results

| Profile | GPUs | Job | Result | Node / Mac acceptance checks | Recorded completion IDs: node / Mac |
| --- | ---: | --- | --- | --- | --- |
| `nemotron` | 5 | 12320 | Passed; cleanup verified | 11 / 11 | 10 / 14 |
| `ultra` | 4 | 12334 | Passed; cleanup verified | 7 / 7 | 8 / 9 |
| `glm53` | 8 | 12342 | Passed; cleanup verified | 7 / 7 | 7 / 8 |
| `safeguard` | 1 | 12360 | Passed; cleanup verified | 4 / 4 | 2 / 3 |

The earlier standalone Safety 4B test, job 12204, is documented separately.
Safety is also exercised through the current release in the five-GPU pair.

A completion ID identifies a real model response. Function/tool-call IDs
are excluded from these reviewed counts. The GLM suite records successful
SSE termination and content but omits the stream response IDs; its two
node/Mac streaming requests are additional to the counted IDs.

Ultra SSE summaries retain response IDs but omit per-chunk model names;
their model is attributed to the selected authenticated endpoint. Full
completion objects retain and validate returned-model identifiers.

## Checks and provenance

- Actual CLI submission, duplicate prevention, live Slurm readiness, private
  endpoint/key fetch, Mac forwarding and common-client model calls.
- Chat-model checks: authenticated catalog, rejected missing/invalid key,
  arithmetic, English response, SSE, function-call schema and four concurrent
  requests. Generated function calls were not executed.
- Classifier checks: a harmless geography question and an unauthorized
  password request. Safeguard uses a separate system policy and strict
  JSON boolean verdict; Nemotron uses its native harm label.
- The paired run additionally checks different keys/ports, rejection of the
  other model's key and simultaneous real requests to both APIs.
- Cleanup checks: owned job absent from live queue, stopped server state,
  removed remote endpoint/keys, closed local forward and rejected expired
  session fetch. The campaign-private SSH master closes after the sequence.

Frozen serving manifest SHA-256:
`0b4a58e082acdd3accc7e314c0dd1957c41b857bb004c212694e170eeec53edd`.

Every passed run matched this complete serving manifest. The CLI and
client hashes were recorded before Mac API tests and checked again during
report generation. Raw evidence, credentials and private endpoints remain
outside the publication package.

Recorded distinct completion IDs across passed stages: **61**.

## GLM cleanup confirmation

GLM passed its node and Mac API checks. Its first `down` correctly
returned `cleanup_pending` after the initial 30-second wait. The original
controller treated that earlier receipt as a failure even though the
subsequent saved live queue observation was empty and the server/keys
were stopped/removed. The original pending receipt and controller result
are preserved. A fresh owned-job check confirmed cleanup, and a separate
review records that confirmation. No GLM model cases were replayed.

## Resource scheduling adjustment

The first paired submission, job 12317, requested the prior cache node.
That node had only four free GPUs while the pair required five. It was
cancelled while pending, with **zero allocated GPUs and zero model requests**.
The pair was resubmitted without a node constraint as job 12320. The
completed Ultra cases were preserved; the node scheduling retry did not
replay them.

## Concurrent-client correction and regression

The first standalone Ultra run, job 12300, passed before a client-side
concurrency review identified shared temporary endpoint names and key-file
truncation during simultaneous session refresh. The CLI now publishes
endpoint and key files with private unique temporary files and atomic
replacement. A concurrent reader/writer test was added. Local checks:
**nine lifecycle tests and twelve client tests**, all passing.

The first Ultra evidence is preserved separately. The table uses the
current-client paired run and a new standalone Ultra regression, followed
by GLM and Safeguard. The model-serving source stayed frozen throughout;
only local session-file publication changed before these Mac tests.

## Limits

These are small functional lifecycle checks, not a safety benchmark,
a throughput comparison, a full-app guardrail evaluation or proof of
general model quality. Cold image/weight preparation and kernel tuning
affect startup and are not interpreted as normal response latency.

This campaign uses one cluster account and one physical Mac. Shared-base
ACLs, another user running this complete CLI sequence, Windows, Kubernetes
routing and preparation-only installation remain outside its live coverage.
Earlier direct-client and large-model comparisons have separate evidence.

The campaign-private SSH master was verified closed after the terminal outcome.
