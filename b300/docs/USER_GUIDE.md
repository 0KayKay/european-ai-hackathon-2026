> For the new server installation/start/stop workflow, use the [main B300 guide](../README.md). This page describes the separately tested historical direct endpoint-handoff client. Its GLM loopback discussion concerns the earlier experiment; the new CLI binds GLM to a private compute IP and has a verified Mac forwarding path. Keep the complete `b300/` directory: the current query client also uses the bundled native Safeguard protocol helper.

# Using GLM and Nemotron on B300

Updated: 28 October 2026, Europe/Berlin. Audience: approved teammates using
internal model APIs. All commands below are run from the `b300/` directory
unless a different machine or working directory is stated.

## 1. Choose a model

| Model | Choose it for | API alias | Recorded server port | Configured context |
| --- | --- | --- | --- | --- |
| GLM-5.3-Flash | General chat, reasoning, producing tool calls | `glm-5.3-flash-leonardo` | 8000, compute loopback | 65,536 tokens (historical runs: 2,048) |
| Nemotron Ultra 550B NVFP4 | Large general model; reasoning or policy judgments | `nemotron-ultra-550b-b300` | 18085, private compute IP | 8,192 tokens |
| Nemotron safety 4B | Classify a message against an explicit safety policy | `nemotron-content-safety-reasoning-4b-b300` | 18086, private compute IP | 4,096 tokens |

Context includes input, template and generated tokens. These are the deployment
limits, not advertised maximum model contexts. Ultra is the official 550B-total
parameter NVFP4 checkpoint; the four-GPU configuration does not change its
parameter count. The 4B model is a classifier, not a general chat replacement.

## 2. Obtain current access

Get the following privately from the model operator:

1. A **currently running Slurm job ID** and its expected expiry time.
2. The HTTP base URL accessible from your machine, including `/v1`.
3. The exact API model alias above and its **session API key**.
4. For Nemotron SSH access, the current `endpoint.json` and your own AXIS access.

Historical jobs `11393` (GLM) and `11962` (Nemotron) are finished. Their old IPs,
keys and endpoint files must not be reused. No permanent public URL is provided
by this package. Allocation, model loading and API readiness are separate states.

