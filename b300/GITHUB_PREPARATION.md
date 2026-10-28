# B300 team branch preparation

Prepared on **28 October 2026** for the user's request to share the B300 CLI
with teammates through a Git branch.

- Repository: `CodifyAG/european-ai-hackathon-2026`.
- Branch: `codex/b300-cli-team-guide`.
- Base: latest fetched `origin/main`, commit `c3c5dd44d3db472c13bb5de9b072b5822446f642`.
- Scope: the reviewed `b300/` directory only. Existing local application and
  Leonardo modifications are excluded and preserved in their original checkout.
- Publication: branch only; no merge into `main` is part of this change.

## Reviewer overview

Add a B300 counterpart to the Leonardo workflow: private AXIS SSH, team settings,
bounded Slurm preparation/serving, guarded readiness, local model port forwarding,
API queries and owned-job shutdown. Include pinned native GLM TP8/PP1,
Nemotron Ultra NVFP4, Safety 4B BF16 and GPT-OSS-Safeguard MXFP4/BF16
configurations, the direct-client guide and strict native classifier protocols.

Large assets are prepared inside the allocation and kept in temporary node
scratch. Keys and frozen run sources remain private to each login. Duplicate
or uncertain submissions are not automatically replayed. Private session files
are published atomically to support simultaneous queries.

## Validation and limits

The original package passed nine lifecycle tests and twelve client tests,
source/shell/profile/link checks and actual B300/Mac lifecycle checks for
standalone Ultra, the five-GPU pair, GLM and Safeguard. Owned-job cleanup was
verified. The earlier standalone Safety smoke test is recorded separately.
See [the report](docs/CLI_ALL_PROFILES_20261028.md) and
[coverage matrix](docs/validation.md) for exact evidence and limitations.

The serving source and client code remain byte-identical to the reviewed
package. Branch preparation updates documentation and the publication manifest.
Offline checks are rerun against this branch; GPU tests are not replayed for
documentation edits. Kubernetes, sustained load, another laptop and shared-base
ACL validation remain separate work.

## Publication contents

The README is the main server-start/API guide. `serve/` contains the portable
runtime, `cli.py` is the laptop entry point and `clients/` contains direct clients.
The serving manifest freezes runtime bytes; `manifest.json` records the reviewed
publication files. Model weights and private runtime files are excluded.

No `.env`, issued AXIS command, private compute address, API key, raw request
ledger or live runtime state is included. The sanitized evidence summary
identifies historical results and verified source hashes. The repository MIT
notice is preserved; checkpoint licensing is separate from the serving code.
