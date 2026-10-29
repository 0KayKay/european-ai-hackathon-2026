# Guardrail Arena — implementation status

State of branch `spec/config-driven-arena` after WP7 (integration), 2026-10-27. **Done** = implemented, covered
by automated tests and clicked through in a browser against the real backend with fake model and DeepTeam
services; **live check open** = additionally needs real models or the cluster (see
[TESTING.md](TESTING.md#what-still-needs-a-live-check)); **partial** / **open** = not (fully) implemented.

Backend tests are in `backend/tests/arena/`, frontend tests next to the components in `frontend/src/arena/`.

## Acceptance criteria

| ID | Status | Evidence |
| --- | --- | --- |
| AC-1 Parity | **Retired** | Arena levels now evolve independently of the legacy challenges; the parity tests and their model-settings helper have been removed. |
| AC-2 Graph from config | **Done** | `test_arena_graph.py` (`test_guardrail_groups_follow_positions`, `test_moving_a_guardrail_in_the_config_moves_it_in_the_graph`, `test_direct_multi_turn_level_has_reply_loop`, `test_indirect_levels_show_injection_and_no_reply_path`, `test_node_details_show_settings_full_prompts_and_models`); `ArenaGraph.test.tsx`, `layout.test.ts`, `ArenaApp.test.tsx`; all 4 levels × 3 difficulties rendered in the browser. |
| AC-3 New level without code | **Done**; evaluation with real red team open | `backend/levels/hidden-mailbox-output-judge/` (level.yaml + one prompt); `test_example_level_uses_an_output_judge_instead_of_an_input_judge`; a full run of it in the browser (fake models). |
| AC-4 Red team | **Partial** — backend, UI and contract done; the DeepTeam service endpoints are not implemented | `test_generated_cases_are_held_to_the_configured_counts`, `test_check_generated`, `test_deepteam_generate_speaks_the_ndjson_contract`, `test_deepteam_multi_turn_request`, `test_generated_inputs_and_live_case_are_visible_and_stop_keeps_completed_cases`; browser run against a fake service implementing IMPLEMENTATION §6: cases appear while generating. Missing: `deepteam/` `/internal/arena/generate` and `/multi-turn` (TESTING.md §1). |
| AC-5 Green team | **Done**; json_schema with real models open | `test_green_generator_direct_level`, `test_green_generator_indirect_level_respects_field_limits`, `test_green_case_blocked_by_a_guardrail_is_a_false_positive`; browser: G01–G10 run, a blocked benign case is listed as false positive. |
| AC-6 Run | **Done** | `test_direct_level_full_run`, `test_indirect_level_full_run_and_dataset_reuse`, `test_generated_inputs_and_live_case_are_visible_and_stop_keeps_completed_cases`, `test_startup_marks_unfinished_runs_interrupted`, `test_arena_store.py`; `RunPage.test.tsx`; browser: live progress and counts, stop, reload on every view, backend killed during a run → *interrupted* (Postgres). |
| AC-7 Results | **Done** | `test_results_rates_and_labels`, `test_api_responses_match_the_frontend_contract`; `RunPage.test.tsx` ("shows the rates exactly as returned", filters); browser: rates, matrix, by type, by decision, filters, export. |
| AC-8 Replay | **Done** | `test_events_follow_the_schema_and_reference_the_level_graph`, `test_guardrail_decisions_are_recorded_exactly_pending_then_final`; `CaseReplay.test.tsx`, `events.test.ts`; browser: single- and multi-turn replays, step/play/keyboard, previous/next case. |
| AC-9 Models per role | **Done** | `test_each_role_gets_its_own_model_parameters`, `test_variant_can_override_a_model_parameter_of_one_role`, `test_model_roles_cover_every_llm_role`; runs record `models` (case roles + generator roles, from the dataset when reused); inspector shows config values, the run page's "Models used" the recorded ones (during and after the run), the export has them. |
| AC-10 Dataset reuse | **Done** | `test_indirect_level_full_run_and_dataset_reuse`, `test_dataset_must_match_level_and_team`, `test_direct_level_red_cases_are_always_generated`, `test_postgres_store_full_run`; browser: a Level 2 run with stored red and green datasets executed exactly their cases (export compared). |

## Requirements

| Group | Status | Notes |
| --- | --- | --- |
| R-CFG-1..7 Level configuration | **Done** | YAML per level with variants (`backend/levels/README.md`); positions incl. `around:<tool>`; load errors name file, difficulty and field (`test_arena_loader.py`); a ModelSpec per role with `extra` pass-through (Leonardo `chat_template_kwargs`). Guardrails now have a display `label`. |
| R-EXE-1..2, 4..5, 7..8 Execution | **Done** | Engine in `hackathon/arena/engine/`; no level-specific branches; every guardrail decision recorded; events never contain headers or credentials (`test_events_never_contain_headers_or_credentials`; the per-run callback token is never stored or served). |
| R-EXE-3 Direct multi-turn | **Partial** | Engine and internal callback done and tested (`test_multi_turn_keeps_history_and_stops_when_success_is_met`, `test_internal_turn_callback_authentication`, browser run with a fake driver); the DeepTeam multi-turn driver is missing. |
| R-EXE-6 Manual mode | Out of scope | Legacy challenge code unchanged. |
| R-UI-1..6 Level view | **Done** | Graph, difficulty switch, inspector with prompts and models, read-only config pane, English UI. |
| R-RED-1, R-RED-3, R-RED-4 | **Done** (backend/UI) | Objective, attack types, attempts, models per role in config; indirect content validated against the injection fields; inputs visible while generating. |
| R-RED-2 DeepTeam generation | **Open** | Backend client and contract done; `deepteam/` endpoints to be built (TESTING.md §1). |
| R-GRN-1..3 Green team | **Done** | Generator with guidance prompt and JSON schema; false positives counted. |
| R-DAT-1..3 Datasets | **Done** | Single-turn cases stored per run; reuse on any difficulty of the level; direct red cases always generated. |
| R-RUN-1..2, 4, 6 Runs | **Done** | All cases run; progress, counts, elapsed, stop; persisted in Postgres (pooled; snapshot stored once). |
| R-RUN-3 Parallel execution | Serial (should) | Cases of one run run serially; separate runs run concurrently. |
| R-RUN-5 Live sample (should) | **Done** | Running case's graph and latest events on the run page. |
| R-RES-1..7 Results | **Done** | Rates per R-RES-1, matrix, by type, by deciding step, filterable case list with error reasons, JSON export, recorded data only. |
| R-RPL-1..3 Replay | **Done** | Highlighted path per turn, event log with all recorded fields, playback and case navigation. |

## Fixed in WP7 (integration)

- Graph nodes could not be clicked with a mouse (React Flow set `pointer-events: none`); jsdom tests had not
  noticed.
- Frontend/backend contract: event data (`attacker_message {text, team}`, `success_check {met, kind, detail}`),
  wire node kinds (`guardrail_group`, `tool_group`), missing fields (`status`, `error`, `generation`, `sources`,
  `messages`, `success`, `usage`, …), decision labels (`Input judge · UNSAFE`, …) and readable guardrail labels.
  `test_arena_contract.py` now checks every response's key set; the level fixtures are real backend responses.
- Failed DeepTeam/green generation and run errors were invisible; error cases showed no reason; stopped cases read
  "not run"; single-turn levels showed "≤ 1 turns"; "Models used" disappeared after a run.
- Postgres: one connection per operation and the 75 KB snapshot written on every update → pool + snapshot column;
  a case running at a crash stayed `running` → `stopped`.
- The internal callback was reachable through the public proxy chain for signed-in users → 404 at Caddy (and the
  Vite proxy), and the backend refuses forwarded requests on it.
- Infra: `ARENA_STORE`, `DEEPTEAM_URL`, `ARENA_CALLBACK_URL` in `infrastructure/base/local.yaml` (inherited by
  the GKE overlays) and `backend/.env.example`; the backend image contains `levels/`.

## Open items

1. DeepTeam arena endpoints (`deepteam/`), then a live red-team run (TESTING.md §1).
2. Live checks with Gemini and Leonardo models: green generator and judges with `json_schema`, the agent's tool
   calls, Leonardo context limits (TESTING.md §2, "Connecting real models").
3. Postgres 18 in the cluster and the edge checks on GKE (TESTING.md §3–4).
4. agentgateway itself has no deny route for `/api/v1/arena/internal/` (its config schema could not be validated
   offline); Caddy and the backend cover it.
5. Cosmetic: the agent node is labelled with `agent.name` (e.g. `hidden-mailbox`); a tool call event is recorded
   before the `around:<tool>` judge decides it (the replay shows the request, then the decision).
