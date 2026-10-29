#!/usr/bin/env python3
"""Start one lineup model inside its container: cached weights, GPU selection, then vLLM.

Weights live in the persistent team cache. A model is downloaded only while its
completion marker is missing; afterwards it starts with Hugging Face offline.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

PATTERNS = ['*.safetensors', '*.json', '*.jinja', '*.txt', '*.model', '*.tiktoken']
IGNORE = ['original/*', 'onnx/*', '*.onnx', '*.onnx_data', 'metal/*', 'imgs/*']


def marker_path(cache, spec):
    return Path(cache) / 'hf' / 'complete' / ('%s@%s.json' % (spec['repo'].replace('/', '--'), spec['revision']))


def complete(folder):
    index = folder / 'model.safetensors.index.json'
    if index.exists():
        shards = set(json.loads(index.read_text())['weight_map'].values())
        return all((folder / shard).is_file() for shard in shards)
    return any(folder.glob('*.safetensors')) or any(folder.glob('pytorch_model*.bin'))


def weights(spec, cache):
    """Return the local snapshot, downloading it once into the shared cache."""
    from huggingface_hub import HfApi, snapshot_download
    hub = str(Path(cache) / 'hf' / 'hub')
    marker = marker_path(cache, spec)
    if marker.exists():
        folder = Path(snapshot_download(spec['repo'], revision=spec['revision'], cache_dir=hub,
                                        local_files_only=True, allow_patterns=PATTERNS, ignore_patterns=IGNORE))
        if complete(folder):
            print('cache hit: %s@%s -> %s' % (spec['repo'], spec['revision'], folder), flush=True)
            return folder
        print('cache marker present but snapshot incomplete; downloading again', flush=True)
    files = HfApi().list_repo_files(spec['repo'], revision=spec['revision'])
    patterns = list(PATTERNS)
    if not any(f.endswith('.safetensors') for f in files):
        patterns.append('pytorch_model*.bin')
    print('downloading %s@%s into %s' % (spec['repo'], spec['revision'], hub), flush=True)
    started = time.monotonic()
    folder = Path(snapshot_download(spec['repo'], revision=spec['revision'], cache_dir=hub, max_workers=16,
                                    allow_patterns=patterns, ignore_patterns=IGNORE))
    if not complete(folder):
        raise RuntimeError('Downloaded snapshot is missing weight shards: %s' % folder)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({'repo': spec['repo'], 'revision': spec['revision'], 'commit': folder.name,
                                  'completed_at_utc': datetime.now(timezone.utc).isoformat(),
                                  'seconds': round(time.monotonic() - started)}, indent=2) + '\n')
    print('downloaded in %ds, commit %s' % (time.monotonic() - started, folder.name), flush=True)
    return folder


def wait_for(plan, spec, run):
    """Models sharing a GPU start one after another; a failed predecessor does not block."""
    ports = {m['id']: m['port'] for m in plan['models']}
    for dep in spec['after']:
        print('waiting for %s before starting on shared GPUs' % dep, flush=True)
        while not (Path(run) / 'state' / (dep + '.exited')).exists():
            try:
                with urllib.request.urlopen('http://127.0.0.1:%d/health' % ports[dep], timeout=5) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            time.sleep(10)


def caches(spec, image, cache, scratch, persist):
    root = Path(cache) / 'compile' / image / spec['id'] if persist else Path(scratch) / 'compile' / spec['id']
    env = {'HF_HOME': str(Path(cache) / 'hf'), 'HF_HUB_CACHE': str(Path(cache) / 'hf' / 'hub'),
           'TMPDIR': str(Path(scratch) / 'tmp'), 'XDG_CACHE_HOME': str(Path(scratch) / 'xdg-cache' / spec['id']),
           'VLLM_CACHE_ROOT': str(root / 'vllm'), 'TRITON_CACHE_DIR': str(root / 'triton'),
           'TORCHINDUCTOR_CACHE_DIR': str(root / 'inductor'), 'CUDA_CACHE_PATH': str(root / 'cuda'),
           'FLASHINFER_WORKSPACE_BASE': str(root / 'flashinfer'), 'DG_JIT_CACHE_DIR': str(root / 'deepgemm'),
           'TVM_FFI_CACHE_DIR': str(root / 'tvm'), 'TILELANG_CACHE_DIR': str(root / 'tilelang'),
           'TORCH_HOME': str(Path(scratch) / 'torch')}
    for path in env.values():
        Path(path).mkdir(parents=True, exist_ok=True)
    return env


def command(spec, folder):
    names = [spec['name']] + spec.get('aliases', [])
    return [sys.executable, '-m', 'vllm.entrypoints.openai.api_server', '--model', str(folder),
            '--served-model-name', *names, '--host', '127.0.0.1', '--port', str(spec['port']),
            '--tensor-parallel-size', str(len(spec['gpus'])), '--pipeline-parallel-size', '1',
            '--gpu-memory-utilization', str(spec['memory'])] + spec['args']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--cache', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--scratch', required=True)
    parser.add_argument('--prefetch', action='store_true', help='Only make sure the weights are cached')
    parser.add_argument('--dry-run', action='store_true', help='Print the vLLM command without downloading or starting')
    args = parser.parse_args()
    plan = json.loads(Path(args.plan).read_text())
    spec = next(m for m in plan['models'] if m['id'] == args.model)
    persist = os.environ.get('PERSIST_COMPILE_CACHE', '1') == '1'
    env = caches(spec, spec['image'], args.cache, args.scratch, persist) if not args.dry_run else {}
    os.environ.update(env)
    if args.dry_run:
        print(json.dumps(command(spec, '/cache/' + spec['repo'])))
        return
    folder = weights(spec, args.cache)
    if args.prefetch:
        return
    visible = [v for v in os.environ.get('CUDA_VISIBLE_DEVICES', '').split(',') if v]
    visible = visible or [str(i) for i in range(plan['node_gpus'])]
    if len(visible) < plan['node_gpus']:
        raise SystemExit('Step sees GPUs %r, lineup expects %d' % (visible, plan['node_gpus']))
    wait_for(plan, spec, args.run)
    key = (Path(args.run) / 'backend.key').read_text().strip()
    os.environ.update({'CUDA_VISIBLE_DEVICES': ','.join(visible[i] for i in spec['gpus']),
                       'VLLM_API_KEY': key, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                       'VLLM_WORKER_MULTIPROC_METHOD': 'spawn', 'VLLM_ENGINE_READY_TIMEOUT_S': '5400',
                       'VLLM_NO_USAGE_STATS': '1', 'DO_NOT_TRACK': '1'})
    os.environ.update(spec.get('env', {}))
    argv = command(spec, folder)
    print('GPUs %s: %s' % (os.environ['CUDA_VISIBLE_DEVICES'], ' '.join(argv[1:])), flush=True)
    os.execv(argv[0], argv)


if __name__ == '__main__':
    main()
