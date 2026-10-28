#!/usr/bin/env python3
"""B300 team inference from a laptop. Python 3.9+, standard library only.

Set B300_BASE to a remote team folder and B300_SSH_COMMAND_FILE to your private,
fresh AXIS-issued `ssh HOST -l USER` command file. Host checking remains strict.
Help and profiles are offline. Other commands use your own authenticated SSH.
"""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'serve'))
from common import identifier, load, private, save, verify_release  # noqa: E402


class Transport:
    def __init__(self, command_file):
        if not command_file:
            raise ValueError('Set B300_SSH_COMMAND_FILE to your private current AXIS SSH command')
        path = Path(command_file).expanduser()
        if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise ValueError('SSH command file must be private: chmod 600 FILE')
        args = shlex.split(path.read_text())
        if (len(args) != 4 or args[0] != 'ssh' or args[2] != '-l'
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', args[1])
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@+-]*', args[3])):
            raise ValueError('Expected the current AXIS ssh HOST -l USER command')
        self.host, self.user = args[1], args[3]
        state = private(ROOT / '.runtime')
        identity = hashlib.sha256((self.host + '\0' + self.user).encode()).hexdigest()
        record = state / 'transport.json'
        previous = load(record) if record.exists() else {}
        folder = Path(previous.get('socket_dir', '/nonexistent'))
        if previous.get('identity') != identity or not folder.exists():
            folder = Path(tempfile.mkdtemp(prefix='b300-ssh-'))
            save(record, {'identity': identity, 'socket_dir': str(folder)})
        if folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o077:
            raise ValueError('Private SSH socket directory has unsafe ownership or permissions')
        self.socket = str(folder / 'master')

    def command(self):
        return ['ssh', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=20',
                '-o', 'ControlMaster=auto', '-o', 'ControlPersist=1800',
                '-o', 'ControlPath=' + self.socket, '-o', 'ServerAliveInterval=30',
                '-o', 'ServerAliveCountMax=3']

    def call(self, script, data=None, timeout=90):
        result = subprocess.run(self.command() + [self.host, '-l', self.user,
            'bash -lc ' + shlex.quote('umask 077; ' + script)],
            input=data, capture_output=True, timeout=timeout)
        if result.returncode:
            try:
                if json.loads(result.stdout).get('ok') is False:
                    return result.stdout
            except (ValueError, AttributeError):
                pass
            raise RuntimeError('SSH/remote operation failed; verify the fresh AXIS session and trusted host key')
        return result.stdout

    def control(self, action, specs):
        command = ['ssh', '-o', 'StrictHostKeyChecking=yes', '-o', 'ControlPath=' + self.socket,
                   '-o', 'ExitOnForwardFailure=yes', '-O', action]
        for spec in specs:
            command += ['-L', spec]
        result = subprocess.run(command + [self.host, '-l', self.user], capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Private SSH forward control failed; no new connection was silently opened')


def archive_release():
    entries = verify_release(ROOT / 'serve')
    output = io.BytesIO()
    digest = hashlib.sha256()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name in sorted(list(entries) + ['RELEASE_MANIFEST.json']):
            data = (ROOT / 'serve' / name).read_bytes()
            digest.update(name.encode() + b'\0' + hashlib.sha256(data).digest())
            entry = tarfile.TarInfo('serve/' + name)
            entry.size, entry.mode = len(data), 0o600
            archive.addfile(entry, io.BytesIO(data))
    return digest.hexdigest()[:20], output.getvalue()


def sync(transport):
    digest, data = archive_release()
    target = '"$HOME/.b300-inference/releases/' + digest + '"'
    # A shared release is immutable once complete. flock serializes interrupted uploads.
    script = ('mkdir -p "$HOME/.b300-inference/releases"; '
              'flock "$HOME/.b300-inference/sync.lock" bash -c ' + shlex.quote(
                  'if [ ! -f ' + target + '/_COMPLETE ]; then '
                  'mkdir -p ' + target + '; tar -xzf - -C ' + target + '; '
                  'python3 ' + target + '/serve/check_release.py && touch ' + target + '/_COMPLETE; fi'))
    transport.call(script, data=data)
    return target


def remote(transport, release, base, action, **fields):
    message = json.dumps(dict(action=action, base=base, **fields))
    output = transport.call('source /etc/profile.d/modules.sh; module load slurm; python3 ' +
        release + '/serve/manage.py ' + shlex.quote(message), timeout=120)
    data = json.loads(output)
    if not data['ok']:
        raise RuntimeError(data.get('message', 'Private run management failed'))
    return data['result']


def publish_private(path, value):
    """Publish complete session files atomically, including concurrent CLI callers."""
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(value)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def fetch(transport, release, base, target):
    identifier(target)
    folder = private(ROOT / '.runtime' / 'sessions' / target)
    try:
        result = remote(transport, release, base, 'fetch', target=target)
    except Exception:
        for name in ('endpoint.json', 'glm.api-key', 'ultra.api-key', 'safety.api-key', 'safeguard.api-key'):
            (folder / name).unlink(missing_ok=True)
        raise
    publish_private(folder / 'endpoint.json', json.dumps(result['endpoint'], indent=2) + '\n')
    for kind, key in result['keys'].items():
        if kind not in ('glm', 'ultra', 'safety', 'safeguard') or not key or any(c.isspace() for c in key):
            raise ValueError('Invalid model session key')
        path = folder / (kind + '.api-key')
        publish_private(path, key + '\n')
    return folder, result['endpoint']


def forward_specs(endpoint, ports):
    import ipaddress
    address = ipaddress.ip_address(endpoint['host_ip'])
    if not address.is_private or address.is_loopback or address.is_unspecified:
        raise ValueError('Expected a private compute-node address')
    specs = []
    for kind, item in endpoint['models'].items():
        local = ports.get(kind) or item['local_port']
        if any(type(p) is not int or not 1024 <= p <= 65535 for p in (local, item['port'])):
            raise ValueError('Invalid forwarding port')
        specs.append((kind, local, '127.0.0.1:%s:%s:%s' % (local, address, item['port'])))
    if len({row[1] for row in specs}) != len(specs):
        raise ValueError('Model local ports must differ')
    return specs


def forward(transport, release, args):
    _, endpoint = fetch(transport, release, args.base, args.target)
    specs = forward_specs(endpoint, {'glm': args.glm_port, 'ultra': args.ultra_port, 'safety': args.safety_port, 'safeguard': args.safeguard_port})
    # Refuse occupied ports; never cancel somebody else's existing forward.
    for _, port, _ in specs:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    lockfile = private(ROOT / '.runtime') / 'forward.lock'
    with lockfile.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A forward from this checkout is already active; use one paired run or stop that forward') from None
        attempted = [row[2] for row in specs]
        try:
            transport.control('forward', attempted)
            print('Keep this terminal open. ' + ', '.join('%s: http://127.0.0.1:%s/v1' % (k, p) for k, p, _ in specs), flush=True)
            while True:
                time.sleep(15)
                # Readiness is bounded by the current Slurm job, not a cached JSON file.
                _, current = fetch(transport, release, args.base, args.target)
                if current['job_id'] != endpoint['job_id']:
                    raise RuntimeError('The selected model session changed; reconnect explicitly')
        except KeyboardInterrupt:
            pass
        finally:
            for spec in attempted:
                try:
                    transport.control('cancel', [spec])
                except RuntimeError:
                    pass


def parser():
    top = argparse.ArgumentParser(description=__doc__)
    top.add_argument('--base', default=os.environ.get('B300_BASE', '~/b300-team'))
    top.add_argument('--ssh-command-file', default=os.environ.get('B300_SSH_COMMAND_FILE'))
    commands = top.add_subparsers(dest='command', required=True)
    p = commands.add_parser('init', help='Create remote team settings and your private run folder')
    for flag in ('account', 'partition', 'qos'):
        p.add_argument('--' + flag)
    p = commands.add_parser('share', help='Grant named cluster users access to team settings, not private runs')
    p.add_argument('users', nargs='+')
    p.add_argument('--revoke', action='store_true')
    commands.add_parser('profiles', help='List pinned deployment profiles offline')
    commands.add_parser('sync', help='Upload the verified serving source only')
    commands.add_parser('status', help='Read your Slurm jobs and private run states')
    for name in ('install', 'up'):
        p = commands.add_parser(name, help='Submit a bounded preparation or model-serving allocation')
        p.add_argument('profile', choices=sorted(load(ROOT / 'serve/profiles.json')))
        p.add_argument('--time')
        p.add_argument('--node')
        p.add_argument('--reuse-job', help='Reuse verified owned scratch from a finished job; pins its node')
    for name in ('fetch', 'down'):
        p = commands.add_parser(name)
        p.add_argument('target', help='Your profile, run ID or recorded job ID')
    p = commands.add_parser('logs')
    p.add_argument('target')
    p.add_argument('--lines', type=int, default=60)
    p.add_argument('--follow', '-f', action='store_true')
    p = commands.add_parser('forward', help='Forward all APIs in your selected run over one existing private SSH connection')
    p.add_argument('target')
    for kind in ('glm', 'ultra', 'safety', 'safeguard'):
        p.add_argument('--' + kind + '-port', type=int)
    p = commands.add_parser('query', help='Call a model through an already-open local forward')
    p.add_argument('target')
    p.add_argument('--model', choices=['glm', 'ultra', 'safety', 'safeguard'])
    p.add_argument('--action', choices=['models', 'chat', 'classify'], default='chat')
    p.add_argument('--text')
    p.add_argument('--policy-file')
    p.add_argument('--local-port', type=int)
    p.add_argument('--max-tokens', type=int)
    p.add_argument('--output')
    return top


def main():
    args = parser().parse_args()
    if args.command == 'profiles':
        print(json.dumps(load(ROOT / 'serve/profiles.json'), indent=2))
        return 0
    try:
        transport = Transport(args.ssh_command_file)
        release = sync(transport)
        if args.command == 'sync':
            print('Serving release verified and uploaded privately.')
        elif args.command == 'forward':
            forward(transport, release, args)
        elif args.command in ('fetch', 'query'):
            folder, endpoint = fetch(transport, release, args.base, args.target)
            if args.command == 'fetch':
                print('Current endpoint and model keys saved privately in ' + str(folder))
            else:
                kind = args.model or ('ultra' if 'ultra' in endpoint['models'] else next(iter(endpoint['models'])))
                if kind not in endpoint['models']:
                    raise ValueError('Selected model is absent from this run')
                port = args.local_port or endpoint['models'][kind]['local_port']
                command = [sys.executable, str(ROOT / 'clients/query.py'), '--profile', kind,
                    '--base-url', 'http://127.0.0.1:%s/v1' % port, '--key-file', str(folder / (kind + '.api-key')),
                    '--action', args.action]
                if args.max_tokens is not None:
                    command += ['--max-tokens', str(args.max_tokens)]
                for flag in ('text', 'policy_file', 'output'):
                    if getattr(args, flag) is not None:
                        command += ['--' + flag.replace('_', '-'), getattr(args, flag)]
                return subprocess.run(command).returncode
        elif args.command == 'logs':
            previous = None
            while True:
                result = remote(transport, release, args.base, 'logs', target=args.target, lines=args.lines)
                if result['log'] != previous:
                    print(result['log'], flush=True)
                    previous = result['log']
                if not args.follow:
                    break
                time.sleep(5)
        else:
            fields = {k: v for k, v in vars(args).items() if k not in ('command', 'base', 'ssh_command_file') and v is not None}
            print(json.dumps(remote(transport, release, args.base, args.command, **fields), indent=2))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        # Avoid leaking one-time SSH usernames, keys or captured command output.
        print('B300 command failed (%s). %s' % (type(error).__name__, str(error)
              if isinstance(error, (ValueError, RuntimeError)) else
              'Inspect your private session, run files and Slurm state.'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
