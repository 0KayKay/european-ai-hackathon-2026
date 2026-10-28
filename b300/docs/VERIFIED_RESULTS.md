# Verified B300 results used by this handoff

Prepared: 27 October 2026; updated 28 October. The first two sections summarize
the historical model experiments. A fresh 28 October Mac connection test is
documented separately below and did not replay the comparison corpus.

## GLM-5.3-Flash: functional API validation

Job `11393`, one eight-B300 node, TP8/PP1: **21/21 checks passed** across three
repetitions of the seven-check suite. Checks covered authentication rejection,
model catalog, arithmetic, English response, completed SSE, forced function-call
JSON and four concurrent requests. The generated function was not executed.

Server readiness took 835.186 seconds, including warmup. This is a functional
deployment result, not a security score, sustained load result or hardware-only
speed comparison with Leonardo. The owned server stopped and the temporary key
was removed after the run.

## Nemotron: paired safety classification

Job `11962` used the five-GPU deployment: four for Ultra NVFP4, one for the BF16
4B specialist. The frozen corpus had 70 attacks and 70 benign messages. Each
model received every case once: **280 scored requests**, plus four warmups.

| Measure | Safety 4B | Ultra 550B NVFP4 |
| --- | ---: | ---: |
| Attack blocks | 60/70 (85.7%) | 70/70 (100%) |
| Benign false blocks | 1/70 (1.4%) | 1/70 (1.4%) |
| Request / verdict errors | 0/140 | 0/140 |
| Median request time | 0.041 s | 0.291 s |

Ultra caught ten attacks missed by the specialist in this suite, mainly in the
application-policy scope. On the smaller general-content subset, both blocked
12/12 attacks and falsely blocked 0/12 benign messages. Equal total false-block
counts do not mean they rejected the same benign question.

Actual JSON verification found 280 unique case/model pairs and 284 distinct,
nonempty response IDs including warmups, with expected returned model names.
Frozen case text, labels and paired prompts were verified locally. Comparison
parameters were temperature zero, 128 output tokens and sequential requests
with alternating model order; Ultra used its native reasoning-off option.

These are input-classification measurements on a known, Codex-labelled small
corpus. They do not test downstream tool authorization, actual disclosure or
the full Chat API. The roughly sevenfold median latency difference also changes
model size, precision and GPU allocation; it is not a throughput benchmark or
a controlled hardware-only comparison. A 100% score here is not a general safety
guarantee.

The final archived laptop-check file recorded `passed=false` / `FileNotFoundError`,
but a 28 October review found an earlier successful Mac check in the monitor log.
A retry after session shutdown overwrote that success record. The historical
individual response artifact was not recovered. The [28 October fresh Mac CLI
verification](MAC_CONNECTIVITY_20261028.md) passed all 13 final checks with a distinct team account and retained five
actual completion records. The older failure is not evidence that the live
SSH route itself failed. Job `11962` ended at the retained-session deadline; actual empty
queue, expired endpoint/key removal and owned SSH resource cleanup were verified.

## Local evidence locations

These paths identify the original project artifacts, **not files bundled in
this GitHub candidate**:

```text
Cluster_B300/glm53-comparison/remote-evidence/runs/11393/state.json
Cluster_B300/glm53-comparison/remote-evidence/runs/11393/configuration.json
Cluster_B300/glm53-comparison/remote-evidence/runs/11393/api-local-{1,2,3}.json
Cluster_B300/nemotron/results/11962/actual-result.json
Cluster_B300/nemotron/results/11962/local-verification.json
Cluster_B300/nemotron/results/11962/laptop-connectivity.json
Cluster_B300/nemotron/results/11962/cleanup-verification.json
```

The sanitized [evidence summary](../evidence-summary.json) records their file
hashes and selected measured values without raw prompts, network addresses,
credentials or the evaluation corpus.

