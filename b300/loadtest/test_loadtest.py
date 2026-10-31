"""Local checks: python3 -m unittest discover -s b300/loadtest -p 'test_*.py' -v"""
import asyncio
import json
from pathlib import Path
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fake_server  # noqa: E402
import loadtest  # noqa: E402
import report  # noqa: E402
import workload  # noqa: E402
from client import Http, StreamTail  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class FakeServer:
    def __init__(self, key, **options):
        self.port = free_port()
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            # Built inside its loop: on Python 3.9 asyncio primitives bind to the current loop.
            server = fake_server.Server(key, options.get('slots', 32), options.get('prefill', 200000.0),
                                        options.get('decode', 2000.0))
            listener = self.loop.run_until_complete(asyncio.start_server(server.handle, '127.0.0.1', self.port))
            self.ready.set()
            self.loop.run_forever()
            listener.close()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        self.ready.wait(5)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        tasks = asyncio.all_tasks(self.loop)
        for task in tasks:
            task.cancel()
        async def drain():
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0.05)
        self.loop.run_until_complete(drain())
        self.loop.close()


class ModelTest(unittest.TestCase):
    def test_rates_scale_with_employees_and_profiles_differ(self):
        small, large = workload.rates(100, 'mixed'), workload.rates(10000, 'mixed')
        for wf in workload.WORKFLOWS:
            self.assertAlmostEqual(large[wf], 100 * small[wf])
        self.assertGreater(workload.rates(1000, 'agentic')['agent'], 3 * workload.rates(1000, 'mixed')['agent'])
        self.assertGreater(workload.rates(1000, 'knowledge')['ingest'], workload.rates(1000, 'chat')['ingest'])

    def test_forecast_counts_every_role(self):
        row = workload.forecast('mixed', [1000], samples=20)[0]
        self.assertTrue({'main', 'fast', 'guard', 'embed', 'rerank'} <= set(row['per_role']))
        self.assertGreater(row['per_role']['main']['prompt_tok_s'], row['per_role']['main']['gen_tok_s'])

    def test_history_trims_oldest_turns_first(self):
        hist = workload.History('system', 10)
        for i in range(10):
            hist.add('user', 'm%d' % i, 100)
        messages, tokens = hist.messages(450)
        self.assertEqual([m['content'] for m in messages], ['system', 'm6', 'm7', 'm8', 'm9'])
        self.assertEqual(tokens, 410)

    def test_media_is_unique(self):
        rng = random.Random(1)
        self.assertNotEqual(workload.speech_wav(rng), workload.speech_wav(rng))
        self.assertTrue(workload.page_png(rng).startswith(b'\x89PNG'))


class RampTest(unittest.TestCase):
    def results(self, *pairs):
        return [{'employees': e, 'passed': p} for e, p in pairs]

    def test_planned_sizes_then_growth(self):
        plan = loadtest.ramp_plan([100, 1000], 2, 2, 10)
        self.assertEqual(plan([]), 100)
        self.assertEqual(plan(self.results((100, True))), 1000)
        self.assertEqual(plan(self.results((100, True), (1000, True))), 2000)

    def test_bisects_after_failure(self):
        plan = loadtest.ramp_plan([100, 1000, 10000], 1.5, 2, 10)
        history = self.results((100, True), (1000, True), (10000, False))
        self.assertEqual(plan(history), 3162)
        history += self.results((3162, False))
        self.assertEqual(plan(history), 1778)
        history += self.results((1778, True))
        self.assertIsNone(plan(history))  # refinement budget used up

    def test_first_stage_failing_steps_down(self):
        plan = loadtest.ramp_plan([300], 1.5, 1, 10)
        self.assertEqual(plan(self.results((300, False))), 100)


