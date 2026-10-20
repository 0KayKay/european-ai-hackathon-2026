"""Local regressions: no cluster login, container execution, or GPU allocation."""
import contextlib
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

KIT = Path(__file__).resolve().parents[1]
MODELS = {"data": [{"id": "test-model"}]}
ANSWER = {"choices": [{"message": {"content": "Hello from Leonardo."}}]}


@contextlib.contextmanager
def api(models=MODELS, response=ANSWER, get_status=200, post_status=200):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, payload):
            self.send_response(status)
            self.end_headers()
            self.wfile.write((payload if isinstance(payload, str)
                              else json.dumps(payload)).encode())

        def do_GET(self):
            self.reply(get_status, models)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.reply(post_status, response)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def environment():
    env = os.environ.copy()
    env.update(VLLM_API_KEY="local-regression-dummy-key", SERVED_NAME="test-model",
               NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    # smoke_test.sh uses python3; use the interpreter running these tests.
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    for key in ("ENDPOINT_NAME", "MODEL_ID", "BASE", "SCRIPT_DIR"):
        env.pop(key, None)
    return env


def executable(path, text):
    path.write_text(text)
    path.chmod(0o755)


class SmokeTests(unittest.TestCase):
    def smoke(self, url):
        return subprocess.run(["bash", str(KIT / "smoke_test.sh"), url],
                              env=environment(), capture_output=True, text=True,
                              timeout=10)

    def test_generated_content_and_reasoning_succeed(self):
        for field in ("content", "reasoning_content", "reasoning"):
            with self.subTest(field=field), api(
                response={"choices": [{"message": {field: "Generated text"}}]}
            ) as url:
                result = self.smoke(url)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("smoke test passed", result.stdout)

    def test_failed_or_empty_responses_never_succeed(self):
        cases = [
            {"post_status": 500, "response": {"error": {"message": "failed"}}},
            {"get_status": 401, "models": {"error": "unauthorized"}},
            {"models": {"data": []}},
            {"models": {"data": [{"id": "wrong-model"}]}},
            {"models": "not JSON"},
            {"response": "not JSON"},
            {"response": {"error": "failed despite HTTP 200"}},
            {"response": {"choices": []}},
            {"response": {"choices": [{"message": {"content": " "}}]}},
            {"response": {"choices": [{"text": "wrong response schema"}]}},
        ]
        for case in cases:
            with self.subTest(case=case), api(**case) as url:
                result = self.smoke(url)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("smoke test passed", result.stdout)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        executable(self.bin / "singularity", "#!/bin/sh\nexit 0\n")
        executable(self.bin / "getent", "#!/bin/sh\nprintf '127.0.0.1 %s\\n' \"$2\"\n")
        self.env = environment()
        self.env.update(WORK=str(self.root / "work"), TEAM="test",
                        HOME=str(self.root / "home"), SLURM_JOB_ID="101",
                        MODEL_ID="test/model", REAL_KIT=str(KIT))
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env["PATH"]

    def tearDown(self):
        self.tmp.cleanup()

    def shell(self, code):
        return subprocess.run(["bash", "-c", 'set -euo pipefail; source "$REAL_KIT/common.sh"; ' + code],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def test_model_names_and_endpoint_files_are_distinct(self):
        result = self.shell('''
MODEL_ID=org/model; a=$(model_endpoint_name vllm-multi)
MODEL_ID=org_model; b=$(model_endpoint_name vllm-multi)
[[ "$a" != "$b" ]]
MODEL_ID=org/model; [[ "$a" == "$(model_endpoint_name vllm-multi)" ]]
SERVED_NAME=main; claim_endpoint "$a"; write_endpoint "$a" node1 20001
SERVED_NAME=moderation; claim_endpoint "$b"; write_endpoint "$b" node2 20002
grep -Fxq MODEL=main "$ENDPOINT_DIR/$a.env"
grep -Fxq MODEL=moderation "$ENDPOINT_DIR/$b.env"
remove_endpoint "$a"; [[ -f "$ENDPOINT_DIR/$b.env" ]]
remove_endpoint "$b"
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list((self.root / "work/test/endpoints").iterdir()), [])

    def test_duplicate_claim_and_foreign_cleanup_preserve_owner(self):
        result = self.shell('''
owner=$ENDPOINT_OWNER
claim_endpoint main; write_endpoint main node1 20001
ENDPOINT_OWNER=another-job
if claim_endpoint main; then exit 90; fi
remove_endpoint main
[[ -f "$ENDPOINT_DIR/main.env" && -d "$ENDPOINT_DIR/.main.lock" ]]
grep -Fxq PORT=20001 "$ENDPOINT_DIR/main.env"
ENDPOINT_OWNER=$owner; remove_endpoint main
claim_endpoint main; write_endpoint main node2 20002
grep -Fxq PORT=20002 "$ENDPOINT_DIR/main.env"
remove_endpoint main
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_concurrent_claims_have_one_winner(self):
        processes = [subprocess.Popen(
            ["bash", "-c", 'source "$REAL_KIT/common.sh"; claim_endpoint same-name'],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)]
        codes = []
        for process in processes:
            process.communicate(timeout=10)
            codes.append(process.returncode)
        self.assertEqual(sorted(codes), [0, 1])

    def test_unsafe_names_and_unclaimed_publication_fail(self):
        for code in ('claim_endpoint ../outside', 'ENDPOINT_NAME=../outside; model_endpoint_name vllm-multi',
                     'write_endpoint unclaimed node1 20000'):
            with self.subTest(code=code):
                self.assertNotEqual(self.shell(code).returncode, 0)

    def test_failed_inference_stops_jobs_without_publishing(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        (scripts / "common.sh").write_text('''source "$REAL_KIT/common.sh"
pick_port() { echo "$TEST_SERVER_PORT"; }
wait_healthy() {
  for i in $(seq 1 100); do [[ -s "$TEST_PID_FILE" ]] && return 0; sleep 0.01; done
  return 1
}
''')
        (scripts / "smoke_test.sh").symlink_to(KIT / "smoke_test.sh")
        executable(scripts / "run_vllm.sh", '#!/bin/bash\necho $$ > "$TEST_PID_FILE"\nexec sleep 60\n')
        executable(self.bin / "nvidia-smi", '#!/bin/sh\necho "GPU 0: mock"\n')
        executable(self.bin / "scontrol", '#!/bin/sh\necho node1\n')
        executable(self.bin / "srun", '#!/bin/bash\nwhile [[ "$1" == --* ]]; do shift; done\nexec "$@"\n')
        for name in ("02_vllm_single.sbatch", "04_vllm_multinode.sbatch"):
            with self.subTest(script=name), api(post_status=500, response={"error": "failed"}) as url:
                pid_file = self.root / (name + ".pid")
                env = dict(self.env, SLURM_SUBMIT_DIR=str(scripts), SCRIPT_DIR=str(scripts),
                           SLURM_JOB_NAME="test-job", SLURM_NNODES="2",
                           SLURM_JOB_NODELIST="node1,node2", SLURM_CPUS_PER_TASK="32",
                           TEST_SERVER_PORT=url.rsplit(":", 1)[1], TEST_PID_FILE=str(pid_file),
                           ENDPOINT_NAME="test-endpoint")
                result = subprocess.run(["bash", str(KIT / name)], env=env,
                                        capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("endpoint written", result.stdout)
                self.assertEqual(list((self.root / "work/test/endpoints").iterdir()), [])
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pid_file.read_text()), 0)


class TunnelTests(unittest.TestCase):
    def test_two_tunnels_have_independent_sessions_and_stop_on_term(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            log = root / "ssh.jsonl"
            executable(binary / "ssh", f"#!{sys.executable}\n" + '''
import json, os, sys
with open(os.environ["TEST_SSH_LOG"], "a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
if "-O" not in sys.argv:
    print("HOST=node1\\nPORT=20000\\nJOBID=101")
''')
            env = environment()
            env.update(HOME=str(root / "home"), LEO_USER="mock", TEAM="test", POLL="0.1",
                       TEST_SSH_LOG=str(log))
            env["PATH"] = str(binary) + os.pathsep + env["PATH"]
            processes = [subprocess.Popen(["bash", str(KIT / "tunnel.sh"), name],
                         env=dict(env, LOCAL_PORT=str(8000 + i)), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
                         for i, name in enumerate(("main", "moderation"))]
            try:
                deadline = time.monotonic() + 5
                forwards = []
                while time.monotonic() < deadline:
                    entries = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
                    forwards = [entry for entry in entries if "forward" in entry]
                    if len({entry[entry.index("-S") + 1] for entry in forwards}) == 2:
                        break
                    time.sleep(0.05)
                self.assertEqual(len({entry[entry.index("-S") + 1] for entry in forwards}), 2)
                processes[0].terminate()
                processes[0].communicate(timeout=5)
                self.assertEqual(processes[0].returncode, 143)
                self.assertIsNone(processes[1].poll())
                processes[1].terminate()
                processes[1].communicate(timeout=5)
                self.assertEqual(processes[1].returncode, 143)
                self.assertEqual(list((root / "home/.ssh").glob("leo.*")), [])
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
