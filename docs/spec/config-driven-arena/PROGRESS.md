# Progress log — night 2 (2026-10-28/29)

Work branch: `spec/config-driven-arena`, restarted from `main` (1aca0a8) after PR #12 was merged. Each package is
pushed when it is done; this file is updated with it, so the branch always says what is finished and what is not.

Priorities agreed with the team: A (merge loose ends) → B (GLM profile on B300) → D (UI) → C (DeepTeam, only via
sub-agents) → E (clean-up).

## A · Merge loose ends — done

- `fix/indirect-levels` merged: clearer red-team objectives for levels 2 and 3, a corrected offer-review system
  prompt (it named tools and arguments that do not exist), `UnexpectedModelBehavior` reported as a case error.
- The offer-review system prompt now deliberately differs from the legacy implementation. The parity tests put the
  legacy prompt back for the comparison (`backend/tests/arena/legacy_models.py`), as they already do for the model
  settings; the static test checks that the new prompt names the real tool signatures.
- Leftovers of `claude/levels-glm-b300` taken over (README/TESTING wording for GLM-5.3-Flash, loader test); the
  level files there were identical to `main`.
- `test_a_second_stop_does_not_interrupt_saving_the_stopped_run` was timing-dependent (a fixed 0.2 s hold); the
  held save now waits for an explicit release.
- deepteam (via sub-agent): ruff formatting that failed CI, and multi-turn opening/follow-up prompts now come from
  the request's objective instead of being written for Level 1 only.

## B · GLM-5.3-Flash on B300 with a larger context — in progress

## D · UI — in progress

## C · DeepTeam — multi-turn prompts done (see A)

## E · Clean-up — open
