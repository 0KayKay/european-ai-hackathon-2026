"""Model-independent preparation, submission and HTTP lifecycle regressions."""
import copy
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
RELEASE = Path(os.environ.get('VLLM_TEST_RELEASE', str(REPO / 'serve'))).resolve()
MODELS = tuple(sorted(p.stem for p in (RELEASE / 'profiles').glob('*.json')))
sys.path.insert(0, str(RELEASE))
import inference
import node


def allocated_ports():
    source = (RELEASE / 'serve_model.sbatch').read_text()
    assignments = '\n'.join(re.findall(r'^export INFERENCE_(?:MASTER|API)_PORT=.*$', source, re.M))
    assert len(assignments.splitlines()) == 2
    script = 'for SLURM_JOB_ID in {0..9999}; do\n' + assignments + '\nprintf "%s %s\\n" "$INFERENCE_MASTER_PORT" "$INFERENCE_API_PORT"\ndone'
    return [tuple(map(int, line.split())) for line in subprocess.check_output(['bash', '-c', script]).decode().splitlines()]


class Framework(unittest.TestCase):
    def test_port_ranges_do_not_overlap_for_any_job_suffix(self):
        ports = allocated_ports()
        masters = {master for master, _ in ports}
        probes = {master + 1 for master in masters}
        apis = {api for _, api in ports}
        self.assertTrue(apis.isdisjoint(masters | probes))
        self.assertEqual(len(apis), 10000)
        self.assertTrue(all(1 <= p <= 65535 for p in masters | probes | apis))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = self.root / 'team'
        self.base.mkdir()
        (self.base / 'users').mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin) + ':' + os.environ['PATH'])
        self.command('singularity', '#!/bin/bash\nexit 0\n')
        self.command('sbatch', '#!/bin/bash\nprintf "12345\\n"\n')
        self.command('scontrol', '#!/bin/bash\nprintf "127.0.0.1\\n"\n')
        self.command('getent', '#!/bin/bash\nprintf "127.0.0.1 localhost\\n"\n')
        self.command('squeue', '#!/bin/bash\nprintf "2030-01-01T00:00:00\\n"\n')
        site = inference.load(RELEASE / 'site.example.json')
        site['account'] = 'TEST_PROJECT'
        runtime = self.base / site['runtime_paths']['vllm-backport-sm80-0.13.1']
        (runtime / 'python-overlay/tilelang').mkdir(parents=True)
        (runtime / 'python-overlay/tilelang/__init__.py').write_text('# test fixture\n')
        (runtime / 'image.sif').write_bytes(b'fake-sif')
        inference.save(runtime / 'runtime.json', {'id': 'vllm-backport-sm80-0.13.1', 'sif': 'image.sif', 'sif_size': 8, 'overlay': 'python-overlay'})
        for model in MODELS:
            path = self.base / site['checkpoints'][model]
            path.mkdir(parents=True)
            for name in ('config.json', 'tokenizer_config.json'):
                (path / name).write_text('{}')
            profile = inference.load(RELEASE / 'profiles' / (model + '.json'))
            shards = ['shard-{}.safetensors'.format(i) for i in range(profile.get('expected_shards', 2))]
            for name in shards:
                (path / name).write_bytes(b'x')
            inference.save(path / 'model.safetensors.index.json', {'weight_map': {str(i): s for i, s in enumerate(shards)}})
        inference.save(self.base / 'inference-site.json', site)

    def tearDown(self):
        self.temp.cleanup()

    def command(self, name, source):
        path = self.bin / name
        path.write_text(source)
        path.chmod(0o700)

    def prepare(self, model):
        output = subprocess.check_output([sys.executable, str(RELEASE / 'inference.py'), 'prepare', model, '--base', str(self.base)], env=self.env, stderr=subprocess.PIPE)
        return Path(output.decode().strip())

    def test_private_pinned_runs_and_runtime_reuse(self):
        runs = [self.prepare(name) for name in MODELS]
        configs = [inference.load(p / 'RUN_CONFIG.json') for p in runs]
        self.assertEqual(configs[0]['sif'], configs[1]['sif'])
        self.assertEqual(configs[0]['overlay'], configs[1]['overlay'])
        self.assertNotEqual(configs[0]['cache_path'], configs[1]['cache_path'])
        keys = [(p / '.runtime/api_key').read_text() for p in runs]
        self.assertNotEqual(keys[0], keys[1])
        self.assertEqual(len(set(keys)), len(MODELS))
        self.assertEqual(len({cfg['cache_path'] for cfg in configs}), len(MODELS))
        for run in runs:
            for path in (run, run / '.runtime', run / '.runtime/api_key'):
                self.assertEqual(path.stat().st_mode & 0o077, 0)
            for name, digest in inference.load(run / 'RELEASE_MANIFEST.json')['files'].items():
                self.assertEqual(hashlib.sha256((run / name).read_bytes()).hexdigest(), digest)

    def test_reject_bad_profiles_and_missing_assets(self):
        p = inference.load(RELEASE / 'profiles/glm53.json')
        for edit in ({'id': '../escape'}, {'engine_args': ['--api-key=secret']}, {'hooks': ['arbitrary_script']}):
            obj = copy.deepcopy(p)
            obj.update(edit)
            with self.assertRaises(ValueError):
                inference.profile_check(obj)
        p['parallelism']['tensor'] = 8
        with self.assertRaises(ValueError):
            inference.profile_check(p)
        (self.base / 'checkpoints/glm53/shard-0.safetensors').unlink()
        result = subprocess.run([sys.executable, str(RELEASE / 'inference.py'), 'prepare', 'glm53', '--base', str(self.base)], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_submission_is_explicit_and_cannot_repeat(self):
        run = self.prepare('deepseek41')
        args = [sys.executable, str(run / 'inference.py'), 'submit']
        dry = subprocess.check_output(args + ['--dry-run'], cwd=str(run), env=self.env, stderr=subprocess.PIPE).decode()
        command = json.loads(dry)
        for flag in ('--nodes=2', '--gres=gpu:4', '--mem=480GB', '--time=02:00:00', '--signal=B:TERM@90'):
            self.assertIn(flag, command)
        self.assertFalse((run / '.runtime/job_id').exists())
        subprocess.check_output(args, cwd=str(run), env=self.env, stderr=subprocess.PIPE)
        self.assertEqual((run / '.runtime/job_id').read_text().strip(), '12345')
        second = subprocess.run(args, cwd=str(run), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(second.returncode, 0)

    def test_model_settings_survive_refactor(self):
        settings = [('glm53', '4', '2', ['glm45', 'glm47']), ('deepseek41', '8', '1', ['deepseek_v41'])]
        if 'qwen235b' in MODELS:
            settings.append(('qwen235b', '4', '2', ['qwen3', 'hermes']))
        for model, tp, pp, parsers in settings:
            cfg = inference.load(self.prepare(model) / 'RUN_CONFIG.json')
            command = node.engine_command(cfg, 0, '10.0.0.1', 29500, 21001)
            self.assertEqual(command[command.index('--tensor-parallel-size') + 1], tp)
            self.assertEqual(command[command.index('--pipeline-parallel-size') + 1], pp)
            for value in parsers:
                self.assertIn(value, command)
            self.assertIn('--headless', node.engine_command(cfg, 1, '10.0.0.1', 29500, 21001))
            if model == 'deepseek41':
                self.assertIn('{"cpu_offload":true}', command)
                self.assertIn('--enable-expert-parallel', command)
                self.assertNotIn('--enable-ep-weight-filter', command)

    def test_failed_launcher_never_publishes_endpoint(self):
        self.command('srun', '#!/bin/bash\nexit 9\n')
        run = self.prepare('glm53')
        env = dict(self.env, SLURM_SUBMIT_DIR=str(run), SLURM_JOB_ID='12999', SLURM_JOB_NODELIST='node[1-2]', SLURM_CPUS_PER_TASK='32')
        result = subprocess.run(['bash', str(run / 'serve_model.sbatch')], cwd=str(run), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((run / '.runtime/endpoint.json').exists())
        self.assertIn(b'launcher exited before readiness', result.stderr)

    def test_batch_readiness_english_checks_and_term_cleanup(self):
        fixture = REPO / 'tests/fake_api.py'
        self.command('srun', '#!/bin/bash\nexec ' + shutil.which('python3') + ' ' + str(fixture) + '\n')
        for model in MODELS:
            with self.subTest(model=model):
                run = self.prepare(model)
                # Pick an unused API port in the launcher's deterministic range.
                for job in range(12300, 12900):
                    probe = socket.socket()
                    try:
                        probe.bind(('127.0.0.1', allocated_ports()[job % 10000][1]))
                        break
                    except OSError:
                        pass
                    finally:
                        probe.close()
                env = dict(self.env, SLURM_SUBMIT_DIR=str(run), SLURM_JOB_ID=str(job), SLURM_JOB_NODELIST='node[1-2]', SLURM_CPUS_PER_TASK='32')
                with (run / 'batch-test.log').open('w') as log:
                    process = subprocess.Popen(['bash', str(run / 'serve_model.sbatch')], cwd=str(run), env=env, stdout=log, stderr=log)
                    try:
                        until = time.monotonic() + 25
                        while not (run / '.runtime/endpoint.json').exists() and process.poll() is None and time.monotonic() < until:
                            time.sleep(0.1)
                        self.assertTrue((run / '.runtime/endpoint.json').exists(), (run / 'batch-test.log').read_text())
                        checks = inference.load(run / ('evidence/api-tests-' + str(job) + '.json'))
                        self.assertEqual(len(checks), 7)
                        self.assertTrue(all(v['passed'] for v in checks.values()))
                        process.send_signal(signal.SIGTERM)
                        self.assertEqual(process.wait(timeout=8), 143)
                        self.assertFalse((run / '.runtime/endpoint.json').exists())
                        pid = int((run / '.runtime/fake-server-pid').read_text())
                        with self.assertRaises(ProcessLookupError):
                            os.kill(pid, 0)
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            process.wait(timeout=8)


if __name__ == '__main__':
    unittest.main()
