"""Cluster link for one B300 run: agentgateway in GKE reaches this run's models through an outbound tunnel.

Same contract as Leonardo's router (leonardo/serve/router.py), so infrastructure/leonardo works unchanged:
agentgateway calls http://leonardo-router:8080/v1 with the fixed router token, and the chisel server in GKE
forwards that port (9000) through the tunnel to a relay here. The relay checks the token, picks the model
server by the request's "model" field and calls it with that server's per-run key.

run.sbatch starts this on the batch host for runs submitted with `up --link`. It waits for the run's
endpoint, starts the relay on 127.0.0.1 and the chisel client, and stops when the endpoint disappears
(server stopped) or on SIGTERM. Slurm ends it with the job in any case. State: RUN/link-state.json.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import signal
from socketserver import ThreadingMixIn
import subprocess
import sys
import threading
import time
import urllib.request

# Same pinned client as leonardo/serve/catalog.json "tools".
CHISEL = {'version': '1.13.0',
          'url': 'https://github.com/jpillora/chisel/releases/download/v1.13.0/chisel_1.13.0_linux_amd64.gz',
          'sha256_gz': '902a38e824bfb4d4e428faa45607a9b71fc74ff28bf1392cf8121650061c6971',
          'sha256': 'b34230fc7f316d77335d59ccff3b4824298c4297ffd9d1d65f9813e01af4ebb1'}
MAX_BODY = 16 * 1024 * 1024
UPSTREAM_TIMEOUT = 900
HOP_BY_HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
              'transfer-encoding', 'upgrade', 'content-length'}
RESTART_DELAYS = (5, 10, 30, 60, 300)
POLL_SECONDS = 10


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_private(path):
    path = Path(path)
    mode = path.lstat()
    if path.is_symlink() or mode.st_uid != os.getuid() or mode.st_mode & 0o077:
        raise ValueError('Must be private (mode 600) and owned by this account: ' + str(path))
    value = path.read_text().strip()
    if not value:
        raise ValueError('Empty private file: ' + str(path))
    return value


def chisel_binary(link_dir):
    """The pinned chisel client in the private link folder; downloaded and verified on first use."""
    target = Path(link_dir) / ('chisel-' + CHISEL['version'])
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == CHISEL['sha256']:
        return target
    with urllib.request.urlopen(CHISEL['url'], timeout=120) as response:
        packed = response.read()
    if hashlib.sha256(packed).hexdigest() != CHISEL['sha256_gz']:
        raise ValueError('chisel download checksum mismatch')
    data = gzip.decompress(packed)
    if hashlib.sha256(data).hexdigest() != CHISEL['sha256']:
        raise ValueError('chisel binary checksum mismatch')
    partial = target.with_name(target.name + '.partial')
    partial.write_bytes(data)
    partial.chmod(0o700)
    partial.replace(target)
    return target


def routes(run):
    """Served model name -> this run's server address and key, from the published endpoint."""
    endpoint = json.loads((run / 'endpoint.json').read_text())
    table = {}
    for kind, entry in endpoint['models'].items():
        table[entry['model']] = {'host': endpoint['host_ip'], 'port': int(entry['port']),
                                 'key': read_private(run / (kind + '.api-key'))}
    return endpoint['job_id'], table


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def handler(table, token, state):
    expected = token.encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, obj):
            data = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(data)

        def error(self, status, message, code):
            self.reply(status, {'error': {'message': message, 'type': 'invalid_request_error', 'code': code}})

        def authorized(self):
            header = self.headers.get('Authorization', '')
            if header.startswith('Bearer ') and hmac.compare_digest(header[7:].strip().encode(), expected):
                return True
            self.error(401, 'Missing or wrong router token', 'unauthorized')
            return False

        def do_GET(self):
            if self.path == '/health':
                return self.reply(200, {'status': 'ok', 'cluster_link': state()})
            if not self.authorized():
                return
            if self.path in ('/v1/models', '/models'):
                return self.reply(200, {'object': 'list', 'data': [
                    {'id': m, 'object': 'model', 'owned_by': 'b300'} for m in sorted(table)]})
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
            if not self.path.startswith('/v1/') or '..' in self.path:
                return self.error(400, 'Invalid path', 'invalid_path')
            data = self.body()
            if data is None:
                return self.error(413, 'Request body too large', 'too_large')
            try:
                model = json.loads(data.decode()).get('model')
            except (ValueError, AttributeError):
                return self.error(400, 'Body must be a JSON object with a "model" field', 'invalid_json')
            route = table.get(model)
            if route is None:
                return self.error(404, 'Model {} is not served by this B300 run'.format(model), 'model_not_found')
            connection = http.client.HTTPConnection(route['host'], route['port'], timeout=UPSTREAM_TIMEOUT)
            headers = {'Content-Type': self.headers.get('Content-Type', 'application/json'),
                       'Accept': self.headers.get('Accept', '*/*'), 'Authorization': 'Bearer ' + route['key']}
            try:
                connection.request('POST', self.path, data, headers)
                response = connection.getresponse()
            except (OSError, http.client.HTTPException) as error:
                connection.close()
                return self.error(502, 'B300 server for {} unreachable: {}'.format(model, type(error).__name__),
                                  'upstream_unreachable')
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

    return Handler


