# DeepSeek sixteen-GPU validation through the unified CLI and router

Real Leonardo run on 20 October 2026, using commit `5fccaf4a21c2c1146b5dff07395b7ad4f1cb6d25` and CLI release
`a4c0ab387e5527fe`. This evidence is separate from the earlier launcher/load test in
[the historical run](../2026-10-20-deepseek41-16gpu/README.md).

## Executed path

1. Initialize an isolated private base with `cli.py init`. Map the already installed,
   unpatched base runtime and pinned DeepSeek checkpoint into that base.
2. `cli.py install deepseek41-16gpu`: job `61574015` built a separate patched runtime,
   reused the verified immutable SIF by hardlink, copied its 7,551-file overlay,
   verified both patch hashes and imported the patched real allocator on CPU.
   No checkpoint download was needed. The base runtime was not patched in place.
3. `cli.py up deepseek41-16gpu --qos boost_qos_dbg --time 00:30:00`:
   job `61574938` allocated four nodes and sixteen GPUs with TP8/PP2 and expert
   parallelism. The production-queue attempt `61574228` was cancelled before any
   allocation; it consumed no GPU time. The profile's default remains two hours
   on production QoS.
4. Startup passed all seven English API checks before publishing `READY`.
   Health was reached after 1096.03 seconds, excluding queue wait.
5. `cli.py router up --hours 1` started CPU router job `61574567`.
   The unmodified `cli.py forward --local-port 18000` connected this laptop to it.
   The cluster link remained disabled throughout.
6. Run `serve/test_endpoint.py` twice from the laptop through the SSH forward and
   router, using the new profile and private router token. Each suite passed all
   seven checks: missing-token rejection, model catalog, arithmetic, English
   response, SSE content and `[DONE]`, forced `sum_numbers` arguments, and four
   concurrent requests. The function was not executed.
7. Stop the exact model allocation and this router with the CLI, verify endpoint
   withdrawal, stop the forward, and verify the recorded jobs have ended.

## Evidence

- [installation.json](installation.json): actual installer result, patch/image and operator source hashes.
- [summary.json](summary.json): job IDs, resource accounting, executed path and patch/READY markers.
- [api-startup.json](api-startup.json): seven checks against the model before endpoint publication.
- [api-client-1.json](api-client-1.json), [api-client-2.json](api-client-2.json): two seven-check suites through the laptop router path.
- [startup.json](startup.json), [ready.json](ready.json): lifecycle timing and published endpoint metadata.
- [release-manifest.json](release-manifest.json), [model-profile.json](model-profile.json): exact executed serve bytes and profile.
- [router-preflight.json](router-preflight.json): CPU router and authentication checks before GPU allocation.
- [cleanup.json](cleanup.json): resource, endpoint, key and tunnel cleanup verification.
- [shutdown-observations.json](shutdown-observations.json): timestamped teardown diagnostics.
- [slurm-accounting.json](slurm-accounting.json): terminal state and exit status of every recorded job and step, including cancellation-related batch failures.

Private checkpoint/run paths, operator login and node names are redacted. Tokens
and API keys are omitted. All prompts are synthetic English test prompts. This
validates one operator account and short functional requests. It does not add a
new load benchmark or establish sustained capacity, long-context behavior,
multi-account access, cluster-link/GKE connectivity or unattended operation.

After the intentional `cli.py down` cancellation, the pinned runtime emitted an
AsyncLLM output-handler traceback and distributed TCPStore/DistNetworkError
warnings. They were absent before the cancellation marker; the first recorded
TCPStore warning followed it by about a second. Resource and endpoint cleanup
passed, but log-clean graceful worker teardown was not established. NCCL also
emitted informational unused-CTS messages during the run; the captured log had
no `NCCL WARN`, `NCCL ERROR` or `StopIteration` markers.
