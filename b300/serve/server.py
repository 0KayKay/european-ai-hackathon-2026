"""Bounded B300 preparation and authenticated serving inside a Slurm allocation."""
import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from common import ROOT, load, save, verify_release
from validation import request, validate


def now():
    return datetime.now(timezone.utc).isoformat()


def stop(child):
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=40)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=15)


def checkpoint(spec, scratch):
    from huggingface_hub import HfApi, snapshot_download
    info = HfApi().model_info(spec['repo'], revision=spec['revision'], files_metadata=True)
    if info.sha != spec['revision']:
        raise ValueError('Resolved checkpoint revision mismatch')
    shards = {f.rfilename: f.size for f in info.siblings if f.rfilename.endswith('.safetensors')}
    if len(shards) != spec.get('shard_count', len(spec.get('shards', {}))):
        raise ValueError('Checkpoint shard count mismatch')
    if sum(shards.values()) != spec['weight_bytes'] or ('shards' in spec and shards != spec['shards']):
        raise ValueError('Checkpoint shard sizes mismatch')
    cached = scratch / 'hf' / ('models--' + spec['repo'].replace('/', '--')) / 'snapshots' / spec['revision']
    missing_bytes = sum(size for name, size in shards.items()
                        if not (cached / name).exists() or (cached / name).stat().st_size != size)
    filesystem = os.statvfs(scratch)
    if filesystem.f_bavail * filesystem.f_frsize < missing_bytes + 80_000_000_000:
        raise RuntimeError('Node scratch needs remaining weights plus 80 GB working space')
    folder = Path(snapshot_download(spec['repo'], revision=spec['revision'],
        cache_dir=str(scratch / 'hf'), max_workers=8,
        allow_patterns=['*.safetensors', '*.json', '*.jinja', '*.txt', '*.model', '*.tiktoken']))
    if not all((folder / name).stat().st_size == size for name, size in shards.items()):
        raise ValueError('Downloaded weight size mismatch')
    for name, digest in spec.get('metadata_sha256', {}).items():
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Checkpoint metadata mismatch')
    return folder


