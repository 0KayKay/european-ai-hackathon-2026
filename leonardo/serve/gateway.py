"""One authenticated entry point for all of your model servers, run as its own Slurm job.

The gateway finds your servers through their private run directories (never through the shared
registry, which anyone in the project could write), routes each request by its "model" field and
swaps the caller's gateway token for that server's own key. Callers never see a server key.

Optionally (site switch "outbound_tunnel", off by default) it also dials out to a chisel server in
Kubernetes, so the cluster reaches the gateway without anyone logging in to Leonardo from there.
"""
import argparse
import datetime
import getpass
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
from socketserver import ThreadingMixIn
import subprocess
import sys
import tempfile
import threading
import time

import inference
from inference import ROOT, load, save

STATE = Path.home() / '.leonardo-inference'
DIRECTORY = STATE / 'gateway'
TOKEN = STATE / 'gateway_token'
TUNNEL_AUTH = STATE / 'tunnel_auth'
JOB_NAME = 'leonardo-gateway'
DEFAULTS = {'partition': 'lrd_all_serial', 'qos': None, 'time': '04:00:00', 'cpus': 2, 'memory': '8000M',
            'outbound_tunnel': False, 'tunnel_server': None, 'tunnel_fingerprint': None,
            'tunnel_proxy': None, 'tunnel_remote_port': 9000}
MAX_BODY = 16 * 1024 * 1024
UPSTREAM_TIMEOUT = 900
HOP_BY_HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
              'transfer-encoding', 'upgrade', 'server', 'date'}


def write_private(path, text):
    inference.private_dir(path.parent)
    temporary = path.with_name('.' + path.name + '.tmp')
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(text)
    os.replace(str(temporary), str(path))


def read_private(path):
    mode = path.lstat()
    if mode.st_uid != os.getuid() or mode.st_mode & 0o077 or not path.is_file():
        raise ValueError('{} must be a private file owned by this account'.format(path))
    return path.read_text().strip()


def token(args):
    if args.action == 'set':
        value = sys.stdin.readline().strip()
        if len(value) < 32:
            raise ValueError('Refusing a gateway token shorter than 32 characters')
        write_private(TOKEN, value + '\n')
        print('Gateway token replaced. A running gateway keeps the old one until restarted.')
    elif args.action == 'ensure':
        if not TOKEN.exists():
            write_private(TOKEN, secrets.token_urlsafe(32) + '\n')
            print('Created a new gateway token in ' + str(TOKEN), file=sys.stderr)
    else:
        print(read_private(TOKEN))


def settings(site):
    merged = dict(DEFAULTS)
    merged.update(site.get('gateway', {}))
    return merged


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def job_command(cfg):
    g, site = cfg['settings'], cfg['site']
    account = os.environ.get('SBATCH_ACCOUNT') or site['account']
    if account == 'YOUR_PROJECT':
        raise ValueError('Set a valid project allocation in the site configuration')
    command = ['sbatch', '--parsable', '--job-name=' + JOB_NAME, '--dependency=singleton',
               '--account=' + inference.identifier(account), '--partition=' + inference.identifier(g['partition']),
               '--nodes=1', '--ntasks=1', '--cpus-per-task=' + str(int(g['cpus'])),
               '--mem=' + g['memory'], '--time=' + inference.time_limit(g['time']),
               '--signal=B:TERM@60', '--output=%x-%j.out', '--chdir=' + str(DIRECTORY)]
    if g['qos']:
        command.append('--qos=' + inference.identifier(g['qos']))
    return command + [str(DIRECTORY / 'gateway.sbatch')]


def seconds(limit):
    days, _, clock = limit.rpartition('-')
    h, m, s = map(int, clock.split(':'))
    return int(days or 0) * 86400 + h * 3600 + m * 60 + s