Use your own account at the [AXIS portal](https://axis-raplabhackathon.axisportal.io/apps),
open **Curiosity v2 Login**, then select the desktop SSH or Web SSH option.
AXIS credentials authenticate cluster access; the model API uses its separate
session key. A Hugging Face download token is not a model API key.

The checked GLM server binds to `127.0.0.1:8000` **on the compute node**, inside
the deployment's network namespace. Your laptop's loopback and the login node's
loopback are different addresses. A forward ending at the login node's
`127.0.0.1:8000` will not reach it. The operator must arrange a verified route,
or run your client inside the same allocated node/container network namespace.
The original automated GLM job exits after its checks; it is not a persistent
user server. See [operator notes](OPERATOR_NOTES.md).

## 3. Save your API key privately

Local terminal, Python 3.9+; no third-party packages are needed:

```bash
mkdir -p .runtime
chmod 700 .runtime
python3 clients/nemotron_client.py key --key-file .runtime/glm.api-key
# Paste the GLM session key into the hidden prompt.
```

The key-entry command only saves a secret and works for any of the three APIs.
It refuses to overwrite an existing key. For a new session, use a new filename.
Use separate key files for GLM, Ultra and safety. Obtain keys privately; never
put them in a command argument, Markdown document, screenshot or Git commit.

## 4. GLM: a first request

**Inside the allocated GLM server's compute/container network namespace**, with
your key file available there:

```bash
python3 clients/query.py --profile glm \
  --base-url http://127.0.0.1:8000/v1 \
  --key-file .runtime/glm.api-key --action models

python3 clients/query.py --profile glm \
  --base-url http://127.0.0.1:8000/v1 \
  --key-file .runtime/glm.api-key \
  --text 'Explain what a GPU does in one short English sentence.'
```

On your laptop, replace `--base-url` with the **operator-verified local tunnel or
gateway URL**. Do not treat the historical Leonardo client port `18083` as proof
that a B300 tunnel exists. The client sends the B300-tested template settings
`reasoning_effort=low` and `clear_thinking=true`, temperature zero and a
1,024-token default output budget. Reduce the output budget with `--max-tokens`
if your input approaches the context limit: 2,048 tokens in the historical
experiment, 65,536 in the current launcher ([sizing](glm-context.md)).

The historical GLM API validation covered authentication, catalog, arithmetic,
English output, streaming, tool-call JSON and four concurrent requests. A tool
call is a model-generated proposal; your application must authorize and execute
tools. This client does not execute tools.

## 5. Nemotron: establish one tunnel for both models

This workflow was **actually verified on the user's Mac on 28 October 2026**
using a provided team account distinct from the operator. Both APIs were reached
through one fresh SSH connection; the operator's existing SSH control socket
was not reused. See [actual verification](MAC_CONNECTIVITY_20261028.md). Another
computer was not tested. Each colleague needs their own AXIS authorization and
current endpoint plus model keys from the operator.

Save a freshly issued AXIS desktop-SSH command privately as
`.runtime/axis-ssh-command`. The helper accepts the portal form `ssh HOST -l USER`.
Save the operator's current `endpoint.json` as `.runtime/endpoint.json`.
Neither file is supplied in the ZIP. Verify the AXIS host identity through the
trusted event/operator process first; the client preserves
`StrictHostKeyChecking=yes` and requires a trusted host-key entry.

```bash
chmod 600 .runtime/axis-ssh-command .runtime/endpoint.json
python3 clients/nemotron_client.py key --key-file .runtime/ultra.api-key
python3 clients/nemotron_client.py key --key-file .runtime/safety.api-key
python3 clients/nemotron_client.py tunnel --kind both \
  --endpoint .runtime/endpoint.json \
  --ssh-command-file .runtime/axis-ssh-command
```

Paste each distinct model key into its hidden prompt. Keep the tunnel terminal
open. One connection forwards both `127.0.0.1:18085` (Ultra) and
`127.0.0.1:18086` (safety). A fresh, unused AXIS command is preferable to repeatedly
opening new connections with an old one-time command. If SSH exits before
listening, obtain/verify a current command and inspect the error; do not disable
host checking or interpret it as a model result. Early handshake failures were
observed before the successful final run.

In another terminal, query Ultra:

```bash
python3 clients/query.py --profile ultra \
  --base-url http://127.0.0.1:18085/v1 \
  --key-file .runtime/ultra.api-key \
  --text 'Explain what a GPU does in one short English sentence.'
```

If local ports are occupied, add `--local-ultra-port 19085
--local-safety-port 19086` to the combined tunnel command and use those same
ports in the query URLs. The single-model `--local-port` option is not used with
`--kind both`. The remote ports still come from the current private endpoint.

## 6. Safety classification with 4B or Ultra

In another terminal, after the safety tunnel is established:

```bash
python3 clients/query.py --profile safety \
  --base-url http://127.0.0.1:18086/v1 \
  --key-file .runtime/safety.api-key --action classify \
  --policy-file examples/safety-policy.txt --max-tokens 128 \
  --text 'What is the capital of France?'
```

Expected output format is `Prompt harm: harmful` or `Prompt harm: unharmful`.
An unexpected or truncated response is an error, not an automatic safe verdict.
The example policy is a demonstration and is **not the application's full
supplier-access policy or the historical evaluation corpus**.

Ultra can classify the same text under the same policy by changing the profile,
port and key:

```bash
python3 clients/query.py --profile ultra \
  --base-url http://127.0.0.1:18085/v1 \
  --key-file .runtime/ultra.api-key --action classify \
  --policy-file examples/safety-policy.txt --max-tokens 128 \
  --text 'What is the capital of France?'
```

The client disables Ultra thinking for these short requests. A safety verdict
does not itself enforce file or tool permissions. The application must decide
whether to block and must independently enforce authorization. No external model
API is called by this client.

## 7. Common failures

| Symptom | Check |
| --- | --- |
| Job pending / `QOSGrpGRES` | Team GPU capacity; this is not model loading. GLM needs eight GPUs; the Nemotron pair needs five. |
| HTTP 401 or 403 | Use the current key for the selected model, not your AXIS credential. |
| Connection refused / timeout | Job still running, API ready, correct node/namespace, tunnel alive and correct local port. |
| SSH host verification failed | Confirm the expected host identity with the operator; preserve strict checking. |
| Model not found | Use the exact served alias, not the Hugging Face repository name. |
| Context exceeded / truncated output | Shorten input or adjust the output budget within the configured context. |
| Invalid classifier output | Record an error and inspect it; do not allow the request based on a guessed verdict. |
| Checkpoint disappeared | Node-local `/tmp` is temporary; the operator may need a new download. |

Closing your own tunnel stops your local connection; it does not cancel the
server. Coordinate shared-server shutdown with its owner. Only the operator
should cancel the explicitly identified owned job, then verify termination.
The recorded server jobs used a four-hour allocation bound, including loading
and tests after allocation. Queue wait is separate.

## 8. What was measured

See [verified results](VERIFIED_RESULTS.md). GLM passed 21/21 functional checks.
The paired safety comparison completed 280 scored calls: 4B blocked 60/70 attacks
and Ultra 70/70, with one benign false block each. This was input classification
on a known small suite, not a full application security guarantee.


## 9. Save a local connection proof

Add `--output .runtime/connection-proof.json` to a common-client command to save
the actual response ID, model, final content and usage privately. Keys and hidden
reasoning are excluded. The CLI returns a nonzero status on request or response
validation errors. The 28 October check used this option for five actual model
completions; it did not simulate model responses.

A new session requires a current endpoint and fresh key. If the AXIS SSH command
has expired, issue a new command through your own portal account; an API key
cannot repair an expired SSH login. Closing a test tunnel does not close the GPU
server. Open your own tunnel terminals when following the guide.
