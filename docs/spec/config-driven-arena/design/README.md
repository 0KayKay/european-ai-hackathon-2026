# Design reference (non-binding)

These screens show how the MVP *could* look. They are a recommendation, not a pixel spec. Follow the idea, the
structure and the general look; details may differ (see [SPEC §6](../SPEC.md#6-design-reference-non-binding)). Dark mode
is not required. All numbers and texts are sample values.

| Screen | What it shows | Files |
|---|---|---|
| Level view · direct, multi-turn (level 1) | config source, execution graph with input guardrails, turn loop back to the attacker, node inspector with prompt | [html](01-level-view-direct-multi-turn.html) · [png](01-level-view-direct-multi-turn.png) |
| Level view · indirect, single-turn (level 2) | attacker writes one email; injection path inbox → `get_emails` → sanitizer → agent; fixed user task; guardrails around `send_email` and on output; no reply to the attacker | [html](02-level-view-indirect-single-turn.html) · [png](02-level-view-indirect-single-turn.png) |
| Run in progress | progress per team, live outcome counts, live sample with current step, generated attacks for a sanity check | [html](03-run-in-progress.html) · [png](03-run-in-progress.png) |
| Run results | attack success rate, false-positive rate, expected vs. outcome, by attack type, by deciding guardrail, filterable case list | [html](04-run-results.html) · [png](04-run-results.png) |
| Case replay | visited path on the graph, playback bar, timestamped event log with guardrail decisions and tool calls | [html](05-case-replay.html) · [png](05-case-replay.png) |

The HTML files are static and self-contained; fonts (Geist, Geist Mono) load from Google Fonts.

Source canvas (page "v2 · revised"; page "v1 · first draft" is superseded):
https://claude.ai/artifact/3xJcETdepVvkukB6H41M7k
