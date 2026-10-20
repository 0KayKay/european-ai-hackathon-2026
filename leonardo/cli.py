#!/usr/bin/env python3
"""Run Leonardo inference from any machine where you can SSH in with your own CINECA login.

Every command opens (or reuses) one SSH connection to a login node, uploads this checkout's serve/
release if Leonardo does not have it yet, and runs serve/inference.py there. Nothing logs in to
Leonardo from Kubernetes; the cluster is reached by the gateway's outbound tunnel instead.

  export LEONARDO_USER=<your login> LEONARDO_BASE='$WORK/<team folder>'
  ./cli.py up qwen06b            # prepare + submit a model server
  ./cli.py status                # your servers, your gateway, the team registry
  ./cli.py gateway up --hours 12 # one entry point for all your servers
  ./cli.py tunnel                # laptop -> gateway on 127.0.0.1:18000
  ./cli.py down qwen06b          # or a job id, or: down gateway

Standard library only; Python 3.7 or newer on this machine.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

SERVE = Path(__file__).resolve().parent / 'serve'
sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))
from update_manifest import build  # noqa: E402

STATE = '"$HOME/.leonardo-inference"'


def ssh_command(args):
    control = os.path.expanduser('~/.ssh/leonardo-%C')
    return ['ssh', '-o', 'ControlMaster=auto', '-o', 'ControlPath=' + control, '-o', 'ControlPersist=30m',
            '-o', 'ServerAliveInterval=30', args.user + '@' + args.host]


def remote(args, command, data=None, capture=False):
    """Run `command` in a login shell on Leonardo, so $WORK and modules are set up."""
    script = 'umask 077; ' + command
    result = subprocess.run(ssh_command(args) + ['bash -lc ' + shlex.quote(script)], input=data,
                            stdout=subprocess.PIPE if capture else None)
    if result.returncode:
        raise SystemExit(result.returncode)
    return result.stdout


def release_archive():
    manifest_path = SERVE / 'RELEASE_MANIFEST.json'
    manifest = json.loads(manifest_path.read_text())
    if build(manifest['release'])['files'] != manifest['files']:
        raise SystemExit('serve/RELEASE_MANIFEST.json is stale: run python3 tools/update_manifest.py')
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name in sorted(manifest['files']) + ['RELEASE_MANIFEST.json']:
            info = archive.gettarinfo(str(SERVE / name), arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mode = 0o600
            with (SERVE / name).open('rb') as handle:
                archive.addfile(info, handle)
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()[:16], buffer.getvalue()


def sync(args):
    """Upload this release once; later calls find it by the manifest hash. Returns its remote path."""
    release, data = release_archive()
    path = STATE + '/releases/' + release
    remote(args, 'test -f {0}/RELEASE_MANIFEST.json || {{ rm -rf {0}.tmp && mkdir -p {0}.tmp && '
                 'tar -xzf - -C {0}.tmp && mv {0}.tmp {0}; }}'.format(path), data=data)
    return path


def inference(args, release, *words, data=None, capture=False):
    return remote(args, 'python3 {}/inference.py {}'.format(release, ' '.join(shlex.quote(w) for w in words)),
                  data=data, capture=capture)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--user', default=os.environ.get('LEONARDO_USER'), help='your CINECA login (LEONARDO_USER)')
    parser.add_argument('--host', default=os.environ.get('LEONARDO_HOST', 'login01-ext.leonardo.cineca.it'))
    parser.add_argument('--base', default=os.environ.get('LEONARDO_BASE', '$WORK/team_vllm'),
                        help="team folder on Leonardo, expanded there (LEONARDO_BASE, default '$WORK/team_vllm')")
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('up', help='prepare and submit a model server')
    p.add_argument('profile')
    p.add_argument('--qos', help='e.g. boost_qos_dbg for a quick 30-minute test')
    p.add_argument('--time', help='e.g. 00:30:00')
    p.add_argument('--reservation')
    sub.add_parser('status', help='your servers and gateway, and the team registry')
    p = sub.add_parser('down', help='cancel servers by profile, model or job id, or "gateway"')
    p.add_argument('target')
    p = sub.add_parser('gateway', help='manage your gateway job')
    p.add_argument('action', choices=['up', 'down', 'token'])
    p.add_argument('--hours', type=float, default=12, help='keep the gateway alive this long (default 12)')
    p.add_argument('--restart', action='store_true')
    p.add_argument('--token-file', help='install this gateway token first (e.g. read from the Kubernetes secret)')
    p.add_argument('--tunnel-auth-file', help='install the chisel user:password for the outbound tunnel first')
    p = sub.add_parser('tunnel', help='forward a local port to your gateway through the login node')
    p.add_argument('--local-port', type=int, default=18000)
    sub.add_parser('sync', help='only upload this release')
    args = parser.parse_args()
    if not args.user:
        parser.error('set --user or LEONARDO_USER to your own CINECA login')

    release = sync(args)
    if args.command == 'sync':
        print('Release on Leonardo: ' + release.replace(STATE, '~/.leonardo-inference'))
    elif args.command == 'up':
        words = ['up', args.profile, '--base', args.base]
        for flag in ('qos', 'time', 'reservation'):
            if getattr(args, flag):
                words += ['--' + flag, getattr(args, flag)]
        inference(args, release, *words)
    elif args.command == 'status':
        inference(args, release, 'status', '--base', args.base)
    elif args.command == 'down':
        inference(args, release, 'down', args.target)
    elif args.command == 'gateway' and args.action == 'down':
        inference(args, release, 'down', 'gateway')
    elif args.command == 'gateway' and args.action == 'token':
        inference(args, release, 'gateway-token', 'ensure')
        sys.stdout.write(inference(args, release, 'gateway-token', 'show', capture=True).decode())
    elif args.command == 'gateway':
        if args.token_file:
            inference(args, release, 'gateway-token', 'set', data=Path(args.token_file).read_bytes())
        if args.tunnel_auth_file:
            remote(args, 'mkdir -p {0} && chmod 700 {0} && cat > {0}/tunnel_auth'.format(STATE),
                   data=Path(args.tunnel_auth_file).read_bytes())
        words = ['gateway-up', '--base', args.base, '--hours', str(args.hours)] + (['--restart'] if args.restart else [])
        inference(args, release, *words)
    elif args.command == 'tunnel':
        report = json.loads(inference(args, release, 'status', '--json', capture=True).decode())
        endpoint = (report.get('gateway') or {}).get('endpoint')
        if not endpoint:
            raise SystemExit('Your gateway is not listening yet; start it with: gateway up')
        forward = '127.0.0.1:{}:{}:{}'.format(args.local_port, endpoint['host'], endpoint['port'])
        print('Gateway at http://127.0.0.1:{}/v1 (Bearer: ./cli.py gateway token). Ctrl-C to stop.'.format(args.local_port), flush=True)
        os.execvp('ssh', ssh_command(args)[:-1] + ['-N', '-o', 'ExitOnForwardFailure=yes', '-L', forward, ssh_command(args)[-1]])


if __name__ == '__main__':
    main()