def engine(spec, kind, folder, address, port):
    args = [sys.executable, '-m', 'vllm.entrypoints.openai.api_server', '--model', str(folder),
            '--served-model-name', spec['served_model'], '--host', address, '--port', str(port),
            '--tensor-parallel-size', str(spec['tp']), '--pipeline-parallel-size', '1']
    if kind == 'glm':
        return args + ['--distributed-executor-backend', 'mp'] + spec['engine_args']
    if kind == 'safeguard':
        return args + ['--dtype', 'bfloat16', '--max-model-len', '8192',
            '--max-num-seqs', '1', '--gpu-memory-utilization', '0.20',
            '--enforce-eager', '--reasoning-parser', 'openai_gptoss', '--no-enable-log-requests']
    args += ['--dtype', 'bfloat16', '--max-model-len', str(spec['max_model_len']),
             '--max-num-seqs', '4', '--gpu-memory-utilization', '0.85', '--no-enable-log-requests']
    if kind == 'ultra':
        args += ['--enable-auto-tool-choice', '--tool-call-parser', 'qwen3_coder',
                 '--reasoning-parser', 'nemotron_v3', '--enable-expert-parallel']
    return args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--host-scratch', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    cfg = load(args.run / 'RUN_CONFIG.json')
    state = {'phase': 'preparing', 'ready': False, 'job_id': os.environ['SLURM_JOB_ID'],
             'profile': cfg['profile'], 'mode': cfg['mode'], 'started_at_utc': now()}
    children, handles, keys = [], [], {}
    endpoint = args.run / 'endpoint.json'

    def update(**fields):
        state.update(fields, updated_at_utc=now())
        save(args.run / 'state.json', state)
        print(json.dumps({k: state[k] for k in ('phase', 'job_id', 'updated_at_utc')}), flush=True)

    def interrupt(signum, frame):
        raise InterruptedError('Owned Slurm allocation is stopping')

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    try:
        verify_release()
        profile = load(ROOT / 'profiles.json')[cfg['profile']]
        models = load(ROOT / 'models.json')
        args.scratch.mkdir(parents=True, exist_ok=True)
        for name, suffix in {'TMPDIR': 'tmp', 'HF_HOME': 'hf-home', 'XDG_RUNTIME_DIR': 'runtime',
            'XDG_CONFIG_HOME': 'config', 'XDG_CACHE_HOME': 'cache', 'XDG_DATA_HOME': 'data',
            'TRITON_CACHE_DIR': 'cache/triton', 'TORCHINDUCTOR_CACHE_DIR': 'cache/inductor',
            'CUDA_CACHE_PATH': 'cache/cuda', 'TORCH_HOME': 'cache/torch',
            'TVM_FFI_CACHE_DIR': 'cache/tvm', 'TILELANG_CACHE_DIR': 'cache/tilelang'}.items():
            path = args.scratch / suffix
            path.mkdir(parents=True, exist_ok=True)
            os.environ[name] = str(path)
        import torch
        count = torch.cuda.device_count()
        if count != profile['gpus'] or not all('B300' in torch.cuda.get_device_name(i) for i in range(count)):
            raise RuntimeError('Allocated CUDA devices do not match the B300 profile')
        ids = os.environ.get('CUDA_VISIBLE_DEVICES', ','.join(map(str, range(count)))).split(',')
        if len(ids) != count:
            raise RuntimeError('CUDA device mapping does not match the allocation')
        folders = {}
        for kind in profile['models']:
            update(phase='downloading_' + kind)
            folders[kind] = checkpoint(models[kind], args.scratch)
        host = socket.gethostname()
        save(args.run / 'cache.json', {'job_id': state['job_id'], 'owner_uid': cfg['owner_uid'],
            'node': os.environ.get('SLURMD_NODENAME', host), 'image': cfg['image'],
            'scratch': args.host_scratch, 'temporary': True, 'prepared_at_utc': now(),
            'verification': 'Pinned revisions, shard sets/sizes and configured metadata hashes; not full weight rehash'})
        if cfg['mode'] == 'install':
            update(phase='installed', finished_at_utc=now())
            return
        if profile['image'] == 'nemotron':
            from cubin_overlay import prepare
            update(phase='preflighting_writable_cubins')
            os.environ.update(prepare(args.scratch, args.run))
        address = socket.gethostbyname(host)
        if not ipaddress.ip_address(address).is_private or ipaddress.ip_address(address).is_loopback:
            raise RuntimeError('Expected a private compute-node address')
        published, validations = {}, {}
        offset, device_map = 0, {}
        for kind in profile['models']:
            device_map[kind] = ids[offset:offset + models[kind]['tp']]
            offset += models[kind]['tp']
        # Match the tested pair: safety on the fifth GPU, Ultra on the first four.
        for name in sorted(profile['models'], key=lambda k: 0 if k == 'safety' else 1):
            spec = models[name]
            # A variant (glm-long) runs as its family's kind: same engine path, checks, key file and endpoint entry.
            kind = spec.get('kind', name)
            key = secrets.token_urlsafe(36)
            keys[kind] = key
            keyfile = args.run / (kind + '.api-key')
            keyfile.write_text(key + '\n')
            keyfile.chmod(0o600)
            with socket.socket() as probe:
                try:
                    probe.bind((address, spec['port']))
                except OSError:
                    probe.bind((address, 0))
                port = probe.getsockname()[1]
            selected = device_map[name]
            environment = dict(os.environ, CUDA_VISIBLE_DEVICES=','.join(selected), VLLM_API_KEY=key,
                HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', VLLM_WORKER_MULTIPROC_METHOD='spawn',
                VLLM_CACHE_ROOT=str(args.scratch / ('vllm-' + kind)),
                FLASHINFER_WORKSPACE_BASE=str(args.scratch / ('flashinfer-' + kind)))
            command = engine(spec, kind, folders[name], address, port)
            handle = (args.run / (kind + '.server.log')).open('w')
            handles.append(handle)
            update(phase='loading_' + kind)
            child = subprocess.Popen(command, env=environment, stdout=handle,
                stderr=subprocess.STDOUT, start_new_session=True)
            children.append(child)
            base = 'http://%s:%s/v1' % (address, port)
            deadline = time.monotonic() + 5400
            while True:
                if child.poll() is not None:
                    raise RuntimeError(kind + ' server exited before readiness; inspect its log')
                try:
                    with request(base + '/models', key) as response:
                        catalog = json.load(response)
                    if spec['served_model'] in [m['id'] for m in catalog['data']]:
                        break
                except (OSError, ValueError):
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError(kind + ' startup exceeded 90 minutes')
                time.sleep(5)
            update(phase='validating_' + kind)
            if kind == 'glm':
                # Preserve the original seven-check GLM suite and its low-reasoning template.
                result = args.run / 'glm-validation.json'
                subprocess.run([sys.executable, str(ROOT / 'vendor/test_endpoint.py'),
                    '--base-url', base[:-3], '--api-key-file', str(keyfile), '--profile',
                    str(ROOT / 'glm-client-profile.json'), '--output', str(result)], check=True, timeout=2400)
                checks = load(result)
                if len(checks) != 7 or not all(c['passed'] for c in checks.values()):
                    raise RuntimeError('GLM acceptance failed')
                validations[kind] = {'passed': True, 'checks': checks}
            else:
                validations[kind] = validate(base, key, spec['served_model'], kind)
                if not validations[kind]['passed']:
                    raise RuntimeError(kind + ' acceptance failed')
            save(args.run / 'validation.json', validations)
            published[kind] = {'port': port, 'model': spec['served_model'],
                'base_url': base, 'local_port': spec['local_port'], 'revision': spec['revision']}
        save(endpoint, {'job_id': state['job_id'], 'owner_uid': cfg['owner_uid'],
                        'host_ip': address, 'ready_at_utc': now(), 'models': published})
        update(phase='ready', ready=True, acceptance_checks_passed=True)
        failures = 0
        while True:
            time.sleep(30)
            if any(c.poll() is not None for c in children):
                raise RuntimeError('An owned model server exited')
            try:
                for kind, entry in published.items():
                    with request(entry['base_url'] + '/models', keys[kind]) as response:
                        json.load(response)
                failures = 0
            except OSError:
                failures += 1
                if failures >= 3:
                    raise RuntimeError('Three consecutive health checks failed')
    except InterruptedError:
        update(phase='stopping', ready=False)
    except Exception as error:
        update(phase='failed', ready=False, error_type=type(error).__name__)
        raise
    finally:
        endpoint.unlink(missing_ok=True)
        for child in reversed(children):
            stop(child)
        for kind in keys:
            (args.run / (kind + '.api-key')).unlink(missing_ok=True)
        for handle in handles:
            handle.close()
        update(phase=state['phase'] if state['phase'] in ('installed', 'failed') else 'stopped',
               ready=False, owned_servers_stopped=all(c.poll() is not None for c in children),
               session_keys_removed=True, finished_at_utc=now())


if __name__ == '__main__':
    main()
