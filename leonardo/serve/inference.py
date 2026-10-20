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
# API checks that must pass before an endpoint is published. Profiles pick a suite with "checks".
CHECK_SUITES = {
    'chat_tools': ('unauthenticated_rejected', 'model_catalog', 'arithmetic', 'english_response',
                   'sse_streaming', 'forced_function_call', 'four_concurrent_requests'),
    'chat': ('unauthenticated_rejected', 'model_catalog', 'arithmetic', 'english_response',
             'sse_streaming', 'four_concurrent_requests'),
    'minimal': ('unauthenticated_rejected', 'model_catalog', 'any_completion'),
    'classify': ('unauthenticated_rejected', 'model_catalog', 'classification'),
}


def load(path):
    return json.loads(Path(path).read_text())


def catalog():
    return load(ROOT / 'catalog.json')


def check_suite(profile):
    return CHECK_SUITES[profile.get('checks', 'chat_tools')]


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
    if profile.get('checks', 'chat_tools') not in CHECK_SUITES:
        raise ValueError('Unknown check suite; use one of ' + ', '.join(sorted(CHECK_SUITES)))
    if profile['runtime'] not in catalog()['runtimes']:
        raise ValueError('Runtime is not in catalog.json: ' + profile['runtime'])
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
    if (model / 'model.safetensors.index.json').exists():
        shards = sorted(set(load(model / 'model.safetensors.index.json')['weight_map'].values()))
    else:
        shards = ['model.safetensors']  # small checkpoints come as a single unindexed file
    expected = cfg['profile'].get('expected_shards')
    if not shards or (expected is not None and len(shards) != expected):
        raise ValueError('Unexpected checkpoint shard count')
    required = [model / 'config.json', model / 'tokenizer_config.json', Path(cfg['sif'])]
    for name in catalog()['runtimes'][cfg['profile']['runtime']]['overlay_required']:
        required.append(Path(cfg['overlay']) / name)
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


def site_base(value):
    raw = value or os.environ.get('VLLM_TEAM_BASE')
    if not raw:
        raise ValueError('Specify --base or VLLM_TEAM_BASE')
    # The admin CLI passes '$WORK/<team>' unexpanded so it resolves on Leonardo.
    return Path(os.path.expandvars(raw)).expanduser().resolve()


def load_site(base):
    site = load(base / 'inference-site.json')
    if site['schema_version'] != 1:
        raise ValueError('Unsupported site schema')
    return site


def prepare_run(model, base_arg):
    manifest = verify_release()
    name = identifier(model)
    if not (ROOT / 'profiles' / (name + '.json')).exists():
        raise ValueError('Unknown profile {}; available: {}'.format(
            name, ', '.join(sorted(p.stem for p in (ROOT / 'profiles').glob('*.json')))))
    profile = profile_check(load(ROOT / 'profiles' / (name + '.json')))
    base = site_base(base_arg)
    site = load_site(base)
    if profile['runtime'] not in site['runtime_paths'] or profile['checkpoint'] not in site['checkpoints']:
        raise ValueError('Runtime {} or checkpoint {} is not installed at this site; see leonardo/install'.format(
            profile['runtime'], profile['checkpoint']))
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
    return run


def prepare(args):
    run = prepare_run(args.model, args.base)
    # stdout is the run path only, allowing RUN_DIR=$(...). Never print the key.
    print(run)


def assert_private_run():
    if Path.cwd().resolve() != ROOT:
        raise ValueError('cd into the private run directory first')
    for path in (ROOT, ROOT / '.runtime', ROOT / '.runtime/api_key'):
        mode = path.lstat()
        if stat.S_ISLNK(mode.st_mode) or mode.st_uid != os.getuid() or mode.st_mode & 0o077:
            raise ValueError('Run directory and key must be private and owned by this account')


def time_limit(value):
    if not re.fullmatch(r'(?:[0-9]+-)?[0-9]+:[0-5][0-9]:[0-5][0-9]', value):
        raise ValueError('Invalid time limit: ' + value)
    return value


