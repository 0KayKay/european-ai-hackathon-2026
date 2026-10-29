"""Local lifecycle contracts with simulated Slurm; never model-execution evidence."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'serve'))
import common
import manage

spec = importlib.util.spec_from_file_location('b300_cli', ROOT / 'cli.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class LifecycleContracts(unittest.TestCase):
    def test_concurrent_session_publication_never_exposes_partial_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'endpoint.json'
            value = json.dumps({'model': 'fixture', 'payload': 'x' * 10000})
            cli.publish_private(path, value)
            def concurrent_caller(index):
                for _ in range(25):
                    cli.publish_private(path, value)
                    self.assertEqual(json.loads(path.read_text())['payload'], 'x' * 10000)
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(concurrent_caller, range(8)))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_stale_or_foreign_ready_file_does_not_make_a_session_live(self):
        state = {'ready': True, 'phase': 'ready', 'job_id': '123'}
        endpoint = {'job_id': '123', 'owner_uid': 99}
        self.assertFalse(common.ready_session('123', state, endpoint, 99, {}))
        live = {'123': {'state': 'PENDING', 'uid': 99}}
        self.assertFalse(common.ready_session('123', state, endpoint, 99, live))
        live['123']['state'] = 'RUNNING'
        self.assertTrue(common.ready_session('123', state, endpoint, 99, live))
        live['123']['uid'] = 100
        self.assertFalse(common.ready_session('123', state, endpoint, 99, live))

    def test_gpu_and_time_requests_are_explicit(self):
        site = {'account': 'project', 'partition': 'hackathon', 'qos': 'team'}
        profile = {'gpus': 5, 'cpus': 64, 'memory': '960G'}
        command = manage.submission_command(profile, site, Path('/tmp/fixture-run'), 'b300-fixture', '04:00:00')
        self.assertIn('--gpus=5', command)
        self.assertIn('--ntasks=1', command)
        self.assertIn('--time=04:00:00', command)
        for invalid in ['00:00:00', '04:00:01', '99:00:00', '30 minutes']:
            with self.assertRaises(ValueError):
                common.time_seconds(invalid)

    def test_existing_owned_job_prevents_duplicate_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            common.save(base / 'b300-site.json', {'account': 'project', 'partition': 'hackathon', 'qos': 'team'})
            path = common.private(manage.mine(base) / 'runs/safety-fixture')
            common.save(path / 'RUN_CONFIG.json', {'profile': 'safety'})
            common.save(path / 'submission.json', {'job_id': '123', 'phase': 'submitted'})
            with patch.object(manage, 'jobs', return_value={'123': {'state': 'PENDING'}}), \
                    patch.object(manage, 'execute') as execute:
                result = manage.start(base, {'profile': 'safety', 'mode': 'serve'})
                self.assertEqual(result['phase'], 'already_active')
                execute.assert_not_called()

    def test_submission_timeout_is_uncertain_and_blocks_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            common.save(base / 'b300-site.json', {'account': 'project', 'partition': 'hackathon', 'qos': 'team'})
            with patch.object(manage, 'jobs', return_value={}), \
                    patch.object(manage, 'execute', side_effect=TimeoutError('fixture lost reply')):
                with self.assertRaises(TimeoutError):
                    manage.start(base, {'profile': 'safety', 'mode': 'serve'})
            receipt = next((manage.mine(base) / 'runs').glob('*/submission.json'))
            self.assertEqual(common.load(receipt)['phase'], 'submission_uncertain')
            with patch.object(manage, 'jobs', return_value={}), patch.object(manage, 'execute') as execute:
                with self.assertRaisesRegex(RuntimeError, 'Uncertain submission'):
                    manage.start(base, {'profile': 'safety', 'mode': 'serve'})
                execute.assert_not_called()

    def test_cancellation_requires_matching_owned_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = common.private(manage.mine(base) / 'runs/safety-fixture')
            common.save(path / 'RUN_CONFIG.json', {'profile': 'safety'})
            common.save(path / 'submission.json', {'job_id': '123', 'job_name': 'b300-fixture'})
            with patch.object(manage, 'jobs', return_value={
                    '123': {'uid': os.getuid() + 1, 'name': 'other-job'}}), \
                    patch.object(manage, 'execute') as execute:
                with self.assertRaisesRegex(RuntimeError, 'Refusing to cancel'):
                    manage.down(base, '123')
                execute.assert_not_called()

    def test_expiry_during_fetch_never_returns_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = common.private(manage.mine(base) / 'runs/safety-fixture')
            common.save(path / 'RUN_CONFIG.json', {'profile': 'safety'})
            common.save(path / 'submission.json', {'job_id': '123'})
            common.save(path / 'state.json', {'job_id': '123', 'phase': 'ready', 'ready': True})
            common.save(path / 'endpoint.json', {'job_id': '123', 'owner_uid': os.getuid(), 'models': {'safety': {}}})
            (path / 'safety.api-key').write_text('fixture-key')
            with patch.object(manage, 'jobs', side_effect=[
                    {'123': {'state': 'RUNNING', 'uid': os.getuid()}}, {}]):
                with self.assertRaisesRegex(RuntimeError, 'ended while'):
                    manage.fetch(base, '123')

    def test_download_release_contains_no_private_files(self):
        digest, data = cli.archive_release()
        self.assertEqual(len(digest), 20)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            names = archive.getnames()
        self.assertIn('serve/server.py', names)
        self.assertFalse(any('.runtime' in name or 'api-key' in name for name in names))

    def test_long_context_glm_runs_as_the_glm_kind(self):
        import server
        models = common.load(common.ROOT / 'models.json')
        profiles = common.load(common.ROOT / 'profiles.json')
        self.assertEqual(profiles['glm53']['models'], ['glm'])
        self.assertEqual(profiles['glm53-long']['models'], ['glm-long'])
        glm, long = models['glm'], models['glm-long']
        # Same checkpoint, API name and laptop port: key files, forwards, clients and the gateway alias are unchanged.
        self.assertEqual(long['kind'], 'glm')
        for field in ('repo', 'revision', 'weight_bytes', 'shard_count', 'metadata_sha256', 'served_model',
                      'tp', 'port', 'local_port', 'client'):
            self.assertEqual(long[field], glm[field], field)
        command = server.engine(long, long['kind'], Path('/fixture'), '10.0.0.1', 8000)
        self.assertIn('--distributed-executor-backend', command)
        value = lambda args, flag: args[args.index(flag) + 1]
        self.assertEqual(value(command, '--tensor-parallel-size'), '8')
        self.assertEqual([value(command, f) for f in ('--max-model-len', '--max-num-seqs', '--max-num-batched-tokens')],
                         ['262144', '32', '16384'])
        self.assertEqual(value(glm['engine_args'], '--max-model-len'), '65536')
        differ = {'--max-model-len', '--max-num-seqs'}
        strip = lambda args: [a for i, a in enumerate(args) if a not in differ and (i == 0 or args[i - 1] not in differ)]
        self.assertEqual(strip(long['engine_args']), strip(glm['engine_args']))

    def test_forwarding_is_private_and_keeps_ports_distinct(self):
        endpoint = {'host_ip': '10.1.2.3', 'models': {
            'ultra': {'local_port': 18085, 'port': 30001},
            'safety': {'local_port': 18086, 'port': 30002}}}
        self.assertEqual(len(cli.forward_specs(endpoint, {})), 2)
        with self.assertRaises(ValueError):
            cli.forward_specs(endpoint, {'ultra': 19000, 'safety': 19000})
        endpoint['host_ip'] = '127.0.0.1'
        with self.assertRaises(ValueError):
            cli.forward_specs(endpoint, {})


if __name__ == '__main__':
    unittest.main()
