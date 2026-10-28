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

## B · GLM-5.3-Flash on B300 with a larger context — done (needs a GPU start to confirm)

- `b300` profile `glm53` (8 × B300, TP8): `--max-model-len 65536`, `--max-num-seqs 16`,
  `--max-num-batched-tokens 16384`; release manifest regenerated; marked "not yet GPU-validated".
- Sizing and what to check on the next start: [b300/docs/glm-context.md](../../../b300/docs/glm-context.md).
  Rough numbers: 288 GB per GPU × 0.90 ≈ 259 GB, weights ≈ 41 GB per GPU, ≈ 200 GB per GPU for the KV cache;
  at an estimated 100–160 KB per token (MLA cache replicated per TP rank) that is ≈ 1.3–1.9 M cached tokens, so
  16 × 65,536 fits. Public sources report a 1,048,576-token `max_position_embeddings`; `config.json` could not be
  read from here.
- **Next start:** `./b300/cli.py up glm53`, then send back from `BASE/users/LOGIN/runs/RUN_ID/glm.server.log`
  the lines `GPU KV cache size: N tokens` and `Maximum concurrency for 65536 tokens per request: Nx`
  (≥ 32x → 131,072 tokens with 16 slots fits).
- Level defaults raised for that context: agent and green generator `max_tokens` 4,096, attacker and simulator
  2,048, judges and evaluator 1,024. The Leonardo `glm53` profile still has 2,048 tokens: lower them there.
- New CI job `b300` (repository check, unit tests, vermin ≥ 3.9).

## D · UI — in progress

## C · DeepTeam — multi-turn prompts done (see A)

## E · Clean-up — partly done

**Branches to delete on the fork** (this session may push only to `spec/config-driven-arena`; deleting returned
HTTP 403, so please delete them in the GitHub UI):

| Branch | Why it can go |
| --- | --- |
| `claude/friendly-einstein-qhzkt5`, `claude/leonardo-cluster-link`, `claude/leonardo-profiles`, `claude/lucid-brown-jszkf2`, `codex/b300-cli-team-guide`, `feat/arena-deepteam-integration` | fully contained in `main` |
| `fix/indirect-levels`, `claude/levels-glm-b300` | merged into this branch; delete once this branch is merged |

Keep or check by hand: `abliterated-model` (1 commit, not in `main`), `claude/leonardo-models-and-link` (5 commits;
their content looks re-committed on `main` under other hashes), `feat/deep-team-ui` (Marina's original UI, used
as a template only).