def submit_command(cfg, reservation=None, qos=None, time=None):
    p, site = cfg['profile'], cfg['site']
    resources = p['resources']
    account = os.environ.get('SBATCH_ACCOUNT') or site['account']
    if account == 'YOUR_PROJECT':
        raise ValueError('Set a valid project allocation in the site configuration')
    qos = qos or site['qos']
    for value in (account, site['partition'], qos):
        identifier(value)
    command = ['sbatch', '--parsable', '--job-name=' + p['id'] + '-common',
               '--account=' + account, '--partition=' + site['partition'], '--qos=' + qos,
               '--nodes=' + str(resources['nodes']), '--ntasks-per-node=1',
               '--gres=gpu:' + str(resources['gpus_per_node']),
               '--cpus-per-task=' + str(resources['cpus_per_task']), '--mem=' + resources['memory'],
               '--time=' + time_limit(time or resources['time']), '--signal=B:TERM@90', '--output=%x-%j.out',
               '--chdir=' + str(ROOT)]
    if reservation:
        command.append('--reservation=' + identifier(reservation))
    return command + [str(ROOT / 'serve_model.sbatch')]


def sbatch(command, marker, cwd=None):
    """Run sbatch once; `marker` guards against a second submission when the outcome is unknown."""
    fd = os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    if result.returncode:
        marker.unlink()
        raise RuntimeError(result.stderr.decode(errors='replace'))
    job = result.stdout.decode().strip().split(';')[0]
    if not re.fullmatch(r'[0-9]+', job):
        raise RuntimeError('Submission outcome uncertain. Keep {} and check squeue before retrying.'.format(marker))
    marker.unlink()
    return job


def submit(args):
    assert_private_run()
    cfg = config()
    preflight(cfg)
    command = submit_command(cfg, args.reservation, args.qos, args.time)
    if args.dry_run:
        print(json.dumps(command))
        return
    if (ROOT / '.runtime/job_id').exists():
        raise ValueError('This run has already been submitted; prepare a new run')
    job = sbatch(command, ROOT / '.runtime/submitting')
    (ROOT / '.runtime/job_id').write_text(job + '\n')
    print('Submitted job ' + job)
    print('Wait for INFERENCE_READY in {}-common-{}.out, then read .runtime/endpoint.json.'.format(cfg['profile']['id'], job))


def up(args):
    """prepare + submit in one step; what the admin CLI runs over SSH."""
    run = prepare_run(args.model, args.base)
    print('Run directory: ' + str(run), flush=True)
    command = [sys.executable, str(run / 'inference.py'), 'submit']
    for flag in ('reservation', 'qos', 'time'):
        if getattr(args, flag):
            command += ['--' + flag, getattr(args, flag)]
    subprocess.check_call(command, cwd=str(run))


def active_jobs():
    """Own Slurm jobs by id, or None if squeue is unavailable."""
    try:
        output = subprocess.check_output(['squeue', '-h', '-u', getpass.getuser(), '-o', '%i|%j|%T|%M|%l|%N'],
                                         stderr=subprocess.DEVNULL, timeout=30).decode()
    except (OSError, subprocess.SubprocessError):
        return None
    jobs = {}
    for line in output.splitlines():
        fields = line.strip().split('|')
        if len(fields) == 6:
            jobs[fields[0]] = dict(zip(('job_id', 'name', 'state', 'elapsed', 'limit', 'nodes'), fields))
    return jobs


def own_runs():
    """Private run directories of this account, newest first, with job id and endpoint when known."""
    runs = []
    for path in Path.home().glob('*-run-*'):
        try:
            mode = path.lstat()
            if not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.getuid() or mode.st_mode & 0o077:
                continue
            cfg = load(path / 'RUN_CONFIG.json')
            job = (path / '.runtime/job_id').read_text().strip() if (path / '.runtime/job_id').exists() else None
            endpoint = load(path / '.runtime/endpoint.json') if (path / '.runtime/endpoint.json').exists() else None
        except (OSError, ValueError, KeyError):
            continue
        runs.append({'run': path, 'profile': cfg['profile'], 'job': job, 'endpoint': endpoint,
                     'mtime': mode.st_mtime})
    return sorted(runs, key=lambda r: -r['mtime'])


