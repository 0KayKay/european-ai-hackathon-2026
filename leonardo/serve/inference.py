"""Shared, standard-library-only deployment and client CLI (Python 3.6+)."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads(Path(path).read_text())


def save(path, obj):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as handle:
        json.dump(obj, handle, indent=2)
        handle.write('\n')
    os.replace(str(temporary), str(path))


def identifier(value):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', value):
        raise ValueError('Invalid identifier: ' + value)
    return value


def profile_check(profile):
    if profile['schema_version'] != 1:
        raise ValueError('Unsupported profile schema')
    for name in ('id', 'checkpoint', 'runtime', 'served_model'):
        identifier(profile[name])
    resources, parallel = profile['resources'], profile['parallelism']
    for name in ('nodes', 'gpus_per_node', 'cpus_per_task'):
        if type(resources[name]) is not int or resources[name] < 1:
            raise ValueError('Invalid resource: ' + name)
    if any(type(parallel[k]) is not int or parallel[k] < 1 for k in ('tensor', 'pipeline')):
        raise ValueError('Invalid parallelism')
    if parallel['tensor'] * parallel['pipeline'] != resources['nodes'] * resources['gpus_per_node']:
        raise ValueError('TP x PP must match the allocated GPU count')
    if not re.fullmatch(r'[1-9][0-9]*(?:[KMGTP]B?)?', resources['memory']):
        raise ValueError('Invalid memory request')
    if not re.fullmatch(r'(?:[0-9]+-)?[0-9]+:[0-5][0-9]:[0-5][0-9]', resources['time']):
        raise ValueError('Invalid time limit')
    reserved = {'--served-model-name', '--tensor-parallel-size', '--pipeline-parallel-size',
                '--nnodes', '--node-rank', '--master-addr', '--master-port', '--host', '--port',
                '--headless', '--api-key', '--config'}
    args = profile['engine_args']
    if not isinstance(args, list) or not all(isinstance(v, str) for v in args):
        raise ValueError('engine_args must be a string array')
    if any(v.split('=', 1)[0] in reserved for v in args):
        raise ValueError('Engine arguments override a managed connection or parallelism flag')
    if set(profile.get('hooks', [])) - {'nccl_ep', 'warm_engram'}:
        raise ValueError('Unknown pre-start hook')
    if 'nccl_ep' in profile.get('hooks', []) and (parallel['pipeline'] != 1 or '--enable-expert-parallel' not in args):
        raise ValueError('The EP smoke hook requires PP=1 and expert parallelism')
    if not 1 <= profile['client']['local_port'] <= 65535 or profile['client']['timeout_seconds'] <= 0:
        raise ValueError('Invalid client port or timeout')
    if profile['startup_timeout_seconds'] <= 0:
        raise ValueError('Invalid startup timeout')
    env = profile.get('environment', {})
    if not isinstance(env, dict) or any(not re.fullmatch(r'[A-Z][A-Z0-9_]*', k) or not isinstance(v, str) for k, v in env.items()):
        raise ValueError('Invalid container environment')
    protected = {'VLLM_API_KEY', 'PYTHONPATH', 'VLLM_HOST_IP', 'CUDA_VISIBLE_DEVICES', 'HF_HOME', 'TMPDIR'}
    if protected.intersection(env):
        raise ValueError('Profile overrides a private/runtime environment variable')
    return profile


def verify_release(root=ROOT):
    manifest = load(root / 'RELEASE_MANIFEST.json')
    for name, expected in manifest['files'].items():
        path = root / name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink():
            raise ValueError('Unsafe release manifest path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Release checksum mismatch: ' + name)
    return manifest


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Private directory must not be a symlink: ' + str(path))
    path.mkdir(mode=0o700, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise ValueError('Private directory belongs to another account: ' + str(path))
    path.chmod(0o700)
    return path


def config():
    return load(ROOT / 'RUN_CONFIG.json')


def preflight(cfg):
    verify_release()
    profile_check(cfg['profile'])
    model = Path(cfg['model_path'])
    index = load(model / 'model.safetensors.index.json')
    shards = sorted(set(index['weight_map'].values()))
    expected = cfg['profile'].get('expected_shards')
    if not shards or (expected is not None and len(shards) != expected):
        raise ValueError('Unexpected checkpoint shard count')
    required = [model / 'config.json', model / 'tokenizer_config.json',
                Path(cfg['sif']), Path(cfg['overlay']) / 'tilelang/__init__.py']
    for name in shards:
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Unsafe checkpoint shard path')
        required.append(model / name)
    for path in required:
        with path.open('rb') as handle:
            if not handle.read(1):
                raise ValueError('Empty required asset: ' + str(path))
    runtime = load(Path(cfg['runtime_path']) / 'runtime.json')
    if runtime['id'] != cfg['profile']['runtime'] or Path(cfg['sif']).stat().st_size != runtime['sif_size']:
        raise ValueError('Runtime registration mismatch')
    if not shutil.which(cfg['container_command']) or not shutil.which('sbatch'):
        raise ValueError('Container runner and Slurm must be available on this host')
    cache = Path(cfg['cache_path'])
    private_dir(cache.parent)
    private_dir(cache)
    for subdir in ('cache', 'tmp'):
        private_dir(cache / subdir)
    for subdir in ('hf', 'vllm', 'triton', 'inductor', 'xdg', 'flashinfer-root', 'tilelang'):
        private_dir(cache / 'cache' / subdir)
    private_dir(cache / 'tmp/runtime')
    print('PREFLIGHT_OK: {} readable shards; runtime {}; private cache {}.'.format(
        len(shards), runtime['id'], cache), file=sys.stderr)


def prepare(args):
    manifest = verify_release()
    name = identifier(args.model)
    profile = profile_check(load(ROOT / 'profiles' / (name + '.json')))
    base = Path(args.base or os.environ.get('VLLM_TEAM_BASE', '')).expanduser()
    if not args.base and not os.environ.get('VLLM_TEAM_BASE'):
        raise ValueError('Specify --base or VLLM_TEAM_BASE')
    base = base.resolve()
    site = load(base / 'inference-site.json')
    if site['schema_version'] != 1:
        raise ValueError('Unsupported site schema')
    runtime_path = (base / site['runtime_paths'][profile['runtime']]).resolve()
    runtime = load(runtime_path / 'runtime.json')
    if runtime['id'] != profile['runtime']:
        raise ValueError('Wrong runtime registration')
    user = identifier(getpass.getuser())
    users = base / 'users'
    if not users.is_dir():
        raise ValueError('Site operator must create the shared users parent directory')
    cfg = {'schema_version': 1, 'profile': profile, 'site': site, 'base': str(base),
           'source_release': str(ROOT), 'release': manifest['release'],
           'model_path': str((base / site['checkpoints'][profile['checkpoint']]).resolve()),
           'runtime_path': str(runtime_path), 'sif': str(runtime_path / runtime['sif']),
           'overlay': str(runtime_path / runtime['overlay']),
           'cache_path': str(users / user / (name + '--' + profile['runtime'])),
           'container_command': shutil.which('singularity') or shutil.which('apptainer') or 'singularity'}
    preflight(cfg)
    run = Path(tempfile.mkdtemp(prefix=name + '-run-', dir=str(Path.home())))
    run.chmod(0o700)
    for filename in manifest['files']:
        destination = run / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(ROOT / filename), str(destination))
        destination.chmod(0o600)
    shutil.copyfile(str(ROOT / 'RELEASE_MANIFEST.json'), str(run / 'RELEASE_MANIFEST.json'))
    private_dir(run / '.runtime')
    private_dir(run / 'evidence')
    key_path = run / '.runtime/api_key'
    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(secrets.token_urlsafe(32) + '\n')
    save(run / 'RUN_CONFIG.json', cfg)
    # stdout is the run path only, allowing RUN_DIR=$(...). Never print the key.
    print(run)


def assert_private_run():
    if Path.cwd().resolve() != ROOT:
        raise ValueError('cd into the private run directory first')
    for path in (ROOT, ROOT / '.runtime', ROOT / '.runtime/api_key'):
        mode = path.lstat()
        if stat.S_ISLNK(mode.st_mode) or mode.st_uid != os.getuid() or mode.st_mode & 0o077:
            raise ValueError('Run directory and key must be private and owned by this account')


def submit_command(cfg, reservation=None):
    p, site = cfg['profile'], cfg['site']
    resources = p['resources']
    account = os.environ.get('SBATCH_ACCOUNT') or site['account']
    if account == 'YOUR_PROJECT':
        raise ValueError('Set a valid project allocation in the site configuration')
    for value in (account, site['partition'], site['qos']):
        identifier(value)
    command = ['sbatch', '--parsable', '--job-name=' + p['id'] + '-common',
               '--account=' + account, '--partition=' + site['partition'], '--qos=' + site['qos'],
               '--nodes=' + str(resources['nodes']), '--ntasks-per-node=1',
               '--gres=gpu:' + str(resources['gpus_per_node']),
               '--cpus-per-task=' + str(resources['cpus_per_task']), '--mem=' + resources['memory'],
               '--time=' + resources['time'], '--signal=B:TERM@90', '--output=%x-%j.out',
               '--chdir=' + str(ROOT)]
    if reservation:
        command.append('--reservation=' + identifier(reservation))
    return command + [str(ROOT / 'serve_model.sbatch')]


def submit(args):
    assert_private_run()
    cfg = config()
    preflight(cfg)
    command = submit_command(cfg, args.reservation)
    if args.dry_run:
        print(json.dumps(command))
        return
    if (ROOT / '.runtime/job_id').exists():
        raise ValueError('This run has already been submitted; prepare a new run')
    lock = ROOT / '.runtime/submitting'
    fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        lock.unlink()
        raise RuntimeError(result.stderr.decode(errors='replace'))
    job = result.stdout.decode().strip().split(';')[0]
    if not re.fullmatch(r'[0-9]+', job):
        raise RuntimeError('Submission outcome uncertain. Keep the submitting marker and check squeue before retrying.')
    (ROOT / '.runtime/job_id').write_text(job + '\n')
    lock.unlink()
    print('Submitted job ' + job)
    print('Wait for INFERENCE_READY in {}-common-{}.out, then read .runtime/endpoint.json.'.format(cfg['profile']['id'], job))


def cpu_check():
    cfg = config()
    preflight(cfg)
    from node import container_command
    command, environment = container_command(cfg, ['python3', '-c',
        'import torch,vllm,tilelang; print("RUNTIME_IMPORT_OK",torch.__version__,vllm.__version__,tilelang.__file__)'], gpu=False)
    subprocess.check_call(command, env=environment)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command')
    p = sub.add_parser('prepare')
    p.add_argument('model')
    p.add_argument('--base')
    p = sub.add_parser('submit')
    p.add_argument('--reservation')
    p.add_argument('--dry-run', action='store_true')
    sub.add_parser('preflight')
    sub.add_parser('cpu-check')
    sub.add_parser('info')
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args)
    elif args.command == 'submit':
        submit(args)
    elif args.command == 'preflight':
        assert_private_run()
        preflight(config())
    elif args.command == 'cpu-check':
        assert_private_run()
        cpu_check()
    elif args.command == 'info':
        cfg = config()
        print(json.dumps({k: cfg[k] for k in ('release', 'source_release', 'profile', 'runtime_path', 'model_path', 'cache_path')}, indent=2))
    else:
        parser.print_help()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        raise SystemExit(str(error))
