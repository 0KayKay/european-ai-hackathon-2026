"""Stand-ins for the app's auth and backend services, just enough for agentgateway's policies.

auth (port 3000): /api/auth/ok answers 200 (readiness), /api/auth/authorize-internal answers 204 for `Bearer $GATEWAY_INTERNAL_KEY`, 401 otherwise,
like frontend/auth-server/server.mjs. backend (port 8000): /api/health answers 200, and the guardrails
webhook /guardrails/request blocks a HARD-challenge message containing "BLOCKME" with 403, like
backend/hackathon/api/guardrails.py would for an unsafe message, and lets everything else through.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import threading


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body=None):
        data = json.dumps(body).encode() if body is not None else b''
        self.send_response(status)
        if body is not None:
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/api/health', '/api/auth/ok'):  # readiness probes of backend and auth
            return self.reply(200, {'status': 'ok'})
        if path == '/api/auth/authorize-internal':
            return self.authorize()
        self.reply(404, {})

    def authorize(self):
        expected = os.environ['GATEWAY_INTERNAL_KEY'].encode()
        supplied = self.headers.get('Authorization', '')[7:].encode()
        self.reply(204 if supplied and hmac.compare_digest(supplied, expected) else 401)

    def do_POST(self):
        path = self.path.split('?')[0]
        if path == '/api/auth/authorize-internal':
            return self.authorize()
        if path == '/guardrails/request':
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            messages = body.get('body', {}).get('messages', [])
            text = ' '.join(str(m.get('content')) for m in messages if m.get('role') == 'user')
            print('guardrail', self.headers.get('x-challenge-difficulty'), repr(text[:80]), flush=True)
            if self.headers.get('x-challenge-difficulty') == 'HARD' and 'BLOCKME' in text:
                return self.reply(200, {'action': {'status_code': 403, 'body': 'Request blocked by challenge guardrail.',
                                                   'reason': 'hard_input'}})
            return self.reply(200, {'action': {'reason': 'stub allows'}})
        self.reply(404, {})


for port in (3000, 8000):
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
print('stubs on 3000 (auth) and 8000 (backend)', flush=True)
threading.Event().wait()
