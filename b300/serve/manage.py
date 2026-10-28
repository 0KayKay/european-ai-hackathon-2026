"""Private per-user B300 run management. Called by cli.py over authenticated SSH."""
import fcntl
import getpass
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from common import ROOT, identifier, load, private, ready_session, save, time_seconds, verify_release


def now():
    return datetime.now(timezone.utc).isoformat()


def execute(command, timeout=35):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Command %s failed (exit %s)' % (command[0], result.returncode))
    return result.stdout


def base_path(value):
    base = Path(os.path.expandvars(os.path.expanduser(value)))
    if not base.is_absolute() or any(c in str(base) for c in ',:\n\r'):
        raise ValueError('Base must be an absolute or home-relative path without mount separators')
    return base


def mine(base):
    return private(base / 'users' / identifier(getpass.getuser()))


def jobs():
    text = execute(['squeue', '--noheader', '--user', str(os.getuid()), '--format=%i|%T|%u|%j|%R'])
    output = {}
    for line in text.splitlines():
        number, state, user, name, reason = line.strip().split('|', 4)
        output[number] = {'state': state, 'uid': pwd.getpwnam(user).pw_uid, 'name': name, 'reason': reason}
    return output


def runs(base):
    folder = mine(base) / 'runs'
    if not folder.exists():
        return []
    output = []
    for path in sorted(folder.iterdir(), reverse=True):
        if path.is_symlink() or path.stat().st_uid != os.getuid():
            continue
        if (path / 'RUN_CONFIG.json').exists():
            output.append((path, load(path / 'RUN_CONFIG.json')))
    return output


def select(base, target):
    identifier(target)
    for path, config in runs(base):
        receipt = load(path / 'submission.json') if (path / 'submission.json').exists() else {}
        if target in (path.name, config['profile'], str(receipt.get('job_id'))):
            return path, config, receipt
    raise ValueError('No matching run owned by this account')


def init(base, args):
    verify_release()
    site = base / 'b300-site.json'
    if site.exists():
        raise ValueError('Team settings already exist; init never overwrites them')
    settings = load(ROOT / 'site.example.json')
    for name in ('account', 'partition', 'qos'):
        if args.get(name):
            settings[name] = identifier(args[name])
    base.mkdir(parents=True, exist_ok=True)
    (base / 'users').mkdir(exist_ok=True)
    (base / 'users').chmod(0o1700)
    save(site, settings)
    mine(base)
    return {'phase': 'initialized', 'base': str(base), 'site': settings}


def share(base, args):
    load(base / 'b300-site.json')
    targets = [base, base / 'users', base / 'b300-site.json']
    if any(path.is_symlink() or path.stat().st_uid != os.getuid() for path in targets):
        raise ValueError('Only the team-folder owner can change its access')
    names = [identifier(name) for name in args['users']]
    for name in names:
        pwd.getpwnam(name)
    if args.get('revoke'):
        entries = ','.join('u:' + name for name in names)
        execute(['setfacl', '-x', entries, *map(str, targets)])
    else:
        execute(['setfacl', '-m', ','.join('u:' + n + ':r-x' for n in names), str(base)])
        execute(['setfacl', '-m', ','.join('u:' + n + ':rwx' for n in names), str(base / 'users')])
        execute(['setfacl', '-m', ','.join('u:' + n + ':r--' for n in names), str(base / 'b300-site.json')])
    # Never traverse users/<login> or grant access to keys. Parents must already be traversable.
    return {'phase': 'access_updated', 'users': names, 'revoke': bool(args.get('revoke')),
            'note': 'Parent directories must also permit traversal; personal runs remain private.'}


def submission_command(profile, site, path, name, duration, node=None):
    time_seconds(duration)
    command = ['sbatch', '--parsable', '--nodes=1', '--ntasks=1', '--gpus=' + str(profile['gpus']),
        '--cpus-per-task=' + str(profile['cpus']), '--mem=' + profile['memory'], '--time=' + duration,
        '--signal=B:TERM@120', '--account=' + identifier(site['account']),
        '--partition=' + identifier(site['partition']), '--qos=' + identifier(site['qos']),
        '--job-name=' + identifier(name), '--output=' + str(path / 'slurm.log'), '--chdir=' + str(path)]
    if node:
        command.append('--nodelist=' + identifier(node))
    return command + [str(path / 'serve/run.sbatch'), str(path)]


