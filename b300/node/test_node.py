"""Local checks for the full-node lineup: python3 -m unittest discover -s b300/node -p 'test_*.py' -v"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import launch  # noqa: E402
import node  # noqa: E402

LINEUP = node.load(HERE / 'lineup.json')


class PlanTest(unittest.TestCase):
    def test_default_lineup_fits_and_orders_shared_gpus(self):
        plan = node.make_plan(LINEUP, gateway_port=18080)
        after = {m['id']: m['after'] for m in plan['models']}
        self.assertEqual(after['safeguard'], ['safety'])
        self.assertEqual(after['vision'], ['whisper'])
        self.assertEqual(after['glm'], [])
        self.assertEqual(sorted({g for m in plan['models'] for g in m['gpus']}), list(range(8)))

    def test_variants_are_exclusive(self):
        node.make_plan(LINEUP, ['glm-tp8'])
        node.make_plan(LINEUP, ['glm-fast', 'safety', 'safeguard'])
        for wanted in (['glm', 'glm-fast'], ['glm-tp8', 'safety'], ['glm', 'missing']):
            with self.assertRaises(ValueError):
                node.make_plan(LINEUP, wanted)

    def test_rejects_gateway_port_and_bad_gpu(self):
        with self.assertRaises(ValueError):
            node.make_plan(LINEUP, ['glm'], gateway_port=8101)
        lineup = copy.deepcopy(LINEUP)
        lineup['models'][0]['gpus'] = [0, 8]
        with self.assertRaises(ValueError):
            node.make_plan(lineup, ['glm'])


class GatewayTest(unittest.TestCase):
    def test_config_hashes_client_key_and_routes_paths(self):
        plan = node.make_plan(LINEUP)
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / 'key'
            key.write_text('client-secret\n')
            config = node.gateway_config(plan, 18080, key, Path(folder) / 'backend.key')
        self.assertNotIn('client-secret', json.dumps(config))
        self.assertEqual(config['llm']['policies']['apiKey']['mode'], 'strict')
        names = {m['name'] for m in config['llm']['models']}
        self.assertIn('glm-5.3-flash-b300', names)
        routes = {r['name']: r for r in config['routes']}
        self.assertEqual(routes['whisper']['backends'], [{'host': '127.0.0.1:8107'}])
        self.assertIn({'path': {'pathPrefix': '/v2/rerank'}}, routes['rerank']['matches'])
        schema = os.environ.get('AGENTGATEWAY_SCHEMA')
        if schema:  # agentgateway's schema/config.json for the pinned release
            import jsonschema
            jsonschema.validate(config, json.loads(Path(schema).read_text()))


class WeightsTest(unittest.TestCase):
    def test_downloads_once_then_uses_cache(self):
        calls = []
        with tempfile.TemporaryDirectory() as cache:
            snapshot = Path(cache) / 'hf' / 'hub' / 'snap' / 'abc123'

            def snapshot_download(repo, revision, cache_dir, local_files_only=False, **kwargs):
                calls.append(local_files_only)
                snapshot.mkdir(parents=True, exist_ok=True)
                (snapshot / 'model.safetensors.index.json').write_text('{"weight_map": {"a": "m-1.safetensors"}}')
                (snapshot / 'm-1.safetensors').write_text('x')
                return str(snapshot)

            hub = types.SimpleNamespace(snapshot_download=snapshot_download,
                                        HfApi=lambda: types.SimpleNamespace(list_repo_files=lambda *a, **k: ['m-1.safetensors']))
            sys.modules['huggingface_hub'] = hub
            try:
                spec = {'repo': 'org/model', 'revision': 'main'}
                self.assertEqual(launch.weights(spec, cache), snapshot)
                self.assertEqual(launch.weights(spec, cache), snapshot)
                self.assertEqual(json.loads(launch.marker_path(cache, spec).read_text())['commit'], 'abc123')
            finally:
                del sys.modules['huggingface_hub']
        self.assertEqual(calls, [False, True])


if __name__ == '__main__':
    unittest.main()
