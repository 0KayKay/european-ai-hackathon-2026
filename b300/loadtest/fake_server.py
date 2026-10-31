#!/usr/bin/env python3
"""A stand-in for the node gateway with limited capacity, for trying the load test without GPUs.

  python3 b300/loadtest/fake_server.py --port 18999 --key-file key.txt

Every model has `--slots` concurrent sequences; requests beyond that queue.
Prefill and decode speeds are per sequence, so latency grows with load.
"""
import argparse
import asyncio
import json
from pathlib import Path

NAMES = ['glm-5.3-flash', 'qwen3-30b-a3b-instruct-2507', 'qwen3-vl-30b-a3b-instruct', 'bge-m3', 'bge-reranker-v2-m3',
         'whisper-large-v3-turbo', 'nemotron-content-safety-reasoning-4b', 'gpt-oss-safeguard-20b']


class Model:
    def __init__(self, slots):
        self.slots = asyncio.Semaphore(slots)
        self.running = 0
        self.waiting = 0
        self.prompt = 0
        self.generated = 0


class Server:
    def __init__(self, key, slots, prefill, decode):
        self.key, self.prefill, self.decode = key, prefill, decode
        self.models = {name: Model(slots) for name in NAMES}

    async def handle(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                method, path, _ = line.decode().split(' ', 2)
                headers = {}
                while True:
                    h = await reader.readline()
                    if h in (b'\r\n', b''):
                        break
                    k, _, v = h.decode().partition(':')
                    headers[k.strip().lower()] = v.strip()
                body = await reader.readexactly(int(headers.get('content-length', 0)))
                if path == '/metrics':
                    await self.send(writer, 200, self.metrics().encode(), 'text/plain')
                elif headers.get('authorization') != 'Bearer ' + self.key:
                    await self.send(writer, 401, b'{"error":"unauthorized"}')
                elif path == '/v1/models':
                    await self.send(writer, 200, json.dumps({'data': [{'id': n} for n in NAMES]}).encode())
                elif path == '/v1/chat/completions':
                    await self.chat(writer, json.loads(body))
                elif path in ('/v1/embeddings', '/v1/rerank'):
                    data = json.loads(body)
                    items = data.get('input') or data.get('documents') or []
                    words = sum(len(x.split()) for x in items) + len(data.get('query', '').split()) * len(items)
                    await self.work(data['model'], words * 1.3, 0)
                    await self.send(writer, 200, json.dumps({'data': [], 'results': [],
                                                             'usage': {'prompt_tokens': int(words * 1.3)}}).encode())
                elif path == '/v1/audio/transcriptions':
                    await self.work('whisper-large-v3-turbo', 1500, 30)
                    await self.send(writer, 200, b'{"text":"hello","usage":{"prompt_tokens":1500,"completion_tokens":30}}')
                else:
                    await self.send(writer, 404, b'{"error":"not found"}')
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def send(self, writer, status, body, ctype='application/json'):
        writer.write(b'HTTP/1.1 %d X\r\nContent-Type: %s\r\nContent-Length: %d\r\n\r\n' % (status, ctype.encode(), len(body)))
        writer.write(body)
        await writer.drain()

    async def work(self, name, prompt, out, on_token=None):
        model = self.models[name]
        model.waiting += 1
        async with model.slots:
            model.waiting -= 1
            model.running += 1
            try:
                await asyncio.sleep(prompt / self.prefill)
                model.prompt += int(prompt)
                for i in range(int(out)):
                    await asyncio.sleep(1.0 / self.decode)
                    model.generated += 1
                    if on_token:
                        await on_token(i)
            finally:
                model.running -= 1

    async def chat(self, writer, data):
        if data['model'] not in self.models:
            return await self.send(writer, 404, b'{"error":"model_not_found"}')
        words = 0
        for m in data['messages']:
            content = m['content']
            words += len(content.split()) if isinstance(content, str) else 800
        prompt, out = int(words * 1.3) + 10, int(data.get('max_tokens', 16))
        usage = {'prompt_tokens': prompt, 'completion_tokens': out, 'total_tokens': prompt + out}
        if not data.get('stream'):
            await self.work(data['model'], prompt, out)
            return await self.send(writer, 200, json.dumps({'id': 'x', 'model': data['model'], 'usage': usage,
                                                            'choices': [{'message': {'content': 'ok'}}]}).encode())
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n')

        async def chunk(payload):
            writer.write(b'%x\r\n%s\r\n' % (len(payload), payload))
            await writer.drain()

        async def token(i):
            await chunk(b'data: {"choices":[{"delta":{"content":"w"}}]}\n\n')
        await self.work(data['model'], prompt, out, token)
        await chunk(('data: ' + json.dumps({'choices': [], 'usage': usage}) + '\n\ndata: [DONE]\n\n').encode())
        writer.write(b'0\r\n\r\n')
        await writer.drain()

    def metrics(self):
        lines = []
        for name, m in self.models.items():
            label = '{model_name="%s"}' % name
            lines += ['vllm:num_requests_running%s %d' % (label, m.running),
                      'vllm:num_requests_waiting%s %d' % (label, m.waiting),
                      'vllm:kv_cache_usage_perc%s %.3f' % (label, min(1.0, m.running / 64.0)),
                      'vllm:prompt_tokens_total%s %d' % (label, m.prompt),
                      'vllm:generation_tokens_total%s %d' % (label, m.generated)]
        return '\n'.join(lines) + '\n'


async def serve(args):
    key = Path(args.key_file).read_text().strip()
    server = Server(key, args.slots, args.prefill, args.decode)
    listener = await asyncio.start_server(server.handle, '127.0.0.1', args.port)
    async with listener:
        await listener.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port', type=int, default=18999)
    parser.add_argument('--key-file', required=True)
    parser.add_argument('--slots', type=int, default=32, help='Concurrent sequences per model')
    parser.add_argument('--prefill', type=float, default=200000.0, help='Prompt tokens per second per sequence')
    parser.add_argument('--decode', type=float, default=2000.0, help='Generated tokens per second per sequence')
    args = parser.parse_args()
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