def start(base, args):
    verify_release()
    site = load(base / 'b300-site.json')
    profile_id = identifier(args['profile'])
    profile = load(ROOT / 'profiles.json')[profile_id]
    user = mine(base)
    with (user / '.submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        live = jobs()  # Failure to read the queue never means it is empty.
        for path, config in runs(base):
            if config['profile'] != profile_id:
                continue
            receipt = load(path / 'submission.json') if (path / 'submission.json').exists() else {}
            if receipt.get('phase') in ('submitting', 'submission_uncertain'):
                raise RuntimeError('Uncertain submission exists; inspect its run and Slurm before resubmitting')
            if str(receipt.get('job_id')) in live:
                return {'phase': 'already_active', 'run_id': path.name, 'job_id': receipt['job_id']}
        image = load(ROOT / 'images.json')[profile['image']]
        reuse = None
        node = args.get('node')
        if args.get('reuse_job'):
            old, config, receipt = select(base, str(args['reuse_job']))
            if str(receipt.get('job_id')) in live:
                raise ValueError('Cannot reuse scratch from an active allocation')
            cache = load(old / 'cache.json')
            old_state = load(old / 'state.json')
            if not old_state.get('owned_servers_stopped') or not old_state.get('session_keys_removed'):
                raise ValueError('Old owned-server and key cleanup must be verified first')
            if cache['image'] != image or cache['owner_uid'] != os.getuid():
                raise ValueError('Cache image or ownership differs')
            if node and node != cache['node']:
                raise ValueError('Node-local cache requires its original node')
            node, reuse = identifier(cache['node']), cache['scratch']
            if not re.fullmatch(r'/tmp/b300-models\.[A-Za-z0-9_.-]+', reuse):
                raise ValueError('Invalid owned cache path')
        run_id = profile_id + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(3)
        path = private(user / 'runs' / run_id)
        shutil.copytree(ROOT, path / 'serve', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        config = {'profile': profile_id, 'mode': args['mode'], 'owner_uid': os.getuid(),
            'image': image, 'reuse_scratch': reuse, 'created_at_utc': now()}
        save(path / 'RUN_CONFIG.json', config)
        name = 'b300-' + profile_id + '-' + run_id[-6:]
        duration = args.get('time') or profile['time']
        if time_seconds(duration) > site.get('max_time_seconds', 14400):
            raise ValueError('Requested time exceeds this team configuration')
        command = submission_command(profile, site, path, name, duration, node)
        save(path / 'submission.json', {'phase': 'submitting', 'job_name': name})
        try:
            number = execute(command).strip().split(';')[0]
            if not number.isdecimal():
                raise RuntimeError('Submission response is uncertain')
        except Exception:
            save(path / 'submission.json', {'phase': 'submission_uncertain', 'job_name': name})
            raise
        save(path / 'submission.json', {'phase': 'submitted', 'job_id': number, 'job_name': name})
        return {'phase': 'submitted', 'job_id': number, 'run_id': run_id, 'mode': args['mode'],
                'gpus': profile['gpus'], 'time': duration, 'log': str(path / 'slurm.log')}


def status(base):
    live = jobs()
    rows = []
    for path, config in runs(base):
        receipt = load(path / 'submission.json') if (path / 'submission.json').exists() else {}
        state = load(path / 'state.json') if (path / 'state.json').exists() else {}
        job = str(receipt.get('job_id', ''))
        endpoint = load(path / 'endpoint.json') if (path / 'endpoint.json').exists() else {}
        rows.append({'run_id': path.name, 'profile': config['profile'], 'mode': config['mode'],
            'job_id': job, 'slurm_state': live.get(job, {}).get('state', 'NOT_IN_QUEUE'),
            'queue_reason_or_node': live.get(job, {}).get('reason'),
            'phase': state.get('phase', receipt.get('phase')),
            'ready': ready_session(job, state, endpoint, os.getuid(), live)})
    return {'runs': rows}


def fetch(base, target):
    path, config, receipt = select(base, target)
    job = str(receipt['job_id'])
    if not (path / 'state.json').exists() or not (path / 'endpoint.json').exists():
        raise RuntimeError('The owned session is not ready; pending/loading/finished runs cannot publish keys')
    state, endpoint = load(path / 'state.json'), load(path / 'endpoint.json')
    if not ready_session(job, state, endpoint, os.getuid(), jobs()):
        raise RuntimeError('The owned model allocation is not ready; stale endpoints cannot be fetched')
    keys = {}
    for kind in endpoint['models']:
        identifier(kind)
        keys[kind] = (path / (kind + '.api-key')).read_text().strip()
    if not ready_session(job, load(path / 'state.json'), endpoint, os.getuid(), jobs()):
        raise RuntimeError('The allocation ended while session files were being fetched')
    return {'run_id': path.name, 'endpoint': endpoint, 'keys': keys}


def down(base, target):
    path, config, receipt = select(base, target)
    number = str(receipt['job_id'])
    live = jobs()
    if number in live:
        if live[number]['uid'] != os.getuid() or live[number]['name'] != receipt['job_name']:
            raise RuntimeError('Refusing to cancel an allocation with a different owner or identity')
        execute(['scancel', number])
        for _ in range(30):
            if number not in jobs():
                break
            time.sleep(1)
    empty = number not in jobs()
    if empty:
        for name in ('endpoint.json', 'glm.api-key', 'ultra.api-key', 'safety.api-key', 'safeguard.api-key'):
            (path / name).unlink(missing_ok=True)
    return {'job_id': number, 'phase': 'stopped' if empty else 'cleanup_pending',
            'queue_empty': empty, 'inactive_session_files_removed': empty}


def main():
    os.umask(0o077)
    try:
        args = json.loads(sys.argv[1])
        base = base_path(args['base'])
        action = args['action']
        if action == 'init':
            result = init(base, args)
        elif action == 'share':
            result = share(base, args)
        elif action in ('up', 'install'):
            args['mode'] = 'install' if action == 'install' else 'serve'
            result = start(base, args)
        elif action == 'status':
            result = status(base)
        elif action == 'fetch':
            result = fetch(base, args['target'])
        elif action == 'down':
            result = down(base, args['target'])
        elif action == 'logs':
            path, _, _ = select(base, args['target'])
            lines = max(1, min(int(args.get('lines', 60)), 1000))
            file = path / 'slurm.log'
            result = {'log': '\n'.join(file.read_text(errors='replace').splitlines()[-lines:]) if file.exists()
                      else 'Batch log not created yet; the allocation may still be pending.'}
        else:
            raise ValueError('Unknown management command')
        print(json.dumps({'ok': True, 'result': result}))
    except Exception as error:
        # Do not serialize captured SSH/stdout, environment, API keys or arbitrary exceptions.
        print(json.dumps({'ok': False, 'error_type': type(error).__name__,
                          'message': str(error) if isinstance(error, (ValueError, RuntimeError)) else
                          'Inspect the private run and Slurm state; no successful outcome is implied.'}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
