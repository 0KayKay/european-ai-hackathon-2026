#!/bin/bash
# Usage: ./smoke_test.sh [base_url]
# Uses $VLLM_API_KEY or ~/.config/leonardo-vllm/api_key.
# If SERVED_NAME is set, require that exact model rather than the first entry.
set -euo pipefail
URL="${1:-http://127.0.0.1:8000}"
KEY="${VLLM_API_KEY:-$(cat "${HOME}/.config/leonardo-vllm/api_key" 2>/dev/null || true)}"
[[ -n "$KEY" ]] || { echo "no API key: set VLLM_API_KEY" >&2; exit 1; }
TEST_DIR=$(mktemp -d); trap 'rm -rf "$TEST_DIR"' EXIT
HDR="$TEST_DIR/auth_header"
( umask 077; printf 'Authorization: Bearer %s\n' "$KEY" > "$HDR" )

echo "== GET $URL/v1/models"
models=$(curl -fsS -m 20 -H "@$HDR" "$URL/v1/models")
echo "$models"
model=$(printf '%s' "$models" | python3 -c '
import json, os, sys
response = json.load(sys.stdin)
if not isinstance(response, dict) or "error" in response:
    sys.exit("FAILED: /v1/models returned an error or invalid response")
data = response.get("data")
if not isinstance(data, list) or not data:
    sys.exit("FAILED: /v1/models returned no models")
ids = [item.get("id") for item in data if isinstance(item, dict)]
ids = [value for value in ids if isinstance(value, str) and value.strip()]
expected = os.environ.get("SERVED_NAME")
if not ids or (expected and expected not in ids):
    sys.exit("FAILED: expected model is unavailable")
print(expected or ids[0])')

python3 -c '
import json, sys
json.dump({"model": sys.argv[1], "messages": [{"role": "user", "content":
    "Say hello from the Leonardo supercomputer in one short sentence."}],
    "max_tokens": 64}, sys.stdout)' "$model" > "$TEST_DIR/request.json"

echo "== POST $URL/v1/chat/completions (model: $model)"
curl -fsS -m 120 -H "@$HDR" -H 'Content-Type: application/json' \
  "$URL/v1/chat/completions" --data-binary "@$TEST_DIR/request.json" > "$TEST_DIR/response.json"
python3 -c '
import json, sys
with open(sys.argv[1]) as stream:
    response = json.load(stream)
if not isinstance(response, dict) or "error" in response:
    sys.exit("FAILED: chat completion returned an error or invalid response")
choices = response.get("choices")
if not isinstance(choices, list) or not choices:
    sys.exit("FAILED: chat completion returned no choices")
for choice in choices:
    message = choice.get("message") if isinstance(choice, dict) else None
    if isinstance(message, dict) and any(
        isinstance(message.get(field), str) and message[field].strip()
        for field in ("content", "reasoning_content", "reasoning")
    ):
        print(json.dumps(response, indent=2))
        break
else:
    sys.exit("FAILED: chat completion returned no generated text")
' "$TEST_DIR/response.json"
echo "== smoke test passed"
