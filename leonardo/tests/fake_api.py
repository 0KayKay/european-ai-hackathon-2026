"""Regression fixture only: OpenAI-style responses, never model inference."""
import json
import os
from pathlib import Path
import signal
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

cfg = json.loads(Path('RUN_CONFIG.json').read_text())
profile = cfg['profile']
key = Path('.runtime/api_key').read_text().strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, content_type='application/json'):
        encoded = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def authorized(self):
        if self.headers.get('Authorization') != 'Bearer ' + key:
            self.send(401, '{}')
            return False
        return True

    def do_GET(self):
        if self.path == '/health':
            self.send(200, '{}')
        elif self.authorized():
            self.send(200, json.dumps({'data': [{'id': profile['served_model']}]}))

    def do_POST(self):
        if not self.authorized():
            return
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert body['model'] == profile['served_model']
        assert body['chat_template_kwargs'] == profile['client']['chat_template_kwargs']
        prompt = body['messages'][0]['content']
        assert all(ord(character) < 128 for character in prompt), 'Only English test prompts are allowed'
        if body.get('stream'):
            chunk = {'choices': [{'delta': {'content': 'Hello from the HPC assistant.'}}]}
            self.send(200, 'data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n', 'text/event-stream')
        elif 'tools' in body:
            call = {'id': 'test-call', 'type': 'function', 'function': {'name': 'sum_numbers', 'arguments': '{"a":17,"b":25}'}}
            self.send(200, json.dumps({'choices': [{'message': {'content': None, 'tool_calls': [call]}}]}))
        else:
            text = 'Hello, I am an AI assistant running on an HPC system.' if 'introduce yourself' in prompt else '42'
            self.send(200, json.dumps({'choices': [{'message': {'content': text}}]}))


if __name__ == '__main__':
    Path('.runtime/fake-server-pid').write_text(str(os.getpid()))
    server = ThreadingHTTPServer(('127.0.0.1', int(os.environ['INFERENCE_API_PORT'])), Handler)
    signal.signal(signal.SIGTERM, lambda *args: (_ for _ in ()).throw(SystemExit(0)))
    try:
        server.serve_forever()
    finally:
        server.server_close()