def up(args):
    manifest = inference.verify_release()
    base = inference.site_base(args.base)
    site = inference.load_site(base)
    g = settings(site)
    current = state(inference.active_jobs())
    if current and current['jobs']:
        if not args.restart:
            raise ValueError('A gateway is already running ({}); use --restart to replace it'.format(current['summary']))
        down()
    if g['outbound_tunnel']:
        if not (g['tunnel_server'] and g['tunnel_fingerprint']):
            raise ValueError('outbound_tunnel needs tunnel_server and tunnel_fingerprint in the site gateway settings')
        read_private(TUNNEL_AUTH)
    token(argparse.Namespace(action='ensure'))
    inference.private_dir(STATE)
    staging = Path(tempfile.mkdtemp(prefix='.gateway-', dir=str(STATE)))
    for filename in manifest['files']:
        destination = staging / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(ROOT / filename), str(destination))
    shutil.copyfile(str(ROOT / 'RELEASE_MANIFEST.json'), str(staging / 'RELEASE_MANIFEST.json'))
    inference.private_dir(staging / '.runtime')
    if g['outbound_tunnel']:
        # Private, verified copy: the shared tools folder could be changed after this check.
        tool = inference.catalog()['tools']['chisel']
        source = base / 'tools' / ('chisel-' + tool['version'])
        if not source.is_file() or sha256(source) != tool['sha256']:
            shutil.rmtree(str(staging))
            raise ValueError('{} is missing or does not match catalog.json; run install/04_install_chisel.sbatch'.format(source))
        shutil.copyfile(str(source), str(staging / 'chisel'))
        (staging / 'chisel').chmod(0o700)
    cfg = {'schema_version': 1, 'base': str(base), 'site': site, 'settings': g, 'release': manifest['release'],
           'until': time.time() + args.hours * 3600}
    save(staging / 'GATEWAY.json', cfg)
    if DIRECTORY.exists():
        shutil.rmtree(str(DIRECTORY))
    os.rename(str(staging), str(DIRECTORY))
    # SLURM_SUBMIT_DIR must be the gateway directory: gateway.sbatch starts with cd into it.
    job = inference.sbatch(job_command(cfg), DIRECTORY / '.runtime/submitting', cwd=str(DIRECTORY))
    print('Submitted gateway job {}; it resubmits itself until {}.'.format(
        job, datetime.datetime.fromtimestamp(cfg['until']).isoformat(timespec='minutes')))
    print('Outbound tunnel: ' + ('ON to ' + g['tunnel_server'] if g['outbound_tunnel'] else 'off (site setting)'))


def next_job(args):
    """Called at the start of each gateway job: queue the successor, which starts when this one ends."""
    cfg = load(DIRECTORY / 'GATEWAY.json')
    job = os.environ['SLURM_JOB_ID']
    if time.time() + seconds(cfg['settings']['time']) >= cfg['until']:
        print('Gateway chain ends with this job (until reached).')
        return
    successor = inference.sbatch(job_command(cfg), DIRECTORY / ('.runtime/next-' + job), cwd=str(DIRECTORY))
    print('Queued successor gateway job ' + successor)


def down():
    subprocess.call(['scancel', '--name=' + JOB_NAME, '--user=' + getpass.getuser()])
    print('Cancelled all {} jobs of {}.'.format(JOB_NAME, getpass.getuser()))


def state(jobs):
    """Your gateway jobs and endpoint, or None when there is no trace of one."""
    mine = [j for j in (jobs or {}).values() if j['name'] == JOB_NAME]
    endpoint = None
    if (DIRECTORY / '.runtime/endpoint.json').exists():
        endpoint = load(DIRECTORY / '.runtime/endpoint.json')
        if jobs is not None and endpoint['job_id'] not in jobs:
            endpoint = None
    if not mine and not endpoint:
        return None
    parts = ['{} {}'.format(j['job_id'], j['state']) for j in mine]
    if endpoint:
        parts.append('listening on {}:{}, tunnel {}'.format(endpoint['host'], endpoint['port'],
                                                         'on' if endpoint['tunnel'] else 'off'))
    return {'jobs': mine, 'endpoint': endpoint, 'summary': '; '.join(parts)}