class ReportTest(unittest.TestCase):
    def call(self, t, cls='interactive', ttft=0.1, dur=1.0, ok=True):
        return {'k': 'call', 't': t, 'wf': 'chat', 'step': 'answer', 'cls': cls, 'role': 'main', 'ok': ok,
                'ttft': ttft, 'dur': dur, 'in': 100, 'out': 11, 'target': 11}

    def test_unfinished_calls_count_against_targets(self):
        agg = report.Aggregate(workload.SLO)
        agg.add([self.call(i) for i in range(40)])
        self.assertTrue(agg.verdict(0, 60)['passed'])
        stuck = [[30.0, True]] * 10
        agg.add([{'k': 'tick', 't': 60, 'w': 0, 'sessions': 5, 'lag': 0.0, 'inflight': {'interactive': stuck}}])
        verdict = agg.verdict(0, 60)
        self.assertFalse(verdict['passed'])
        self.assertIn('interactive ttft_p95', verdict['failures'][0])

    def test_error_rate_and_tpot(self):
        agg = report.Aggregate(workload.SLO)
        agg.add([self.call(i, ok=i % 10) for i in range(50)])
        stats = agg.verdict(0, 60)
        self.assertTrue(any(f.startswith('errors') for f in stats['failures']))
        self.assertAlmostEqual(stats['stats']['cls']['interactive']['tpot_p50'], 0.09)

    def test_prometheus_parsing(self):
        key = 'k'
        server = FakeServer(key)
        try:
            values = report.scrape_metrics('http://127.0.0.1:%d/metrics' % server.port)
        finally:
            server.stop()
        self.assertIn('vllm:num_requests_waiting', values)


class ClientTest(unittest.TestCase):
    def test_stream_usage_and_keep_alive(self):
        server = FakeServer('secret')

        async def go():
            client = Http('http://127.0.0.1:%d/v1' % server.port, 'secret')
            body = json.dumps({'model': 'glm-5.3-flash', 'messages': [{'role': 'user', 'content': 'a b c'}],
                               'max_tokens': 5, 'stream': True}).encode()
            tail = StreamTail()
            status, _, first = await client.request('/chat/completions', body, on_data=tail)
            status2, data, _ = await client.request('/models', None)
            stranger = Http('http://127.0.0.1:%d/v1' % server.port, 'wrong')
            denied = await stranger.request('/models', None)
            stranger.close()
            idle = len(client.idle)
            client.close()
            await asyncio.sleep(0.05)
            return status, tail, first, status2, data, denied[0], idle
        try:
            status, tail, first, status2, data, denied, idle = asyncio.run(go())
        finally:
            server.stop()
        self.assertEqual((status, status2, denied), (200, 200, 401))
        self.assertEqual(tail.usage()['completion_tokens'], 5)
        self.assertIsNotNone(first)
        self.assertIn(b'glm-5.3-flash', data)
        self.assertEqual(idle, 1)  # both requests used one kept-alive connection


class EndToEndTest(unittest.TestCase):
    def test_short_ramp_writes_report(self):
        server = FakeServer('secret', slots=16, decode=400.0)
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / 'key'
            key.write_text('secret\n')
            out = Path(folder) / 'out'
            started = time.time()
            done = subprocess.run([sys.executable, str(HERE / 'loadtest.py'), 'ramp', '--base-url',
                                   'http://127.0.0.1:%d/v1' % server.port, '--key-file', str(key), '--employees', '300,3000',
                                   '--grow', '1', '--refine', '1', '--stage', '8s', '--warmup', '3s', '--drain', '2s',
                                   '--cooldown', '2s', '--workers', '2', '--time-scale', '0.02', '--out', str(out)],
                                  capture_output=True, text=True, timeout=240)
            server.stop()
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertNotIn('never awaited', done.stderr)
            summary = json.loads((out / 'summary.json').read_text())
            self.assertGreaterEqual(len(summary['stages']), 2)
            roles = set()
            for stage in summary['stages']:
                roles |= set(stage['stats']['role'])
            self.assertTrue({'main', 'fast', 'guard', 'embed', 'rerank', 'vision'} <= roles, roles)
            self.assertIn('## Stage 1: 300 employees', (out / 'summary.md').read_text())
            self.assertLess(time.time() - started, 200)
            errors = [json.loads(line) for p in out.glob('requests-*.jsonl') for line in p.read_text().splitlines()
                      if '"k":"error"' in line]
            self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
