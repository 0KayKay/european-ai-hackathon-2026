# Decision log

Decisions taken during the spec discussion on 2026-10-27. The memo numbers refer to the verbatim transcript in
[`transcript/`](transcript/) (German, as spoken).

| # | Decision | Rationale | Memo |
|---|---|---|---|
| D1 | The event has two activities for two groups: the **security team** adapts guardrails and levels, the **red team** adapts attacker agents. A **green team** dataset models benign use. | New this year: automated attackers instead of only human red-teaming; hardening must be measured against both attacks and normal use. | 01 |
| D2 | Levels are described declaratively in a config file; the UI is rendered from it and the backend executes it. | One abstraction that can cover different learning scenarios; the email example must be expressible. | 01 |
| D3 | **Primary acceptance criterion:** the three existing levels × three difficulties are reproduced from config with exactly the same behavior. | We already have a working reference system. | 01 |
| D4 | Step 1 only renders UI from config. Editing the config in the UI (also during play) is step 2. | Keep the MVP small. | 01 |
| D5 | Metrics: how many attacks got through, *and* how many benign requests were wrongly blocked; per attack type. | The goal is a secure **and** usable system; today there are no statistics at all. | 01 |
| D6 | Dataset generation (red and green) is part of the first step. | Without datasets there is nothing to evaluate. | 02 |
| D7 | The attack type must be configurable. The MVP uses DeepTeam (much out of the box), but the spec is not tied to it. | MVP first; the generator is an implementation decision. | 02 |
| D8 | agentgateway content-moderation endpoints (Michi's work) are out of scope. | Avoid getting lost; can become a new guardrail type later. | 02 |
| D9 | The guardrail position is part of the config (between user and agent, agent and output, around tools …); the graph shows the guardrail exactly there. | Seeing where a guardrail sits is central for learning. | 02 |
| D10 | Make a UI design draft before implementation; use Marina's `feat/deep-team-ui` branch as the starting concept. | Check the direction early. | 02 |
| D11 | Most important: everything can be traced after a run. During a run: progress and number of successes. A live view of the running case is nice to have and may stay simple. | Nobody wants to watch for hours; learning happens by looking at the cases that got through. | 03 |
| D12 | A run executes all N attempts; it does not stop at the first success. | Statistics need the full run. | 03 |
| D13 | Serial execution is fine; parallel only if about as simple. With parallel runs, show one selectable sample live. | Keep the architecture simple; a good, coherent UI matters more. | 03 |
| D14 | Results can be report-like; the UI does not need to be complex. | — | 03 |
| D15 | Design artifacts and UI are in English. | The rest of the participants communicate in English. | 04 |
| D16 | Indirect injection (level 2, also level 3) is single-turn: the attacker enters via the email (the data the agent reads), a fixed user task triggers a fresh run, and nothing goes back to the attacker. The config must express direct/multi-turn vs. indirect/single-turn and where the injection goes. | v1 of the design showed this wrongly. | 04 |
| D17 | Show only data the system records. No generated interpretation of a case, no comparison between runs (that may come later). | Interpretations can be wrong depending on context; run-to-run data does not exist yet. | 04 |
| D18 | Design v2 is accepted as a **non-binding** reference: similar vibe and structure, details may differ; dark mode not required. | Implementation may find better solutions in details. | 05 |
| D19 | The spec is reviewed via a TL;DR of goals, MVP scope and acceptance criteria. Things we decided against appear only as out of scope. | Clear handover to an implementation session. | 05 |
| D20 | The manual (human-player) challenge mode is out of scope; it does not have to run on config levels. | The setup is built for autonomous attack agents; the manual mode still exists in the old version and could be added back later. | 06 |
| D21 | Models are configurable per LLM role (agent, judge, attacker, generator …), each with easily adjustable parameters (sampling, penalties …). | Parameters change model behavior a lot; generator, attacker and judge may need different models. | 06 |
| D22 | Single-turn datasets (levels 2 and 3, green cases) can be stored and reused between runs; multi-turn level 1 attacks cannot. | Reuse is essential for benchmarking; multi-turn conversations are adaptive. | 06 |
| D23 | The implementing agent builds the dataset-generation framework but does not author attack content itself; generation is the job of the evaluation run. Steps it cannot execute are done best effort. | Writing injections itself would likely hit content-moderation limits and is not needed. | 06 |
| D24 | The design canvases go into the branch as static HTML copies (with screenshots) as a non-binding reference. | The implementing agent needs the visual reference. | 06 |
