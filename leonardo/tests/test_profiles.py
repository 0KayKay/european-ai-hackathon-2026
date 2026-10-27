"""cli.py profiles: listing, and adding a fine-tune's profile from a (stand-in) Hugging Face Hub.

Runs on a copy of leonardo/ and infrastructure/, so the repository stays untouched.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

REPO = Path(__file__).resolve().parents[2]
BASE_SHA = 'c1899de289a04d12100db370d81485cdf75e47ca'  # qwen06b's pinned revision
TUNE_SHA = '0123456789abcdef0123456789abcdef01234567'
QWEN3 = {'architectures': ['Qwen3ForCausalLM'], 'model_type': 'qwen3'}
FULL = ['config.json', 'tokenizer_config.json', 'generation_config.json']
# repo -> (sha, files with sizes, config.json); the shapes of the Hub's /api/models/<repo>/revision/<rev>
MODELS = {
    'Qwen/Qwen3-0.6B': (BASE_SHA, {'model.safetensors': 1503300328, **dict.fromkeys(FULL)}, QWEN3),
    'team/qwen06b-tune': (TUNE_SHA, {'model-00001-of-00002.safetensors': 800000000,
                                     'model-00002-of-00002.safetensors': 700000000, **dict.fromkeys(FULL)}, QWEN3),
    'team/llama-tune': (TUNE_SHA, {'model.safetensors': 1, **dict.fromkeys(FULL)},
                        {'architectures': ['LlamaForCausalLM'], 'model_type': 'llama'}),
    'team/qwen06b-lora': (TUNE_SHA, {'adapter_config.json': 1, 'adapter_model.safetensors': 1}, None),
    'team/qwen3-8b-tune': (TUNE_SHA, {'model.safetensors': 16400000000, **dict.fromkeys(FULL)}, QWEN3),
    'team/qwen3-too-big': (TUNE_SHA, {'model.safetensors': 60000000000, **dict.fromkeys(FULL)}, QWEN3),
    'team/qwen3-awq': (TUNE_SHA, {'model.safetensors': 1, **dict.fromkeys(FULL)},
                       dict(QWEN3, quantization_config={'quant_method': 'awq'})),
    'team/no-tokenizer': (TUNE_SHA, {'model.safetensors': 1, 'config.json': 1}, QWEN3),
    'team/nested': (TUNE_SHA, {'README.md': 1, 'draft/config.json': 1, 'draft/model.safetensors': 1}, None),
    # Derives the served name qwen3-0.6b-leonardo, which qwen06b already uses, and has another architecture.
    'other/Qwen3-0.6B': (TUNE_SHA, {'model.safetensors': 1, **dict.fromkeys(FULL)},
                         {'architectures': ['Qwen3MoeForCausalLM'], 'model_type': 'qwen3_moe'}),
}


class Hub(HTTPServer):
    def __init__(self):
        self.seen = []
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                hub.seen.append((self.path, self.headers.get('Authorization')))
                for repo, (sha, files, config) in MODELS.items():
                    if repo == 'team/gated':
                        continue
                    if self.path.startswith('/api/models/{}/revision/'.format(repo)):
                        siblings = [{'rfilename': n, **({'size': s} if s else {})} for n, s in files.items()]
                        return self.answer(200, {'id': repo, 'sha': sha, 'siblings': siblings})
                    if self.path == '/{}/resolve/{}/config.json'.format(repo, sha) and config:
                        return self.answer(200, config)
                if self.path.startswith('/api/models/team/gated/'):
                    return self.answer(401, {'error': 'gated'})
                self.answer(404, {'error': 'not found'})

            def answer(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        super().__init__(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


class GatewayRendering(unittest.TestCase):
    def test_routes_match_base_without_removed_guardrail_webhook(self):
        result = subprocess.run(
            [sys.executable, str(REPO / 'leonardo/tools/render_agentgateway.py'), '--check'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (REPO / 'infrastructure/leonardo/agentgateway.yaml').read_text()
        self.assertNotIn('/guardrails/request', config)
        self.assertIn('/api/auth/authorize-internal', config)
        self.assertIn('file: /var/run/leonardo-router/token', config)


class Profiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for part in ('leonardo/cli.py', 'leonardo/serve', 'leonardo/tools', 'leonardo/validation',
                     'leonardo/install', 'leonardo/diagnostics', 'infrastructure/base', 'infrastructure/leonardo'):
            source, target = REPO / part, self.root / part
            if source.is_dir():
                shutil.copytree(str(source), str(target), ignore=shutil.ignore_patterns('__pycache__'))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(source), str(target))
        self.hub = Hub()
        self.env = dict(os.environ, HF_ENDPOINT='http://127.0.0.1:{}'.format(self.hub.server_port))
        self.env.pop('HF_TOKEN', None)
        self.profiles = self.root / 'leonardo/serve/profiles'

    def tearDown(self):
        self.hub.shutdown()
        self.hub.server_close()
        self.temp.cleanup()

    def cli(self, *words, env=None):
        return subprocess.run([sys.executable, str(self.root / 'leonardo/cli.py'), 'profiles'] + list(words),
                              env=env or self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    def test_add_a_fine_tune(self):
        result = self.cli('add', 'qwen06b-tune', '--from', 'qwen06b', '--model', 'team/qwen06b-tune')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('WARNING', result.stdout)
        base = json.loads((self.profiles / 'qwen06b.json').read_text())
        new = json.loads((self.profiles / 'qwen06b-tune.json').read_text())
        self.assertEqual((new['id'], new['model_id'], new['revision'], new['checkpoint'], new['served_model']),
                         ('qwen06b-tune', 'team/qwen06b-tune', TUNE_SHA, 'qwen06b-tune', 'qwen06b-tune-leonardo'))
        self.assertEqual(new['derived_from'], 'qwen06b')
        for same in ('runtime', 'resources', 'parallelism', 'engine_args', 'checks'):
            self.assertEqual(new.get(same), base.get(same), same)
        others = [json.loads(p.read_text())['client']['local_port'] for p in self.profiles.glob('*.json') if p.stem != new['id']]
        self.assertNotIn(new['client']['local_port'], others)
        self.assertIn('- name: qwen06b-tune-leonardo', (self.root / 'infrastructure/leonardo/agentgateway.yaml').read_text())
        # Adding a profile leaves the release manifest current, and the upload includes the profile.
        check = subprocess.run([sys.executable, str(self.root / 'leonardo/tools/update_manifest.py'), '--check'],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(check.returncode, 0, check.stdout)
        files = subprocess.run([sys.executable, '-c', 'import cli; print("\\n".join(cli.release_files()))'],
                               cwd=str(self.root / 'leonardo'), stdout=subprocess.PIPE, universal_newlines=True).stdout
        self.assertIn('serve/profiles/qwen06b-tune.json', files.split())
        listing = self.cli().stdout
        self.assertRegex(listing, r'qwen06b-tune .* not validated yet, from qwen06b')
        self.assertRegex(listing, r'qwen06b .* validated \d{4}-\d\d-\d\d \(job \d+\)')  # its newest recorded run
        self.assertIn('already exists', self.cli('add', 'qwen06b-tune', '--from', 'qwen06b', '--model',
                                                 'team/qwen06b-tune').stderr)

    def test_refuses_what_the_base_settings_do_not_fit(self):
        result = self.cli('add', 'llama', '--from', 'qwen06b', '--model', 'team/llama-tune')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LlamaForCausalLM', result.stderr)
        self.assertFalse((self.profiles / 'llama.json').exists())
        forced = self.cli('add', 'llama', '--from', 'qwen06b', '--model', 'team/llama-tune', '--force')
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertIn('WARNING: architecture', forced.stdout)
        self.assertIn('LoRA adapter', self.cli('add', 'lora', '--from', 'qwen06b', '--model', 'team/qwen06b-lora').stderr)
        self.assertIn('tokenizer_config.json', self.cli('add', 'x', '--from', 'qwen06b', '--model', 'team/no-tokenizer').stderr)
        nested = self.cli('add', 'x', '--from', 'qwen06b', '--model', 'team/nested').stderr
        self.assertIn('It holds: README.md. Folders: draft/.', nested)
        self.assertIn('A config.json sits in draft/config.json', nested)
        # The architecture is refused first, even when the derived served name is also taken.
        clash = self.cli('add', 'moe', '--from', 'qwen06b', '--model', 'other/Qwen3-0.6B').stderr
        self.assertIn('Qwen3MoeForCausalLM', clash)
        self.assertNotIn('taken', clash)
        # Size against the base profile's GPUs (1 x 64 GB), not against the base's weights.
        self.assertNotIn('WARNING', self.cli('add', 'eightb', '--from', 'qwen06b', '--model', 'team/qwen3-8b-tune').stdout)
        self.assertIn('room for the KV cache', self.cli('add', 'big', '--from', 'qwen06b', '--model', 'team/qwen3-too-big').stdout)
        self.assertIn('quantization awq differs', self.cli('add', 'awq', '--from', 'qwen06b', '--model', 'team/qwen3-awq').stdout)
        self.assertIn('taken', self.cli('add', 'other', '--from', 'qwen06b', '--model', 'team/qwen06b-tune',
                                        '--served-name', 'qwen3-0.6b-leonardo').stderr)
        self.assertIn('No base profile', self.cli('add', 'y', '--from', 'nope', '--model', 'team/qwen06b-tune').stderr)
        self.assertIn('org/name', self.cli('add', 'z', '--from', 'qwen06b', '--model', 'not a repo').stderr)

    def test_gated_models_use_hf_token(self):
        result = self.cli('add', 'gated', '--from', 'qwen06b', '--model', 'team/gated')
        self.assertIn('HF_TOKEN', result.stderr)
        self.cli('add', 'qwen06b-tune', '--from', 'qwen06b', '--model', 'team/qwen06b-tune',
                 env=dict(self.env, HF_TOKEN='hf_test'))
        self.assertIn(('/api/models/team/qwen06b-tune/revision/main?blobs=true', 'Bearer hf_test'), self.hub.seen)


if __name__ == '__main__':
    unittest.main()