class Routes:
    """Model name -> your newest ready server, rescanned at most every 10 seconds."""

    def __init__(self):
        self.lock = threading.Lock()
        self.table, self.scanned, self.jobs, self.jobs_at = {}, 0, None, 0

    def current(self):
        with self.lock:
            now = time.monotonic()
            if now - self.jobs_at > 30:
                self.jobs, self.jobs_at = inference.active_jobs(), now
            if now - self.scanned > 10:
                table = {}
                for run in reversed(inference.own_runs()):  # oldest first, so newer runs win
                    endpoint = run['endpoint']
                    if not endpoint or (self.jobs is not None and endpoint['job_id'] not in self.jobs):
                        continue
                    try:
                        key = read_private(run['run'] / '.runtime/api_key')
                    except (OSError, ValueError):
                        continue
                    table[endpoint['model']] = {'host': endpoint['host'], 'port': int(endpoint['port']),
                                                'key': key, 'job_id': endpoint['job_id']}
                self.table, self.scanned = table, now
            return self.table


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def handler(routes, gateway_token):
    expected = gateway_token.encode()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'leonardo-gateway'

        def log_message(self, *args):
            pass

        def log(self, status, model=None, started=None):
            print('{} {} {} {} {}'.format(self.command, self.path, model or '-', status,
                                          '{:.1f}s'.format(time.monotonic() - started) if started else ''), flush=True)

        def reply(self, status, obj):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.log(status)

        def error(self, status, message, code):
            self.reply(status, {'error': {'message': message, 'type': 'invalid_request_error', 'code': code}})

        def authorized(self):
            header = self.headers.get('Authorization', '')
            if header.startswith('Bearer ') and hmac.compare_digest(header[7:].strip().encode(), expected):
                return True
            self.error(401, 'Missing or wrong gateway token', 'unauthorized')
            return False

        def do_GET(self):
            if self.path == '/health':
                return self.reply(200, {'status': 'ok'})
            if not self.authorized():
                return
            if self.path in ('/v1/models', '/models'):
                models = sorted(routes.current())
                return self.reply(200, {'object': 'list', 'data': [
                    {'id': m, 'object': 'model', 'owned_by': 'leonardo'} for m in models]})
            self.error(404, 'Unknown path', 'not_found')

        def body(self):
            if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
                parts, total = [], 0
                while True:
                    size = int(self.rfile.readline().split(b';')[0], 16)
                    if size == 0:
                        while self.rfile.readline() not in (b'\r\n', b'\n', b''):
                            pass
                        return b''.join(parts)
                    total += size
                    if total > MAX_BODY:
                        return None
                    parts.append(self.rfile.read(size))
                    self.rfile.readline()
            length = int(self.headers.get('Content-Length') or 0)
            return None if length > MAX_BODY else self.rfile.read(length)

        def do_POST(self):
            if not self.authorized():
                return
            if not self.path.startswith('/') or '..' in self.path:
                return self.error(400, 'Invalid path', 'invalid_path')
            data = self.body()
            if data is None:
                return self.error(413, 'Request body too large', 'too_large')
            try:
                model = json.loads(data.decode()).get('model')
            except (ValueError, AttributeError):
                return self.error(400, 'Body must be a JSON object with a "model" field', 'invalid_json')
            route = routes.current().get(model)
            if route is None:
                return self.error(404, 'Model {} is not running on Leonardo right now'.format(model), 'model_not_found')
            self.forward(route, model, data)

        def forward(self, route, model, data):
            started = time.monotonic()
            connection = http.client.HTTPConnection(route['host'], route['port'], timeout=UPSTREAM_TIMEOUT)
            headers = {'Content-Type': self.headers.get('Content-Type', 'application/json'),
                       'Accept': self.headers.get('Accept', '*/*'), 'Authorization': 'Bearer ' + route['key']}
            try:
                connection.request('POST', self.path, data, headers)
                response = connection.getresponse()
            except (OSError, http.client.HTTPException) as error:
                connection.close()
                return self.error(502, 'Leonardo server for {} unreachable: {}'.format(model, type(error).__name__), 'upstream_unreachable')
            try:
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in HOP_BY_HOP:
                        self.send_header(name, value)
                self.send_header('Connection', 'close')
                self.end_headers()
                # read1 returns as soon as data arrives, so server-sent events stream through.
                while True:
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, http.client.HTTPException):
                pass  # the caller or the server went away mid-stream
            finally:
                connection.close()
                self.log(response.status, model, started)

    return Handler


def tunnel_command(cfg, port):
    g = cfg['settings']
    command = [str(DIRECTORY / 'chisel'), 'client', '--fingerprint', g['tunnel_fingerprint'], '--keepalive', '25s']
    if g['tunnel_proxy']:
        command += ['--proxy', g['tunnel_proxy']]
    return command + [g['tunnel_server'], 'R:0.0.0.0:{}:127.0.0.1:{}'.format(int(g['tunnel_remote_port']), port)]


def serve(args):
    os.umask(0o077)
    cfg = load(DIRECTORY / 'GATEWAY.json')
    port = int(os.environ['GATEWAY_PORT'])
    server = Server(('0.0.0.0', port), handler(Routes(), read_private(TOKEN)))
    tunnel = None
    if cfg['settings']['outbound_tunnel']:
        # chisel reads AUTH from the environment, which keeps the password out of the process list.
        environment = dict(os.environ, AUTH=read_private(TUNNEL_AUTH))
        tunnel = subprocess.Popen(tunnel_command(cfg, port), env=environment)
    endpoint = {'job_id': os.environ.get('SLURM_JOB_ID', 'local'), 'host': os.uname().nodename.split('.')[0],
                'port': port, 'tunnel': tunnel is not None, 'release': cfg['release'],
                'started': datetime.datetime.now().astimezone().isoformat()}
    save(DIRECTORY / '.runtime/endpoint.json', endpoint)
    print('GATEWAY_READY', json.dumps(endpoint), flush=True)

    def stop(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if tunnel is not None and tunnel.poll() is None:
            tunnel.terminate()
            tunnel.wait()
        path = DIRECTORY / '.runtime/endpoint.json'
        if path.exists() and load(path)['job_id'] == endpoint['job_id']:
            path.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['serve'])
    try:
        serve(parser.parse_args())
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        raise SystemExit(str(error))
