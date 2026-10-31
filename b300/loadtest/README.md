# Enterprise load test for the B300 node

This tool answers one question for the [`b300/node`](../node/README.md) deployment: if this one
8-GPU node served a whole company, how big could the company be before people notice?

It simulates employees of a 100- to 10,000-person organisation (or larger) using
LibreChat, document chat, file uploads, RAG ingest pipelines, agents with
sub-agents, Langflow-style automations and meeting transcription. It plays their
busiest hour against the gateway, and grows the organisation stage by stage until a
latency target breaks. The result reads like *"mixed profile: 14,000 employees;
first failure at 21,000, GLM queued up first"*.

It uses only the Python 3.9+ standard library, so it runs on the login node,
the compute node or a laptop.

> **Status: tested only against the bundled fake server** (`fake_server.py`), not against the
> real node yet. The first real run will show whether the gateway accepts every request
> shape (see [Caveats](#caveats)).

## Quick start

Start the node (`sbatch b300/node/node.sbatch`) and wait until every model is `READY`
in its log. Then, on the login node, inside `tmux` or `screen`:

```bash
b300/loadtest/loadtest.sh JOBID ramp --profile mixed     # up to about an hour
b300/loadtest/loadtest.sh JOBID suite                    # chat, knowledge, agentic, mixed: about 2-3 hours
b300/loadtest/loadtest.sh JOBID sweep --sweep-role main --input 8000 --output 500
```

`loadtest.sh` attaches the test to the running serving job as an extra
`srun --overlap` step on the same node. So:
- **no second job is needed**, and no GPUs: the team's 8-GPU limit would keep a second GPU job queued anyway;
- it can read every vLLM server's own `/metrics` (queue lengths, KV-cache use, prefix-cache hits),
  so the report says *which* model gave out first;
- it uses 8 of the job's CPUs (`LOADTEST_CPUS`).

Results go to `$CACHE_DIR/loadtests/TIME-COMMAND/`: `summary.md` (start there), `summary.json`,
the raw per-call `requests-*.jsonl`, `metrics.jsonl`, `console.log`, and a copy of the code used.
The serving job's own 4-hour limit ends the test too, so start long suites early.

To run from elsewhere, pass the gateway directly, for example over the SSH tunnel from the
[node guide](../node/README.md#calling-the-api). Without `--plan` there are no server metrics:

```bash
python3 b300/loadtest/loadtest.py ramp --base-url http://127.0.0.1:18080/v1 --key-file gateway.key
```

## Commands

| Command | What it does |
| --- | --- |
| `plan` | Offline demand forecast per profile and size (calls/s and prompt/generated tokens/s per model). Sends nothing. |
| `run` | One profile at one size for `--stage` (default 15 min). |
| `ramp` | Stages at `--employees` (default 100, 500, 1000, 10000). Then ×`--grow` (1.5) while all targets pass. After the first failure, `--refine` (2) bisection stages narrow the edge. Up to `--max-stages` (12). |
| `suite` | `ramp` for each of `--profiles` (default all four), 4-minute stages, at most 8 per profile. |
| `sweep` | Closed loop: N clients send one request shape back to back, N = `--levels`. Gives each model's raw saturation curve (tokens/s vs latency). |
| `report DIR` | Rebuild `summary.md` from a result folder, for example after an interrupted run. |

Common options: `--pattern steady|morning|bursty|backlog`, `--stage 5m`, `--warmup 90s`,
`--workers N`, `--role main=OTHER-MODEL`, `--natural-lengths`.
See `python3 b300/loadtest/loadtest.py COMMAND --help`.

Typical session on a fresh node:

```bash
python3 b300/loadtest/loadtest.py plan                                   # what the profiles ask for
b300/loadtest/loadtest.sh JOBID sweep --sweep-role main --input 4000 --output 400
b300/loadtest/loadtest.sh JOBID sweep --sweep-role fast --input 4000 --output 400
b300/loadtest/loadtest.sh JOBID suite --profiles agentic,mixed
b300/loadtest/loadtest.sh JOBID run --profile mixed --pattern bursty --employees <edge found above>
```

## The usage model

All numbers live in the tables at the top of [`workload.py`](workload.py); edit them to match a real company.

**Who.** 60% of employees use the AI at all, and 70% of those on a given day. Active users are
*light*, *regular* or *power* users. Sessions per active user per workday:

| Archetype | Chat | Document chat | Uploads | Agent tasks | Meetings |
| --- | ---: | ---: | ---: | ---: | ---: |
| light | 2 | 0.5 | 0.3 | 0.1 | – |
| regular | 5 | 2 | 1 | 1 | 0.2 |
| power | 10 | 4 | 3 | 8 | 0.5 |

On top of that come machine traffic per employee per day: 0.5 documents into the RAG index
and 3 automation runs. The test plays the **busiest hour**: 1.8× the average hour of an
8-hour day. Ingest runs at the average rate.

**Profiles** change the archetype mix and scale individual workflows:

| Profile | Meaning | Main model at 1,000 / 10,000 employees (forecast) |
| --- | --- | --- |
| `chat` | Early adoption: mostly LibreChat conversations, little automation | 200 / 2,000 gen tok/s |
| `knowledge` | RAG-heavy: document chat, uploads, 4× ingest | 450 / 4,500 gen tok/s |
| `mixed` | Balanced enterprise (default) | 480 / 4,800 gen tok/s; 9k / 91k prompt tok/s |
| `agentic` | Mature AI org: 25% power users, 3× agent tasks, 3× automations | 1,900 / 18,700 gen tok/s; 42k / 420k prompt tok/s |

(`plan` prints the full table for every model, including the share of prompt tokens per model.)

**Patterns** shape arrivals over a stage:
- `steady`: a constant rate.
- `morning`: a log-in wave that overshoots to 1.3× and settles.
- `bursty`: 1 minute at 3× every 5 minutes, then 0.6×. Correlated spikes: a meeting ends, a deadline hits.
- `backlog`: 2% of employees' documents are dumped into ingest at once, plus a queue of automation runs.

Arrivals are random (Poisson), so small organisations are naturally spiky and large ones smooth out.

**What a session sends.** Every workflow makes the calls its real counterpart would:

| Workflow | Calls |
| --- | --- |
| `chat` (LibreChat) | Each turn is guarded by the safety classifier (plus the policy model on 20% of turns), then streamed from GLM (70%) or the fast model. History grows; 20% of messages are pastes of 0.5–12k tokens. A title comes from the fast model. 40 s typical think time. |
| `doc_chat` (RAG) | Per question: guard, embed query, rerank 40 candidates, answer from GLM with 8 retrieved chunks. Popular documents recur across users. |
| `upload` | OCR via the vision model for 15% of files (scanned). Chunk, embed in batches of 32, summarise the whole file (up to 60k tokens) on GLM, then 0–2 follow-up questions over its chunks. |
| `ingest` (pipeline) | OCR for 25%, embeddings, metadata extraction, and for 30% contextual-retrieval calls. Those send the whole document as a shared prefix with every chunk. |
| `agent` | Tool loop on GLM (typically 7 steps, up to 30). Tool results of 50–8,000 tokens are appended, and the history is compacted beyond 60k tokens. 12% of steps fan out 2–5 sub-agents on GLM or the fast model, nested up to two levels. |
| `automation` (Langflow) | Triage (guard + short JSON), document extraction (JSON), or drafting on GLM. Not streamed. |
| `meeting` | Whisper over 30-second segments (typically 30), then a GLM summary. |

Text is synthetic: random business words calibrated to the main model's tokenizer
at start-up. Images are synthetic scanned pages and audio is quiet noise; each is unique so
encoder caches miss. System prompts are shared by all users, as they would be in reality,
so prefix caching works the way it would in production. Output lengths are forced with vLLM's
`min_tokens` so the load does not depend on what the model happens to write.

## Targets (SLOs) and reading the report

A stage **passes** when the 95th percentiles meet these targets (`SLO` in `workload.py`).
The first `--warmup` of each stage isn't judged. Calls still running when a stage ends count
at their current age, so a stalled server can't hide.

| Class | Calls | Target |
| --- | --- | --- |
| `interactive` | chat answers, RAG answers, upload follow-ups | TTFT ≤ 3 s, time per output token ≤ 100 ms |
| `longctx` | summary over a whole uploaded file | TTFT ≤ 20 s |
| `agent` | agent and sub-agent steps | TTFT ≤ 10 s, time per token ≤ 120 ms |
| `guard` | safety and policy checks before a turn | ≤ 1.5 s |
| `retrieval` | query embedding, reranking | ≤ 1 s |
| `background` | ingest, OCR, titles, automations, transcription | ≤ 120 s per call |
| all | | errors ≤ 1% |

`summary.md` contains:
- the highest passing size, the first failing size and the likely bottleneck (the vLLM server with the longest queue);
- a table of all stages;
- per stage: latency per model and per workflow step, end-to-end session times, and the vLLM servers'
  running/waiting requests, KV-cache use, preemptions, prefix-cache hit rate and real token throughput.

What to look for:
- **A growing `Waiting max`** on one server is the bottleneck. Shift GPUs to it in `lineup.json`, or move work to another model.
- **KV cache near 100% with preemptions:** context or concurrency limits are too high for that server's memory share.
- **A good TTFT but a slow time per token:** the decode batch is too large. Lower `--max-num-seqs`, or try `glm-fast`.
- **Guard p95 failing first:** the small classifiers sit in front of every chat turn, so give them more slots.
- **Generator lag > 1 s:** the load generator itself is the limit. Add `--workers` / `LOADTEST_CPUS`.

## Trying it without the cluster

```bash
echo secret > /tmp/key
python3 b300/loadtest/fake_server.py --key-file /tmp/key --slots 24 --decode 400 &
python3 b300/loadtest/loadtest.py ramp --base-url http://127.0.0.1:18999/v1 --key-file /tmp/key \
  --employees 200,2000 --stage 20s --warmup 5s --time-scale 0.02
python3 -m unittest discover -s b300/loadtest -p 'test_*.py' -v
```

`--time-scale 0.02` runs think times and tool delays 50× faster and raises arrival rates to
match, so a ramp takes a minute. It is a testing aid; never use it against the real node.

## Caveats

- **`min_tokens` and `stream_options` must reach vLLM through agentgateway.** If calls fail with
  HTTP 400, rerun with `--natural-lengths`. If the report's `Avg out` is far below the target,
  outputs end early and the load is lighter than planned.
- **The load generator shares the node's CPUs** with vLLM's API servers. Eight CPUs out of 64 is
  usually harmless; watch `Generator lag`.
- **Agents don't execute real tool calls.** They append tool results of realistic size, and tool
  definitions are part of the system prompt rather than the `tools` field. That keeps the model's
  tool-call parser out of the measurement.
- **Stepping down after an overloaded stage** cancels all sessions and waits for idle servers
  (`--cooldown`). The smaller stage then fills up from empty during its longer warm-up; it is
  slightly optimistic for very long agent sessions.
- **The rates are assumptions.** They are a plausible starting point, not measured usage of a
  real company. Change the tables in `workload.py` when you have real numbers.
