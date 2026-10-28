"""Cluster link contracts with a fake vLLM server and a stand-in chisel client; never GKE or GPU evidence."""
import contextlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'serve'))
import common
import link
import manage

TOKEN = 'router-token-' + 'x' * 32
KEY = 'per-run-model-key'


class FakeVllm(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        FakeVllm.seen.append((self.path, self.headers['Authorization'], json.loads(body)))
        if self.headers['Authorization'] != 'Bearer ' + KEY:
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for part in ('data: {"a":1}\n\n', 'data: [DONE]\n\n'):
            self.wfile.write(part.encode())
            self.wfile.flush()


def serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def call(port, path, token=None, body=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request('http://127.0.0.1:%s%s' % (port, path), data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


class Relay(unittest.TestCase):
    def setUp(self):
        FakeVllm.seen = []
        self.upstream = serve(HTTPServer(('127.0.0.1', 0), FakeVllm))
        table = {'glm-5.3-flash-leonardo': {'host': '127.0.0.1', 'port': self.upstream.server_address[1], 'key': KEY}}
        self.relay = serve(link.Server(('127.0.0.1', 0), link.handler(table, TOKEN, lambda: 'connected')))
        self.port = self.relay.server_address[1]

    def tearDown(self):
        for server in (self.relay, self.upstream):
            server.shutdown()
            server.server_close()

    def test_health_needs_no_token_and_shows_only_the_state(self):
        status, body = call(self.port, '/health')
        self.assertEqual((status, json.loads(body)), (200, {'status': 'ok', 'cluster_link': 'connected'}))

    def test_router_token_is_required(self):
        body = {'model': 'glm-5.3-flash-leonardo', 'messages': []}
        self.assertEqual(call(self.port, '/v1/chat/completions', None, body)[0], 401)
        self.assertEqual(call(self.port, '/v1/chat/completions', 'wrong', body)[0], 401)
        self.assertEqual(call(self.port, '/v1/models', 'wrong')[0], 401)
        self.assertEqual(FakeVllm.seen, [])

    def test_models_lists_this_run(self):
        status, body = call(self.port, '/v1/models', TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual([m['id'] for m in json.loads(body)['data']], ['glm-5.3-flash-leonardo'])

    def test_request_reaches_the_model_with_its_own_key_and_streams_back(self):
        body = {'model': 'glm-5.3-flash-leonardo', 'messages': [{'role': 'user', 'content': 'Hi'}], 'stream': True}
        status, text = call(self.port, '/v1/chat/completions', TOKEN, body)
        self.assertEqual(status, 200)
        self.assertEqual(text, 'data: {"a":1}\n\ndata: [DONE]\n\n')
        path, authorization, received = FakeVllm.seen[0]
        self.assertEqual((path, authorization, received), ('/v1/chat/completions', 'Bearer ' + KEY, body))

    def test_unknown_model_and_bad_requests(self):
        status, text = call(self.port, '/v1/chat/completions', TOKEN, {'model': 'qwen3-0.6b-leonardo'})
        self.assertEqual((status, json.loads(text)['error']['code']), (404, 'model_not_found'))
        self.assertEqual(call(self.port, '/v1/chat/completions', TOKEN, ['not', 'an', 'object'])[0], 400)
        self.assertEqual(call(self.port, '/admin', TOKEN, {'model': 'glm-5.3-flash-leonardo'})[0], 400)

    def test_unreachable_model_server_is_502(self):
        self.upstream.shutdown()
        self.upstream.server_close()
        status, text = call(self.port, '/v1/chat/completions', TOKEN, {'model': 'glm-5.3-flash-leonardo'})
        self.assertEqual((status, json.loads(text)['error']['code']), (502, 'upstream_unreachable'))


class TunnelState(unittest.TestCase):
    def test_chisel_output_maps_to_states(self):
        changes = []
        tunnel = link.Tunnel([], {}, lambda: changes.append(dict(tunnel.current)))
        tunnel.read('2026/10/28 10:00:00 client: Connecting to wss://example/leonardo-link\n')
        self.assertEqual(tunnel.current['state'], 'connecting')
        tunnel.read('2026/10/28 10:00:01 client: Connected (Latency 868µs)\n')
        self.assertEqual(tunnel.current['state'], 'connected')
        tunnel.read('2026/10/28 10:00:09 client: Connection error: Invalid fingerprint (Attempt: 2)\n')
        self.assertEqual((tunnel.current['state'], tunnel.current['error']), ('retrying', 'Invalid fingerprint'))
        tunnel.read('2026/10/28 10:00:09 client: Connection error: Invalid fingerprint (Attempt: 3)\n')
        self.assertEqual(len(changes), 2)  # repeated errors are one change


    def test_a_chisel_that_cannot_start_is_reported(self):
        changes = []
        tunnel = link.Tunnel(['/nonexistent/chisel'], {}, lambda: changes.append(dict(tunnel.current)))
        tunnel.run()
        self.assertEqual(changes[-1]['state'], 'failed')
        self.assertIn('cannot start chisel', changes[-1]['error'])


class Settings(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        common.save(self.base / 'b300-site.json', {'account': 'project', 'partition': 'hackathon', 'qos': 'team'})

    def tearDown(self):
        self.directory.cleanup()

    def setup_link(self):
        return manage.link(self.base, {'link_action': 'setup', 'server': 'https://example.org/leonardo-link',
                                       'fingerprint': 'A' * 43 + '='}, 'leonardo:secret')

    def test_setup_and_token_are_private(self):
        self.setup_link()
        token = manage.link(self.base, {'link_action': 'token'}, None)['token']
        self.assertEqual(manage.link(self.base, {'link_action': 'token'}, None)['token'], token)
        folder = manage.link_dir(self.base)
        for name in ('link.json', 'link_auth', 'router_token'):
            self.assertEqual((folder / name).stat().st_mode & 0o777, 0o600, name)
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual(link.read_private(folder / 'link_auth'), 'leonardo:secret')
        given = 'given-' + 'y' * 40
        self.assertEqual(manage.link(self.base, {'link_action': 'token'}, given)['token'], given)

    def test_setup_rejects_bad_values(self):
        good = {'link_action': 'setup', 'server': 'https://example.org/leonardo-link', 'fingerprint': 'A' * 44}
        for change, secret in (({'server': 'http://example.org/x'}, 'u:p'), ({'fingerprint': 'short'}, 'u:p'),
                               ({}, 'no-password'), ({}, None), ({'proxy': 'ftp://proxy'}, 'u:p')):
            with self.assertRaises(ValueError):
                manage.link(self.base, dict(good, **change), secret)

    def test_up_link_requires_settings_and_records_them(self):
        with patch.object(manage, 'jobs', return_value={}), \
                patch.object(manage, 'execute', return_value='4242\n'):
            with self.assertRaisesRegex(ValueError, 'not configured'):
                manage.start(self.base, {'profile': 'glm53', 'mode': 'serve', 'link': True})
            self.assertFalse((manage.mine(self.base) / 'runs').exists())
            self.setup_link()
            manage.link(self.base, {'link_action': 'token'}, None)
            with self.assertRaisesRegex(ValueError, 'up only'):
                manage.start(self.base, {'profile': 'glm53', 'mode': 'install', 'link': True})
            result = manage.start(self.base, {'profile': 'glm53', 'mode': 'serve', 'link': True})
        config = common.load(manage.mine(self.base) / 'runs' / result['run_id'] / 'RUN_CONFIG.json')
        self.assertEqual(config['link_dir'], str(manage.link_dir(self.base)))
        with patch.object(manage, 'jobs', return_value={'4242': {'state': 'RUNNING', 'uid': os.getuid()}}), \
                patch.object(manage, 'execute', return_value='4243\n'):
            with self.assertRaisesRegex(ValueError, 'already holds the cluster link'):
                manage.start(self.base, {'profile': 'safety', 'mode': 'serve', 'link': True})
            unlinked = manage.start(self.base, {'profile': 'safety', 'mode': 'serve'})
            self.assertNotIn('link_dir', common.load(manage.mine(self.base) / 'runs' / unlinked['run_id'] / 'RUN_CONFIG.json'))
            rows = {r['profile']: r for r in manage.status(self.base)['runs']}
        self.assertIn('cluster_link', rows['glm53'])
        self.assertNotIn('cluster_link', rows['safety'])


FAKE_CHISEL = '''#!/bin/sh
printf '%s\\n' "$AUTH" "$@" > "$(dirname "$0")/argv"
echo "2026/10/28 10:00:00 client: Connected (Latency 1ms)"
exec sleep 60
'''


class EndToEnd(unittest.TestCase):
    def test_link_waits_for_the_endpoint_relays_and_stops_with_the_run(self):
        FakeVllm.seen = []
        upstream = serve(HTTPServer(('127.0.0.1', 0), FakeVllm))
        with tempfile.TemporaryDirectory() as directory:
            run, folder = Path(directory) / 'run', common.private(Path(directory) / 'link')
            common.private(run)
            fake = folder / 'fake-chisel'
            fake.write_text(FAKE_CHISEL)
            fake.chmod(0o700)
            for name, value in (('link_auth', 'leonardo:secret'), ('router_token', TOKEN), ('link.json', json.dumps(
                    {'server': 'https://example.org/leonardo-link', 'fingerprint': 'F' * 44, 'remote_port': 9000}))):
                manage.write_secret(folder / name, value)
            common.save(run / 'state.json', {'phase': 'loading_glm'})

            def drive():
                time.sleep(0.5)  # the link must wait for the published endpoint
                manage.write_secret(run / 'glm.api-key', KEY)
                common.save(run / 'endpoint.json', {'job_id': '7', 'host_ip': '127.0.0.1', 'models': {'glm': {
                    'port': upstream.server_address[1], 'model': 'glm-5.3-flash-leonardo'}}})
                for _ in range(100):
                    if (folder / 'argv').exists() and 'connected' in (run / 'link-state.json').read_text():
                        break
                    time.sleep(0.1)
                argv = (folder / 'argv').read_text().split('\n')
                self.results['argv'] = argv
                port = argv[-2].rsplit(':', 1)[1]
                self.results['call'] = call(port, '/v1/chat/completions', TOKEN, {'model': 'glm-5.3-flash-leonardo'})
                (run / 'endpoint.json').unlink()  # the server stopped

            self.results = {}
            thread = threading.Thread(target=drive)
            thread.start()
            with patch.object(link, 'chisel_binary', return_value=fake), patch.object(link, 'POLL_SECONDS', 0.2), \
                    patch.object(sys, 'argv', ['link.py', '--run', str(run), '--link-dir', str(folder)]), \
                    contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO())):
                link.main()
            thread.join()
            upstream.shutdown()
            upstream.server_close()
            argv = self.results['argv']
            self.assertEqual(argv[0], 'leonardo:secret')  # AUTH via the environment
            self.assertNotIn('leonardo:secret', argv[1:])
            self.assertIn('F' * 44, argv)
            self.assertIn('https://example.org/leonardo-link', argv)
            self.assertRegex(argv[-2], r'^R:0\.0\.0\.0:9000:127\.0\.0\.1:\d+$')
            self.assertEqual(self.results['call'][0], 200)
            self.assertEqual(FakeVllm.seen[0][1], 'Bearer ' + KEY)
            self.assertEqual(common.load(run / 'link-state.json')['state'], 'off')


if __name__ == '__main__':
    unittest.main()