def status(args):
    import gateway
    import registry
    jobs = active_jobs()
    report = {'jobs_known': jobs is not None, 'servers': [], 'gateway': gateway.state(jobs), 'registry': None}
    for run in own_runs():
        if run['job'] and (jobs is None or run['job'] in jobs):
            job = (jobs or {}).get(run['job'], {})
            report['servers'].append({'profile': run['profile']['id'], 'model': run['profile']['served_model'],
                                      'job_id': run['job'], 'state': job.get('state', 'unknown'),
                                      'elapsed': job.get('elapsed'), 'limit': job.get('limit'),
                                      'ready': run['endpoint'] is not None, 'run': str(run['run']),
                                      'endpoint': run['endpoint'] and '{}:{}'.format(run['endpoint']['host'], run['endpoint']['port'])})
    if args.base:
        base = site_base(args.base)
        root = registry.directory({'base': str(base), 'site': load_site(base)})
        report['registry'] = registry.records(root) if root else None
    if args.json:
        print(json.dumps(report, indent=2))
        return
    if jobs is None:
        print('WARNING: squeue failed; showing runs that may already have ended.')
    print('Your model servers:')
    for s in report['servers'] or [{}]:
        if s:
            print('  {profile:<12} job {job_id:<10} {state:<10} {elapsed}/{limit}  {0}'.format(
                ('READY ' + s['model'] + ' at ' + s['endpoint']) if s['ready'] else 'starting (no endpoint yet)', **s))
        else:
            print('  none')
    g = report['gateway']
    print('Your gateway: ' + (g['summary'] if g else 'not running'))
    if report['registry'] is not None:
        print('Team endpoints in the shared registry:')
        for r in report['registry'] or [None]:
            r = r and {k: r.get(k, '?') for k in ('name', 'owner', 'host', 'port', 'job_id', 'slurm_expected_end')}
            print('  {name:<36} {owner:<10} {host}:{port} job {job_id} until {slurm_expected_end}'.format(**r) if r else '  none')


def down(args):
    import gateway
    if args.target == 'gateway':
        gateway.down()
        return
    jobs = active_jobs()
    targets = []
    for run in own_runs():
        p = run['profile']
        if run['job'] and args.target in (p['id'], p['served_model'], run['job']):
            if jobs is None or run['job'] in jobs:
                targets.append(run['job'])
    if not targets:
        raise ValueError('No running server of yours matches {}; see status'.format(args.target))
    subprocess.check_call(['scancel'] + targets)
    print('Cancelled job(s) ' + ' '.join(targets))


def cpu_check():
    cfg = config()
    preflight(cfg)
    from node import container_command
    modules = catalog()['runtimes'][cfg['profile']['runtime']]['imports']
    code = 'import {0}; print("RUNTIME_IMPORT_OK", {1})'.format(
        ','.join(modules), ','.join('{0}.__version__ if hasattr({0}, "__version__") else {0}.__file__'.format(m) for m in modules))
    command, environment = container_command(cfg, ['python3', '-c', code], gpu=False)
    subprocess.check_call(command, env=environment)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command')
    p = sub.add_parser('prepare', help='check assets and create a private run directory')
    p.add_argument('model')
    p.add_argument('--base')
    for name, text in (('submit', 'submit a prepared run (from its directory)'), ('up', 'prepare and submit')):
        p = sub.add_parser(name, help=text)
        if name == 'up':
            p.add_argument('model')
            p.add_argument('--base')
        p.add_argument('--reservation')
        p.add_argument('--qos', help='override the site QoS, e.g. boost_qos_dbg')
        p.add_argument('--time', help='override the profile time limit, e.g. 00:30:00')
        if name == 'submit':
            p.add_argument('--dry-run', action='store_true')
    p = sub.add_parser('status', help='your servers and gateway, and the team registry')
    p.add_argument('--base')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('down', help='cancel your servers for a profile, model or job id, or "gateway"')
    p.add_argument('target')
    p = sub.add_parser('gateway-up', help='start the gateway job that routes to your servers')
    p.add_argument('--base')
    p.add_argument('--hours', type=float, default=12, help='keep resubmitting for this long (default 12)')
    p.add_argument('--restart', action='store_true', help='cancel a running gateway first')
    p = sub.add_parser('gateway-token', help='create, replace or print the gateway token')
    p.add_argument('action', choices=['ensure', 'set', 'show'])
    sub.add_parser('gateway-next', help='(used by gateway.sbatch) submit the successor job')
    sub.add_parser('preflight')
    sub.add_parser('cpu-check')
    sub.add_parser('info')
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args)
    elif args.command == 'submit':
        submit(args)
    elif args.command == 'up':
        up(args)
    elif args.command == 'status':
        status(args)
    elif args.command == 'down':
        down(args)
    elif args.command in ('gateway-up', 'gateway-token', 'gateway-next'):
        import gateway
        {'gateway-up': gateway.up, 'gateway-token': gateway.token, 'gateway-next': gateway.next_job}[args.command](args)
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
