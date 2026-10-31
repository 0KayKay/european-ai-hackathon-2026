#!/usr/bin/env python3
"""Enterprise load test for the b300/node gateway (standard library only, Python 3.9+).

  plan    demand forecast per organisation size, no requests sent
  run     one profile at one organisation size for a fixed time
  ramp    grow the organisation stage by stage until a latency target fails, then narrow it down
  suite   ramp several profiles one after another
  sweep   closed-loop concurrency sweep of one model and request shape
  report  rebuild the report of an earlier run from its files
"""
import argparse
import asyncio
import json
import math
import multiprocessing
import os
from pathlib import Path
import random
import sys
import threading
import time
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import workload  # noqa: E402
from client import Http, Recorder  # noqa: E402
from report import Aggregate, scrape_metrics, write_report  # noqa: E402


def seconds(value):
    value = str(value).strip()
    unit = {'s': 1, 'm': 60, 'h': 3600}.get(value[-1])
    return float(value[:-1]) * unit if unit else float(value)


def raise_fd_limit():
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    except (ImportError, ValueError, OSError):
        pass


def http_json(base, key, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base.rstrip('/') + path, data=data, headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def endpoint(args):
    if args.endpoint:
        info = json.loads(Path(args.endpoint).read_text())
        base, key_file = args.base_url or info['base_url'], args.key_file or info['key_file']
    else:
        base, key_file = args.base_url, args.key_file
    if not base or not key_file:
        sys.exit('Give --endpoint ENDPOINT.json, or --base-url and --key-file')
    return base.rstrip('/'), Path(key_file).read_text().strip()


def discover_roles(base, key, overrides):
    listed = {m['id'] for m in http_json(base, key, '/models')['data']}
    roles = {}
    for role, name in dict(workload.ROLES, **overrides).items():
        if name in listed:
            roles[role] = name
    if 'main' not in roles:
        sys.exit('Main model %r is not served; listed: %s. Use --role main=NAME.'
                 % (overrides.get('main', workload.ROLES['main']), ', '.join(sorted(listed))))
    missing = sorted(set(workload.ROLES) - set(roles))
    print('models: ' + ', '.join('%s=%s' % kv for kv in sorted(roles.items())))
    if missing:
        print('not served (chat roles fall back to main, other steps are skipped): ' + ', '.join(missing))
    return roles


def calibrate(base, key, roles):
    """Tokens per synthetic word on the main model, from two one-token requests."""
    text = workload.Text(1.0)
    rng = random.Random(7)
    counts = []
    for words in (20, 2020):
        body = {'model': roles['main'], 'max_tokens': 1, 'messages': [{'role': 'user', 'content': text.make(rng, words)}]}
        body.update(workload.ROLE_PARAMS.get('main', {}))
        counts.append(http_json(base, key, '/chat/completions', body, timeout=600)['usage']['prompt_tokens'])
    ratio = (counts[1] - counts[0]) / 2000.0
    ratio = min(3.0, max(0.8, ratio))
    print('calibration: %.2f tokens per synthetic word' % ratio)
    return ratio


def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    limit, k, p = math.exp(-lam), 0, rng.random()
    while p > limit:
        k += 1
        p *= rng.random()
    return k


# ------------------------------------------------------------------ worker

def worker_main(cfg, index, employees, stage, stage_start, stage_duration, stop, reset):
    raise_fd_limit()
    try:
        asyncio.run(worker(cfg, index, employees, stage, stage_start, stage_duration, stop, reset))
    except KeyboardInterrupt:
        pass


async def worker(cfg, index, employees, stage, stage_start, stage_duration, stop, reset):
    client = Http(cfg['base_url'], cfg['key'], cfg['timeout'])
    rec = Recorder(Path(cfg['out']) / ('requests-%d.jsonl' % index), index, cfg['epoch'])
    rec.stage = lambda: stage.value
    text = workload.Text(cfg['ratio'])
    ctx = workload.Ctx(client, cfg['roles'], text, workload.Shared(text), rec,
                       force_lengths=cfg['force_lengths'], time_scale=cfg['time_scale'])
    rng = random.Random(cfg['seed'] * 1000 + index)
    share, scale = 1.0 / cfg['workers'], cfg['time_scale']
    sessions = set()
    started = {wf: 0 for wf in workload.WORKFLOWS}

    def start(wf):
        task = asyncio.ensure_future(workload.run_session(ctx, wf, rng.getrandbits(62)))
        sessions.add(task)
        task.add_done_callback(sessions.discard)
        started[wf] += 1

    async def housekeeping():
        last = time.monotonic()
        worst = 0.0
        beat = 0
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            worst = max(worst, now - last - 1.0)
            last = now
            beat += 1
            if beat % 5 == 0:
                rec.write({'k': 'tick', 't': round(rec.now(), 3), 'sessions': len(sessions), 'lag': round(worst, 3),
                           'started': dict(started), 'inflight': ctx.inflight_ages()})
                worst = 0.0
            rec.flush()

    keeper = asyncio.ensure_future(housekeeping())
    if cfg['pattern'] == 'backlog':
        # A department's archive dumped into the index at once, plus a batch of queued flow runs.
        for _ in range(int(employees.value * 0.02 * share) + 1):
            start('ingest')
        for _ in range(int(employees.value * 0.05 * share) + 1):
            start('automation')
    tick, cached, generation = 0.1, (None, None), reset.value
    next_tick = time.monotonic()
    while not stop.is_set():
        if reset.value != generation:
            # Everyone leaves: cancelled requests disconnect, so the servers abort them.
            generation = reset.value
            for task in list(sessions) + list(ctx.background):
                task.cancel()
            await asyncio.gather(*sessions, *ctx.background, return_exceptions=True)
            rec.write({'k': 'reset', 't': round(rec.now(), 3)})
            next_tick = time.monotonic()
        if cached[0] != employees.value:
            cached = (employees.value, workload.rates(employees.value, cfg['profile'], share))
        factor = workload.pattern_factor(cfg['pattern'], time.time() - stage_start.value, stage_duration.value)
        for wf, rate in cached[1].items():
            for _ in range(poisson(rng, rate * (1.0 if wf == 'ingest' else factor) * tick / scale)):
                start(wf)
        next_tick += tick
        await asyncio.sleep(max(0.0, next_tick - time.monotonic()))
    # Stop arriving; let running sessions finish for a while, then cut them off.
    deadline = time.monotonic() + cfg['drain']
    while sessions and time.monotonic() < deadline:
        await asyncio.sleep(1.0)
    for task in list(sessions) + list(ctx.background):
        task.cancel()
    await asyncio.gather(*sessions, *ctx.background, return_exceptions=True)
    keeper.cancel()
    rec.write({'k': 'end', 't': round(rec.now(), 3), 'left': len(sessions)})
    rec.close()
    client.close()


# ------------------------------------------------------------- coordinator

class Tail:
    """Reads complete new lines from the workers' files."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.offsets = {}

    def read(self):
        rows = []
        for path in sorted(self.folder.glob('requests-*.jsonl')):
            with path.open('rb') as handle:
                handle.seek(self.offsets.get(path, 0))
                data = handle.read()
            end = data.rfind(b'\n') + 1
            self.offsets[path] = self.offsets.get(path, 0) + end
            rows.extend(json.loads(line) for line in data[:end].splitlines() if line.strip())
        return rows


def metrics_thread(plan, out, stop, epoch):
    """Polls each vLLM server's /metrics on the node (needs --plan; the job's backend key opens them)."""
    spec = json.loads(Path(plan).read_text())
    key_file = Path(plan).parent / 'backend.key'
    key = key_file.read_text().strip() if key_file.exists() else ''
    with open(Path(out) / 'metrics.jsonl', 'a') as handle:
        while not stop.is_set():
            t = round(time.time() - epoch, 3)
            for m in spec['models']:
                values = scrape_metrics('http://127.0.0.1:%d/metrics' % m['port'], key)
                if values:
                    handle.write(json.dumps({'t': t, 'model': m['name'], 'm': values}) + '\n')
            handle.flush()
            stop.wait(5)


def cooldown(args, cfg):
    """Waits until every vLLM server is idle (with --plan), otherwise a fixed pause."""
    deadline = time.time() + max(cfg['cooldown'], 5)
    if not (args.plan and Path(args.plan).exists()):
        time.sleep(cfg['cooldown'])
        return
    spec = json.loads(Path(args.plan).read_text())
    key_file = Path(args.plan).parent / 'backend.key'
    key = key_file.read_text().strip() if key_file.exists() else ''
    time.sleep(5)
    while time.time() < deadline:
        busy = 0.0
        for m in spec['models']:
            values = scrape_metrics('http://127.0.0.1:%d/metrics' % m['port'], key) or {}
            busy += sum(v for n, v in values.items() if 'num_requests_running' in n or 'num_requests_waiting' in n)
        if busy == 0:
            return
        time.sleep(5)


def progress(agg, window_start, now, stage_label):
    s = agg.window(window_start, now)
    line = '%s %s | calls/s %.1f err %.1f%% | chat TTFT p95 %s TPOT p95 %s | agent TTFT p95 %s | guard p95 %s | gen tok/s %d | sessions %d | gen-lag %.2fs' % (
        time.strftime('%H:%M:%S'), stage_label, s['calls_s'], 100 * s['error_rate'],
        fmt(s['cls'].get('interactive', {}).get('ttft_p95')), ms(s['cls'].get('interactive', {}).get('tpot_p95')),
        fmt(s['cls'].get('agent', {}).get('ttft_p95')), fmt(s['cls'].get('guard', {}).get('dur_p95')),
        s['gen_tok_s'], agg.sessions_now(), agg.max_lag(window_start, now))
    print(line, flush=True)


def fmt(value):
    return '-' if value is None else ('%.2fs' % value)


def ms(value):
    return '-' if value is None else ('%.0fms' % (1000 * value))


def run_stages(args, cfg, stage_plan):
    """Runs workers through stages; stage_plan(results) returns the next employee count or None."""
    out = Path(cfg['out'])
    out.mkdir(parents=True, exist_ok=True)
    mp = multiprocessing.get_context('spawn')
    employees, stage, reset = mp.Value('d', 0.0), mp.Value('i', 0), mp.Value('i', 0)
    stage_start, stage_duration, stop = mp.Value('d', time.time()), mp.Value('d', cfg['stage']), mp.Event()
    first = stage_plan([])
    employees.value = first
    workers = [mp.Process(target=worker_main, args=(cfg, i, employees, stage, stage_start, stage_duration, stop, reset))
               for i in range(cfg['workers'])]
    scraper_stop = threading.Event()
    scraper = None
    if args.plan and Path(args.plan).exists():
        scraper = threading.Thread(target=metrics_thread, args=(args.plan, out, scraper_stop, cfg['epoch']), daemon=True)
        scraper.start()
    for w in workers:
        w.start()
    tail, agg, results = Tail(out), Aggregate(workload.SLO), []
    current = first
    try:
        while current is not None:
            warmup = cfg['warmup']
            if results and current < results[-1]['employees']:
                # Stepping down after an overload: start from an empty system instead of its backlog.
                print('-- resetting: cancelling all sessions and letting the servers drain', flush=True)
                employees.value = 0
                reset.value += 1
                cooldown(args, cfg)
                agg.add(tail.read())
                warmup = min(cfg['warmup'] * 1.5, cfg['stage'] / 2)
            begin = time.time() - cfg['epoch']
            stage.value, employees.value, stage_start.value = len(results), current, time.time()
            label = 'stage %d: %d employees (%s, %s)' % (len(results) + 1, current, cfg['profile'], cfg['pattern'])
            print('== ' + label, flush=True)
            end = begin + cfg['stage']
            while time.time() - cfg['epoch'] < end:
                time.sleep(max(0.5, min(30.0, end - (time.time() - cfg['epoch']))))
                agg.add(tail.read())
                now = time.time() - cfg['epoch']
                progress(agg, max(begin, now - 60), now, 'stage %d %d emp' % (len(results) + 1, current))
            window = (begin + min(warmup, cfg['stage'] / 2), end)
            verdict = agg.verdict(*window)
            results.append({'stage': len(results) + 1, 'employees': current, 'start': begin, 'end': end,
                            'window': window, 'passed': verdict['passed'], 'failures': verdict['failures'],
                            'stats': verdict['stats'], 'generator_lag_max': agg.max_lag(*window)})
            print('   %s %d employees: %s' % ('PASS' if verdict['passed'] else 'FAIL', current,
                                             '; '.join(verdict['failures']) or 'all targets met'), flush=True)
            (out / 'stages.json').write_text(json.dumps({'config': public(cfg), 'stages': results}, indent=2))
            current = stage_plan(results)
    except KeyboardInterrupt:
        print('interrupted; stopping workers', flush=True)
    finally:
        stop.set()
        for w in workers:
            w.join(cfg['drain'] + 60)
            if w.is_alive():
                w.terminate()
        scraper_stop.set()
        if scraper:
            scraper.join(10)
    agg.add(tail.read())
    (out / 'stages.json').write_text(json.dumps({'config': public(cfg), 'stages': results}, indent=2))
    path = write_report(out)
    print('report: %s' % path, flush=True)
    return results


def public(cfg):
    return {k: v for k, v in cfg.items() if k != 'key'}


def ramp_plan(sizes, grow, refine, max_stages):
    """Planned sizes in order; past them grow by `grow`; after a failure, bisect in log space."""
    state = {'refine': refine}

    def plan(results):
        if not results:
            return sizes[0]
        if len(results) >= max_stages:
            return None
        passed = [r['employees'] for r in results if r['passed']]
        failed = [r['employees'] for r in results if not r['passed']]
        best = max(passed) if passed else None
        worst = min([f for f in failed if best is None or f > best], default=None)
        if worst is None:
            upcoming = [s for s in sizes if s > results[-1]['employees']]
            return upcoming[0] if upcoming else (round(results[-1]['employees'] * grow) if grow > 1 else None)
        if state['refine'] <= 0:
            return None
        state['refine'] -= 1
        if best is None:
            return max(1, round(worst / 3)) if worst > 3 else None
        if worst / best < 1.2:
            return None
        return round(math.sqrt(best * worst))
    return plan


def make_cfg(args, base, key, roles, ratio, profile, out):
    workers = args.workers or max(1, min(8, len(os.sched_getaffinity(0)) - 1) if hasattr(os, 'sched_getaffinity') else 2)
    return {'base_url': base, 'key': key, 'roles': roles, 'ratio': ratio, 'profile': profile, 'pattern': args.pattern,
            'workers': workers, 'stage': seconds(args.stage), 'warmup': seconds(args.warmup), 'drain': seconds(args.drain),
            'timeout': seconds(args.timeout), 'seed': args.seed, 'out': str(out), 'epoch': time.time(),
            'cooldown': seconds(args.cooldown),
            'time_scale': args.time_scale, 'force_lengths': not args.natural_lengths}


def prepare(args):
    base, key = endpoint(args)
    overrides = dict(item.split('=', 1) for item in args.role)
    roles = discover_roles(base, key, overrides)
    ratio = args.tokens_per_word or calibrate(base, key, roles)
    return base, key, roles, ratio


def out_dir(args, name):
    return Path(args.out or ('loadtest-results/%s-%s' % (time.strftime('%Y%m%d-%H%M%S'), name)))


# ------------------------------------------------------------------- sweep

async def sweep(args, base, key, roles, ratio, out):
    """Closed loop: N clients send the same request shape back to back, N rising per level."""
    role = args.sweep_role
    text = workload.Text(ratio)
    rng = random.Random(args.seed)
    prefix = text.make(random.Random(1), args.shared_prefix) if args.shared_prefix else ''
    client = Http(base, key, seconds(args.timeout))
    rec = Recorder(out / 'requests-sweep.jsonl', 0, time.time())
    ctx = workload.Ctx(client, roles, text, workload.Shared(text), rec, force_lengths=not args.natural_lengths)
    levels = [int(x) for x in args.levels.split(',')]
    duration = seconds(args.level_time)
    rows = []

    async def one():
        if role == 'embed':
            await ctx.embed([text.make(rng, args.input) for _ in range(args.batch)], 'sweep', 'embed', 'sweep')
        elif role == 'rerank':
            await ctx.rerank(text.make(rng, 30), [text.make(rng, args.input) for _ in range(args.batch)],
                             'sweep', 'rerank', 'sweep')
        elif role == 'asr':
            await ctx.transcribe(random.Random(rng.random()), 'sweep', 'asr', 'sweep')
        else:
            messages = ([{'role': 'system', 'content': prefix}] if prefix else []) + \
                [{'role': 'user', 'content': text.make(rng, args.input)}]
            await ctx.chat(role, messages, args.output, 'sweep', 'chat', 'sweep', stream=role not in ('guard', 'policy'))

    for level in levels:
        stop_at = time.monotonic() + duration
        t0 = rec.now()

        async def loop():
            while time.monotonic() < stop_at:
                await one()
        print('level %d: %d concurrent %s requests for %ds' % (level, level, role, duration), flush=True)
        await asyncio.gather(*(loop() for _ in range(level)))
        rec.flush()
        agg = Aggregate(workload.SLO)
        agg.add([json.loads(line) for line in (out / 'requests-sweep.jsonl').read_text().splitlines()])
        s = agg.window(t0 + min(10, duration / 4), rec.now(), by_start=True)
        row = {'concurrency': level, 'calls_s': s['calls_s'], 'error_rate': s['error_rate'], 'gen_tok_s': s['gen_tok_s'],
               'prompt_tok_s': s['prompt_tok_s'], **s['cls'].get('sweep', {})}
        rows.append(row)
        print('   calls/s %.2f  gen tok/s %d  prompt tok/s %d  TTFT p50/p95 %s/%s  TPOT p50/p95 %s/%s  dur p95 %s  err %.1f%%' % (
            row['calls_s'], row['gen_tok_s'], row['prompt_tok_s'], fmt(row.get('ttft_p50')), fmt(row.get('ttft_p95')),
            ms(row.get('tpot_p50')), ms(row.get('tpot_p95')), fmt(row.get('dur_p95')), 100 * row['error_rate']), flush=True)
        if row['error_rate'] > 0.5:
            print('   more than half the requests failed; stopping the sweep')
            break
    client.close()
    rec.close()
    (out / 'sweep.json').write_text(json.dumps({'role': role, 'model': roles.get(role, roles['main']), 'input': args.input,
                                                'output': args.output, 'batch': args.batch, 'levels': rows}, indent=2))
    lines = ['# Sweep: %s (%s)' % (role, roles.get(role, roles['main'])), '',
             'Input %d tokens, output %d tokens, shared prefix %d, batch %d, %ds per level.' % (
                 args.input, args.output, args.shared_prefix, args.batch, duration), '',
             '| Concurrency | Calls/s | Gen tok/s | Prompt tok/s | TTFT p50 | TTFT p95 | TPOT p50 | TPOT p95 | Duration p95 | Errors |',
             '| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        lines.append('| %d | %.2f | %d | %d | %s | %s | %s | %s | %s | %.1f%% |' % (
            r['concurrency'], r['calls_s'], r['gen_tok_s'], r['prompt_tok_s'], fmt(r.get('ttft_p50')), fmt(r.get('ttft_p95')),
            ms(r.get('tpot_p50')), ms(r.get('tpot_p95')), fmt(r.get('dur_p95')), 100 * r['error_rate']))
    (out / 'summary.md').write_text('\n'.join(lines) + '\n')
    print('report: %s' % (out / 'summary.md'))


# --------------------------------------------------------------------- CLI

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    def connection(p):
        p.add_argument('--endpoint', help='endpoint.json written by node.sbatch (base_url and key_file)')
        p.add_argument('--base-url', help='Gateway URL ending in /v1, e.g. http://127.0.0.1:18080/v1 through a tunnel')
        p.add_argument('--key-file', help='File with the gateway key')
        p.add_argument('--plan', help="The job's plan.json: also scrape each vLLM server's /metrics (on the node only)")
        p.add_argument('--role', action='append', default=[], metavar='ROLE=MODEL', help='Override a role model name')
        p.add_argument('--tokens-per-word', type=float, help='Skip calibration and use this ratio')
        p.add_argument('--natural-lengths', action='store_true', help='Do not force output lengths with min_tokens')
        p.add_argument('--timeout', default='900s', help='Per-request timeout')
        p.add_argument('--seed', type=int, default=1)
        p.add_argument('--out', help='Result folder (default loadtest-results/TIME-NAME)')

    def scenario(p, stage_default):
        p.add_argument('--profile', default='mixed', choices=sorted(workload.PROFILES))
        p.add_argument('--pattern', default='steady', choices=['steady', 'morning', 'bursty', 'backlog'])
        p.add_argument('--stage', default=stage_default, help='Time per stage, e.g. 5m')
        p.add_argument('--warmup', default='90s', help='Start of each stage left out of its verdict')
        p.add_argument('--drain', default='60s', help='Time running sessions get after the last stage')
        p.add_argument('--cooldown', default='120s', help='Longest wait for idle servers before a smaller stage')
        p.add_argument('--workers', type=int, help='Load-generator processes (default: CPUs - 1, at most 8)')
        p.add_argument('--time-scale', type=float, default=1.0, help=argparse.SUPPRESS)  # tests: compress time

    p = sub.add_parser('plan', help='Forecast demand per organisation size; sends nothing')
    p.add_argument('--profile', default='all', choices=['all'] + sorted(workload.PROFILES))
    p.add_argument('--employees', default='100,500,1000,10000')
    p.add_argument('--tokens-per-word', type=float, default=1.3)

    p = sub.add_parser('run', help='One profile at one size')
    connection(p)
    scenario(p, '15m')
    p.add_argument('--employees', type=int, default=1000)

    for name, help_text in (('ramp', 'Grow until a latency target fails, then narrow the edge down'),
                            ('suite', 'Ramp several profiles one after another')):
        p = sub.add_parser(name, help=help_text)
        connection(p)
        scenario(p, '5m' if name == 'ramp' else '4m')
        p.add_argument('--employees', default='100,500,1000,10000', help='Planned stage sizes')
        p.add_argument('--grow', type=float, default=1.5, help='Growth per stage after the planned sizes (1 = stop)')
        p.add_argument('--refine', type=int, default=2, help='Bisection stages after the first failure')
        p.add_argument('--max-stages', type=int, default=12 if name == 'ramp' else 8)
        if name == 'suite':
            p.add_argument('--profiles', default='chat,knowledge,agentic,mixed')

    p = sub.add_parser('sweep', help='Concurrency sweep of one model and request shape')
    connection(p)
    p.add_argument('--sweep-role', default='main', choices=sorted(workload.ROLES))
    p.add_argument('--input', type=int, default=2000, help='Prompt tokens (per item for embed/rerank)')
    p.add_argument('--output', type=int, default=300, help='Generated tokens')
    p.add_argument('--shared-prefix', type=int, default=0, help='Tokens of system prompt shared by all requests')
    p.add_argument('--batch', type=int, default=32, help='Inputs per embed request / documents per rerank request')
    p.add_argument('--levels', default='1,4,16,64,128,256,512')
    p.add_argument('--level-time', default='90s')

    p = sub.add_parser('report', help='Rebuild summary.md/json of a result folder')
    p.add_argument('folder')
    args = parser.parse_args()
    raise_fd_limit()

    if args.command == 'plan':
        sizes = [int(x) for x in args.employees.split(',')]
        profiles = sorted(workload.PROFILES) if args.profile == 'all' else [args.profile]
        for profile in profiles:
            print('\n## %s: %s' % (profile, workload.PROFILES[profile]['description']))
            for row in workload.forecast(profile, sizes, ratio=args.tokens_per_word):
                print('\n%d employees, busiest hour: sessions/h %s' % (row['employees'], ', '.join(
                    '%s %.0f' % kv for kv in row['sessions_per_hour'].items())))
                for role, v in row['per_role'].items():
                    print('  %-7s %7.2f calls/s %9d prompt tok/s %7d gen tok/s' % (
                        role, v['calls_s'], v['prompt_tok_s'], v['gen_tok_s']))
        return
    if args.command == 'report':
        print(write_report(Path(args.folder)))
        return

    base, key, roles, ratio = prepare(args)
    if args.command == 'sweep':
        out = out_dir(args, 'sweep-' + args.sweep_role)
        out.mkdir(parents=True, exist_ok=True)
        asyncio.run(sweep(args, base, key, roles, ratio, out))
        return
    if args.command == 'run':
        out = out_dir(args, 'run-%s-%d' % (args.profile, args.employees))
        cfg = make_cfg(args, base, key, roles, ratio, args.profile, out)
        run_stages(args, cfg, lambda results: None if results else args.employees)
        return
    sizes = sorted(int(x) for x in args.employees.split(','))
    profiles = args.profiles.split(',') if args.command == 'suite' else [args.profile]
    root = out_dir(args, args.command + '-' + '-'.join(profiles))
    worst = len(profiles) * args.max_stages * (seconds(args.stage) + seconds(args.cooldown)) + seconds(args.drain)
    print('at most %d stages per profile: up to %.0f minutes in total (the serving job must outlive this)' % (
        args.max_stages, worst / 60), flush=True)
    summary = []
    for profile in profiles:
        if profile not in workload.PROFILES:
            sys.exit('Unknown profile ' + profile)
        out = root / profile if len(profiles) > 1 else root
        cfg = make_cfg(args, base, key, roles, ratio, profile, out)
        results = run_stages(args, cfg, ramp_plan(sizes, args.grow, args.refine, args.max_stages))
        passed = [r['employees'] for r in results if r['passed']]
        summary.append((profile, max(passed) if passed else None, len(results)))
    print('\nHighest organisation size meeting every target:')
    for profile, best, stages in summary:
        print('  %-10s %s  (%d stages)' % (profile, best if best is not None else 'none, even the smallest stage failed', stages))


if __name__ == '__main__':
    main()
