# Nemotron Mac access verification — 28 October 2026

**Completed:** the updated colleague-package commands reached both Nemotron
APIs from the user's Mac using a provided team cluster account distinct from
the operator's account. All **13 checks passed**, with five actual completions
and five distinct returned response IDs.

## Verified workflow

The client now supports `tunnel --kind both`: one SSH connection forwards both
model ports. Strict host-key checking remains enabled. The same fresh AXIS
command is used once for the combined tunnel, while each model retains its own
API key. The verifier used a clean package copy, the CLI's hidden key-entry
prompts and the exact common-client commands documented in the guide. It did
not reuse the operator's SSH control socket for the client tunnel.

| Check | Result |
| --- | --- |
| Hidden CLI key entry and mode-600 key files | 2/2 passed |
| Both model ports through one direct SSH connection | 2/2 passed |
| Authenticated model catalogs | 2/2 passed |
| Ultra English chat | 1/1 passed |
| Safety 4B benign and disallowed classifications | 2/2 passed |
| Ultra benign and disallowed classifications | 2/2 passed |
| Deliberately invalid API-key rejection | 2/2 passed, HTTP 401 |

Ultra answered: “A GPU performs massive parallel calculations to render graphics
and accelerate complex data processing.” Both models classified the harmless
geography question as `Prompt harm: unharmful` and the unauthorized password
request as `Prompt harm: harmful`, under the package's demonstration policy.

The five actual completions returned the expected aliases (three Ultra, two
safety), distinct nonempty response IDs, normal stop reasons and actual token
usage. Their final-response evidence was saved using `query.py --output`.
Catalog and invalid-key requests are not counted as model completions. Hidden
reasoning and API keys are excluded from these saved response records.

## What was wrong and what changed

The earlier monitor recorded a successful Mac check at
`2026-10-27T17:31:11.181041+00:00`. Cleanup/transport retries later re-entered its
verification branch after the model session had stopped. At
`2026-10-27T19:16:49.222558+00:00`, it attempted to read an endpoint already
removed during shutdown and overwrote the prior record with `FileNotFoundError`.
The previous blanket description of Mac access as failed is corrected here.
The original per-response success artifact was overwritten; that historical
monitor flag is not presented as recovered raw response evidence.

Session fetching now rejects non-ready or mismatched jobs, checks readiness
again after reading keys, and discards expired local files. Connectivity attempts
are appended separately; a later failure cannot erase a successful result.
One-model partial success does not establish that both APIs work. The monitor
also avoids repeating a completed check after expiry and waits for its owned
SSH socket to disappear after an exit reply.

In today's additional team-account test, the earlier separate-tunnel attempts
failed before any model cases ran. A combined-tunnel attempt also initially
failed before listening; a subsequent run with the same issued command passed.
The exact transient AXIS/SSH handshake cause was not established. No host-key
checking, authentication or network protection was disabled. The final guide
uses the successfully verified one-connection workflow; early failed attempts
are retained separately rather than being called successful.

Eighteen deployment/state contracts and ten packaged-client contracts passed
locally. Those tests use fixtures and are separate from the actual model calls.

## Scope

Two cluster identities were exercised on the same Mac. The operator's earlier
separate-tunnel baseline passed 13 checks. The final updated client was then
verified with the distinct provided team account: SSH `whoami` matched that
account and differed from the operator, and refreshed AXIS credentials for that
account were used in the final combined-tunnel run. Credentials were obtained
from the existing private configuration, not added to the publication package.

The primary evidence reported above is the final **team-account run**, not a
sum across different client versions. Another physical computer, additional
teammates and Windows/Linux were not tested. Each colleague still needs their
own valid AXIS authorization, fresh endpoint and private model key. This check
is not a full Chat API, authorization, throughput or general safety benchmark.
GLM was not retested, and the historical 280-call moderation suite was not replayed.

## Current session and lifecycle

Owned job `12174` uses five B300 GPUs: four for Ultra, one for safety. Allocation
started **28 October 2026, 08:37:31 CET** and is limited by Slurm to
**12:37:31 CET (Europe/Berlin)** that day. Servers were retained within this
four-hour allocation for user access. This is a dated session, not a permanent
endpoint; check the current allocation before connecting.

The verifier stopped its own SSH process group and confirmed both loopback
ports were closed afterward. GPU servers remain running intentionally. Open
your own fresh guide tunnel before querying from the laptop. Test-forward
cleanup does not imply GPU-server cleanup.

## Evidence and publication

The operator baseline and failed attempts are preserved under
`Cluster_B300/nemotron/.runtime/mac-access-20261028/`. Final team-account evidence
is in its `teammate-account2/` directory: verification JSON, identity verification
receipt and per-request final-response JSON. None of the live endpoint, session
keys, SSH commands or private runtime files is included in the GitHub candidate.
The public evidence summary records the tested-client and verification hashes.

The English guide and local ZIP were updated. No Git staging, commit, push or
pull request was performed.