class Tunnel:
    """The chisel client: chisel retries by itself; this reads its output for the state and restarts it
    if the process exits. States: connecting, connected, retrying (with the error), restarting."""

    def __init__(self, command, environment, on_change):
        self.command, self.environment, self.on_change = command, environment, on_change
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.process = None
        self.current = {'state': 'connecting', 'error': None, 'since': now()}
        self.thread = threading.Thread(target=self.run, daemon=True)

    def set(self, state, error=None):
        with self.lock:
            if (state, error) == (self.current['state'], self.current['error']):
                return
            self.current = {'state': state, 'error': error, 'since': now()}
        self.on_change()

    def read(self, line):
        """chisel 1.13 client lines, e.g. "2026/10/20 21:45:38 client: Connected (Latency 868µs)"."""
        message = line.split(' client: ', 1)[-1].strip()
        if message.startswith('Connected'):
            self.set('connected')
        elif message.startswith('Connection error: '):
            self.set('retrying', re.sub(r' \(Attempt: [^)]*\)$', '', message[len('Connection error: '):]))
        elif message == 'Disconnected':
            self.set('retrying', 'disconnected')

    def run(self):
        failures = 0
        while not self.stopping.is_set():
            started = time.monotonic()
            self.set('connecting')
            with self.lock:
                if self.stopping.is_set():
                    return
                try:
                    self.process = subprocess.Popen(self.command, env=self.environment, stdout=subprocess.PIPE,
                                                    stderr=subprocess.STDOUT)
                except OSError as error:  # e.g. a noexec filesystem: say so instead of hanging in "connecting"
                    self.current = {'state': 'failed', 'error': 'cannot start chisel: {}'.format(error), 'since': now()}
                    self.process = None
            if self.process is None:
                self.on_change()
                return
            with self.process.stdout:
                for line in self.process.stdout:
                    sys.stdout.buffer.write(line)
                    sys.stdout.flush()
                    self.read(line.decode('utf-8', 'replace'))
            code = self.process.wait()
            if self.stopping.is_set():
                return
            failures = 1 if time.monotonic() - started > 600 else failures + 1
            delay = RESTART_DELAYS[min(failures, len(RESTART_DELAYS)) - 1]
            self.set('restarting', 'chisel exited with code {}; restarting in {} s'.format(code, delay))
            self.stopping.wait(delay)

    def stop(self):
        self.stopping.set()
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        self.thread.join(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--link-dir', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    run, link_dir = args.run, args.link_dir
    status = {'state': 'waiting_for_models', 'error': None, 'since': now()}
    written = threading.Lock()

    def publish(**fields):
        with written:
            status.update(fields, updated=now())
            temporary = run / 'link-state.json.tmp'
            temporary.write_text(json.dumps(status, indent=2) + '\n')
            temporary.replace(run / 'link-state.json')
            print('LINK_STATE ' + json.dumps({k: status.get(k) for k in ('state', 'error')}), flush=True)

    stopping = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopping.set())
    publish()
    try:
        settings = json.loads(read_private(link_dir / 'link.json'))
        token = read_private(link_dir / 'router_token')
        auth = read_private(link_dir / 'link_auth')
        binary = chisel_binary(link_dir)
        # Wait for the server to publish its checked endpoint; give up if the run ends first.
        while not (run / 'endpoint.json').exists():
            phase = json.loads((run / 'state.json').read_text()).get('phase') if (run / 'state.json').exists() else None
            if phase in ('failed', 'stopped', 'installed') or stopping.wait(POLL_SECONDS):
                publish(state='off', error='run ended before its models were ready')
                return
        job_id, table = routes(run)
        holder = {}

        def link_state():
            return holder['tunnel'].current['state'] if 'tunnel' in holder else 'connecting'
        relay = Server(('127.0.0.1', 0), handler(table, token, link_state))
        port = relay.server_address[1]
        threading.Thread(target=relay.serve_forever, daemon=True).start()
        command = [str(binary), 'client', '--fingerprint', settings['fingerprint'],
                   '--keepalive', '25s', '--max-retry-interval', '30s']
        if settings.get('proxy'):
            command += ['--proxy', settings['proxy']]
        command += [settings['server'], 'R:0.0.0.0:{}:127.0.0.1:{}'.format(int(settings.get('remote_port', 9000)), port)]
        # chisel reads AUTH from the environment: the password never appears in the process list.
        tunnel = holder['tunnel'] = Tunnel(command, dict(os.environ, AUTH=auth), on_change=lambda: publish(
            models=sorted(table), server=settings['server'], **holder['tunnel'].current))
        tunnel.thread.start()
        # Serve until the run withdraws its endpoint (server stopped or failed) or the job ends.
        while not stopping.wait(POLL_SECONDS):
            try:
                if json.loads((run / 'endpoint.json').read_text())['job_id'] != job_id:
                    break
            except (OSError, ValueError):
                break
        tunnel.stop()
        relay.shutdown()
        publish(state='off', error=None)
    except Exception as error:
        publish(state='failed', error='{}: {}'.format(type(error).__name__, error))
        raise


if __name__ == '__main__':
    main()
