# B300 validation status

Updated on 28 October 2026. Actual B300 model/API checks and local fixtures are
separate. All tested servers and owned forwards were stopped after evidence was
saved. See the [full CLI report](CLI_ALL_PROFILES_20261028.md).

| Workflow | Actual evidence | Status |
| --- | --- | --- |
| Standalone Ultra, four GPUs | Job 12334; node and Mac API suites, common CLI query and verified cleanup | Passed with the atomic-session client. |
| Ultra + Safety, five GPUs | Job 12320; both API suites, independent keys, cross-key rejection and simultaneous CLI calls | Passed; both servers stopped and keys removed. |
| Retained native GLM, eight GPUs | Job 12342; original seven checks on node and Mac, common query, private-IP route and shutdown | Passed at 2,048 tokens / 4 slots; generated functions were not executed. |
| GLM at 65,536 tokens / 16 slots / 16,384-token chunked prefill | 29 October start: KV cache 16,784,237 tokens, 256.11x concurrency ([GLM context](glm-context.md)) | KV cache size measured; no acceptance-suite result recorded here. |
| `glm53-long`: GLM at 262,144 tokens / 32 slots | None yet; sizing from the 65,536 measurement ([GLM context](glm-context.md)) | Not GPU-validated. Record the KV cache and maximum concurrency lines from the first start. |
| GPT-OSS-Safeguard, one GPU | Job 12360; native policy/JSON protocol, node/Mac normal and unauthorized requests | Passed; strict boolean verdict parser rejects malformed/truncated outputs. |
| Standalone Safety, one GPU | Earlier job 12204; current Safety also checked in paired job 12320 | Earlier standalone smoke preserved; current paired path passed. |
| Same-owner, same-image cache reuse | Safety → initial Ultra; paired cache → current Ultra; GLM → Safeguard | Exercised live. Reuse pins a node and does not skip all tuning. |
| Preparation-only `install` | Implemented; not used as a separate job in this campaign | Not live-validated. |
| Shared-base ACL management / a second user using this full launcher | Implemented ownership/private-folder rules | Not live-validated. |
| Cluster link to agentgateway | Job 12482: link attached to ready GLM via overlapping Slurm step; public GKE ingress, authenticated model catalog, real completion and streaming; unauthenticated catalog rejected (401) | Live GLM connection passed. Fresh `up --link`, other models through GKE and live reconnection remain unvalidated. Local tunnel/relay tests also exist in `tests/test_link.py`. |
| Another physical laptop, Windows, sustained load and natural time-limit expiry | Outside this campaign | Not validated. |

## Executed source

The four current runs match one complete frozen serving manifest. CLI/client
hashes were recorded before their Mac API checks. The initial Ultra job 12300
passed before a local session-file race was corrected; its evidence is retained
separately, and standalone Ultra was then rerun with the corrected client.

The paired node-pinned submission 12317 was cancelled before allocation or model
requests because the cache node lacked five free GPUs. It is not a model failure
or an extra scored case. Its unpinned successor is in the table above.

## Local and historical evidence

Nine lifecycle tests and twelve client tests passed, including concurrent private
session publication, uncertain/duplicate submission handling, foreign cancellation
refusal, expired endpoints and strict native classifier parsing. Python/shell
syntax, serving hashes, pinned settings, GPU totals and Markdown links passed.
Local fixtures are not model outputs.

Earlier native GLM checks (11393), the 280-call Nemotron comparison (11962) and
the distinct-account direct-client check (12174) remain in
[historical results](VERIFIED_RESULTS.md) and the
[direct-client report](MAC_CONNECTIVITY_20261028.md). They are not substituted
for the new launcher results. The [original Safety smoke report](CLI_VALIDATION_20261028.md)
retains its own source-snapshot qualification.

These are functional deployment checks, not a safety benchmark or a full-app
guardrail evaluation. The same small arithmetic and classification prompts are
reused; passing them does not establish general model quality or safety.
