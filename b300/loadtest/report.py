"""Aggregation, latency-target verdicts and the Markdown/JSON report of a load-test folder."""
import json
import math
from pathlib import Path
import urllib.request

CLASS_ORDER = ['interactive', 'longctx', 'agent', 'guard', 'retrieval', 'background', 'sweep']
SERVER = {  # vLLM metric name fragments (names differ between versions)
    'running': ('num_requests_running',),
    'waiting': ('num_requests_waiting',),
    'kv': ('kv_cache_usage_perc', 'gpu_cache_usage_perc'),
    'preemptions': ('num_preemptions',),
    'prefix_hits': ('prefix_cache_hits',),
    'prefix_queries': ('prefix_cache_queries',),
    'prompt_tokens': ('prompt_tokens_total',),
    'generation_tokens': ('generation_tokens_total',),
}


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(math.ceil(q * len(ordered))) - 1))]


def scrape_metrics(url, key=''):
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + key} if key else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            text = response.read().decode('utf-8', 'replace')
    except Exception:  # noqa: BLE001 - a missing sample is fine
        return None
    values = {}
    for line in text.splitlines():
        if not line or line[0] == '#':
            continue
        name = line.split('{', 1)[0].split(' ', 1)[0]
        if name.endswith('_created') or '_bucket' in name:
            continue
        if any(f in name for fragments in SERVER.values() for f in fragments):
            rest = line.rsplit('}', 1)[1] if '{' in line else line[len(name):]
            try:
                values[name] = values.get(name, 0.0) + float(rest.split()[0])
            except (ValueError, IndexError):
                continue
    return values


class Aggregate:
    def __init__(self, slo):
        self.slo = slo
        self.calls, self.sessions, self.errors, self.ticks = [], [], [], []
        self.latest = {}

    def add(self, rows):
        for r in rows:
            kind = r.get('k')
            if kind == 'call':
                if r.get('err') == 'cancelled':
                    continue
                tpot = None
                if r.get('ttft') is not None and r.get('out', 0) > 1:
                    tpot = max(0.0, r['dur'] - r['ttft']) / (r['out'] - 1)
                self.calls.append((r['t'], r['cls'], r['role'], r['wf'] + '/' + r['step'], bool(r['ok']), r.get('ttft'),
                                   tpot, r['dur'], r.get('in') or 0, r.get('out') or 0, r.get('target'),
                                   (r.get('status'), (r.get('err') or '')[:80])))
            elif kind == 'session':
                self.sessions.append((r['t'], r['wf'], r['ok'], r['dur']))
            elif kind == 'error':
                self.errors.append((r['t'], r['wf'], r['err']))
            elif kind == 'tick':
                self.ticks.append((r['t'], r['w'], r['sessions'], r['lag'], r.get('inflight', {})))
                self.latest[r['w']] = r['sessions']

    def sessions_now(self):
        return sum(self.latest.values())

    def max_lag(self, t0, t1):
        return max([lag for t, _, _, lag, _ in self.ticks if t0 <= t <= t1] or [0.0])

    def inflight(self, t1):
        """Calls still running at the end of a window, from each worker's latest tick before it."""
        latest = {}
        for t, w, _, _, inflight in self.ticks:
            if t <= t1 + 6:
                latest[w] = inflight
        merged = {}
        for inflight in latest.values():
            for cls, ages in inflight.items():
                merged.setdefault(cls, []).extend(ages)
        return merged

    @staticmethod
    def _group(rows, extra=None):
        ttft = [r[5] for r in rows if r[5] is not None]
        tpot = [r[6] for r in rows if r[6] is not None]
        dur = [r[7] for r in rows]
        for age, waiting in (extra or []):  # unfinished calls are at least this slow
            dur.append(age)
            if waiting:
                ttft.append(age)
        n = len(rows)
        out = {'n': n, 'errors': sum(1 for r in rows if not r[4]), 'in_avg': round(sum(r[8] for r in rows) / n) if n else 0,
               'out_avg': round(sum(r[9] for r in rows) / n) if n else 0, 'unfinished': len(extra or [])}
        targets = [r[10] for r in rows if r[10]]
        if targets:
            out['target_avg'] = round(sum(targets) / len(targets))
        for name, values in (('ttft', ttft), ('tpot', tpot), ('dur', dur)):
            for q in (50, 95, 99):
                v = percentile(values, q / 100.0)
                out['%s_p%d' % (name, q)] = None if v is None else round(v, 4)
        return out

    def window(self, t0, t1, by_start=True, with_inflight=False):
        rows = [c for c in self.calls if t0 <= c[0] <= t1]
        span = max(t1 - t0, 1e-9)
        inflight = self.inflight(t1) if with_inflight else {}
        groups = {'cls': {}, 'role': {}, 'step': {}}
        for r in rows:
            groups['cls'].setdefault(r[1], []).append(r)
            groups['role'].setdefault(r[2], []).append(r)
            groups['step'].setdefault(r[3], []).append(r)
        errors = {}
        for r in rows:
            if not r[4]:
                errors[r[11]] = errors.get(r[11], 0) + 1
        sessions = {}
        for t, wf, ok, dur in self.sessions:
            if t0 <= t <= t1:
                sessions.setdefault(wf, []).append((ok, dur))
        return {
            'span': span, 'calls': len(rows), 'calls_s': len(rows) / span,
            'error_rate': (sum(1 for r in rows if not r[4]) / len(rows)) if rows else 0.0,
            'prompt_tok_s': int(sum(r[8] for r in rows) / span), 'gen_tok_s': int(sum(r[9] for r in rows) / span),
            'cls': {k: self._group(v, inflight.get(k)) for k, v in groups['cls'].items()},
            'role': {k: self._group(v) for k, v in groups['role'].items()},
            'step': {k: self._group(v) for k, v in groups['step'].items()},
            'sessions': {wf: {'n': len(v), 'failed': sum(1 for ok, _ in v if not ok),
                              'p50': percentile([d for _, d in v], 0.5), 'p95': percentile([d for _, d in v], 0.95)}
                         for wf, v in sessions.items()},
            'top_errors': sorted(([n, '%s %s' % k] for k, n in errors.items()), reverse=True)[:5],
        }

    def verdict(self, t0, t1):
        stats = self.window(t0, t1, with_inflight=True)
        failures = []
        for cls, targets in self.slo.items():
            if not isinstance(targets, dict) or cls not in stats['cls']:
                continue
            group = stats['cls'][cls]
            if group['n'] + group['unfinished'] < self.slo['min_samples']:
                continue
            for metric, limit in targets.items():
                value = group.get(metric)
                if value is not None and value > limit:
                    failures.append('%s %s %.2fs > %.2fs' % (cls, metric, value, limit))
        if stats['calls'] and stats['error_rate'] > self.slo['error_rate']:
            failures.append('errors %.1f%% > %.1f%%' % (100 * stats['error_rate'], 100 * self.slo['error_rate']))
        if not stats['calls']:
            failures.append('no completed calls')
        return {'passed': not failures, 'failures': failures, 'stats': stats}


def server_summary(metrics, t0, t1):
    by_model = {}
    for row in metrics:
        by_model.setdefault(row['model'], []).append(row)
    out = {}
    for model, rows in by_model.items():
        rows.sort(key=lambda r: r['t'])
        inside = [r for r in rows if t0 <= r['t'] <= t1]
        before = [r for r in rows if r['t'] < t0][-1:]
        after = [r for r in rows if r['t'] > t1][:1]
        edges = before + inside + after  # counters: one sample either side of the window
        if not inside and len(edges) < 2:
            continue
        inside = inside or edges

        def series(key, sample):
            names = [n for n in sample[-1]['m'] if any(f in n for f in SERVER[key])]
            return [sum(r['m'].get(n, 0.0) for n in names) for r in sample]
        span = max(edges[-1]['t'] - edges[0]['t'], 1e-9)
        summary = {}
        for key in ('running', 'waiting', 'kv'):
            values = series(key, inside)
            summary[key + '_max'] = round(max(values), 3) if values else None
            summary[key + '_avg'] = round(sum(values) / len(values), 3) if values else None
        for key in ('preemptions', 'prompt_tokens', 'generation_tokens', 'prefix_hits', 'prefix_queries'):
            values = series(key, edges)
            summary[key] = (values[-1] - values[0]) if len(values) > 1 else 0.0
        summary['prompt_tok_s'] = int(summary.pop('prompt_tokens') / span)
        summary['gen_tok_s'] = int(summary.pop('generation_tokens') / span)
        queries = summary.pop('prefix_queries')
        hits = summary.pop('prefix_hits')
        summary['prefix_hit_rate'] = round(hits / queries, 3) if queries else None
        out[model] = summary
    return out


def bottleneck(servers, stats, slo):
    if servers:
        model, s = max(servers.items(), key=lambda kv: ((kv[1].get('waiting_max') or 0), (kv[1].get('kv_max') or 0)))
        if (s.get('waiting_max') or 0) > 0:
            return '%s queued the most requests (max %d waiting, KV cache up to %.0f%%)' % (
                model, s['waiting_max'], 100 * (s.get('kv_max') or 0))
    worst, ratio = None, 0.0
    for role, group in stats['role'].items():
        for metric in ('ttft_p95', 'dur_p95'):
            value = group.get(metric)
            if value and value > ratio:
                worst, ratio = '%s (%s %.2fs)' % (role, metric, value), value
    return 'slowest role: ' + worst if worst else 'unknown'


def fmt(value, unit='s'):
    if value is None:
        return '-'
    if unit == 'ms':
        return '%.0fms' % (1000 * value)
    return ('%.2f%s' % (value, unit)) if unit else str(value)


def read_rows(folder):
    rows = []
    for path in sorted(Path(folder).glob('requests-*.jsonl')):
        with path.open() as handle:
            rows.extend(json.loads(line) for line in handle if line.strip())
    return rows


def write_report(folder):
    folder = Path(folder)
    if not (folder / 'stages.json').exists():
        return folder / 'summary.md'
    meta = json.loads((folder / 'stages.json').read_text())
    from workload import SLO
    agg = Aggregate(SLO)
    agg.add(read_rows(folder))
    metrics = []
    if (folder / 'metrics.jsonl').exists():
        metrics = [json.loads(line) for line in (folder / 'metrics.jsonl').read_text().splitlines() if line.strip()]
    cfg, stages = meta['config'], meta['stages']
    details = []
    for stage in stages:
        t0, t1 = stage['window']
        # The ramp's own verdict, judged when the stage ended (unfinished calls count at their age).
        verdict = stage if 'stats' in stage else agg.verdict(t0, t1)
        servers = server_summary(metrics, t0, t1)
        details.append({'stage': stage['stage'], 'employees': stage['employees'], 'passed': verdict['passed'],
                        'failures': verdict['failures'], 'stats': verdict['stats'], 'servers': servers,
                        'generator_lag_max': stage.get('generator_lag_max', agg.max_lag(t0, t1)),
                        'bottleneck': None if verdict['passed'] else bottleneck(servers, verdict['stats'], SLO)})
    passed = [d['employees'] for d in details if d['passed']]
    failed = [d['employees'] for d in details if not d['passed']]
    result = {'config': cfg, 'slo': SLO, 'highest_passing': max(passed) if passed else None,
              'lowest_failing': min([f for f in failed if not passed or f > max(passed)], default=None), 'stages': details}
    (folder / 'summary.json').write_text(json.dumps(result, indent=2))

    lines = ['# Load test: %s profile, %s pattern' % (cfg['profile'], cfg['pattern']), '',
             'Models: %s.' % ', '.join('%s = `%s`' % kv for kv in sorted(cfg['roles'].items())),
             'Stages of %ds (first %ds not judged), %d generator processes, %.2f tokens per synthetic word%s.' % (
                 cfg['stage'], cfg['warmup'], cfg['workers'], cfg['ratio'],
                 '' if cfg.get('force_lengths', True) else ', natural output lengths'), '']
    if result['highest_passing'] is None:
        lines.append('**No stage met every target.**')
    else:
        lines.append('**Highest organisation size meeting every target: %d employees.**' % result['highest_passing'])
    if result['lowest_failing'] is not None:
        first_fail = next(d for d in details if d['employees'] == result['lowest_failing'])
        lines.append('First failing size: %d employees (%s). Likely bottleneck: %s.' % (
            first_fail['employees'], '; '.join(first_fail['failures']), first_fail['bottleneck']))
    lines += ['', '## Stages', '',
              '| # | Employees | Verdict | Calls/s | Errors | Chat TTFT p95 | Chat TPOT p95 | Doc TTFT p95 | Agent TTFT p95 '
              '| Guard p95 | Retrieval p95 | Background p95 | Prompt tok/s | Gen tok/s | Generator lag |',
              '| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for d in details:
        s, c = d['stats'], d['stats']['cls']
        verdict = 'pass' if d['passed'] else '**fail**'
        lines.append('| %d | %d | %s | %.1f | %.1f%% | %s | %s | %s | %s | %s | %s | %s | %d | %d | %.2fs |' % (
            d['stage'], d['employees'], verdict, s['calls_s'], 100 * s['error_rate'],
            fmt(c.get('interactive', {}).get('ttft_p95')), fmt(c.get('interactive', {}).get('tpot_p95'), 'ms'),
            fmt(c.get('longctx', {}).get('ttft_p95')), fmt(c.get('agent', {}).get('ttft_p95')),
            fmt(c.get('guard', {}).get('dur_p95')), fmt(c.get('retrieval', {}).get('dur_p95')),
            fmt(c.get('background', {}).get('dur_p95')), s['prompt_tok_s'], s['gen_tok_s'], d['generator_lag_max']))
    if any(d['generator_lag_max'] > 1.0 for d in details):
        lines += ['', '> The load generator itself fell behind (event-loop lag above 1 s) in some stages; '
                      'rerun with more `--workers` or CPUs before trusting those numbers.']
    lines += ['', 'Targets: ' + '; '.join('%s %s' % (cls, ', '.join('%s ≤ %ss' % kv for kv in t.items()))
                                          for cls, t in SLO.items() if isinstance(t, dict))
              + '; errors ≤ %.0f%%.' % (100 * SLO['error_rate'])]
    for d in details:
        s = d['stats']
        lines += ['', '## Stage %d: %d employees (%s)' % (d['stage'], d['employees'], 'pass' if d['passed'] else 'fail')]
        if d['failures']:
            lines += ['', 'Missed: ' + '; '.join(d['failures']) + '.']
        if s['top_errors']:
            lines += ['', 'Most common errors: ' + '; '.join('%d× %s' % (n, e) for n, e in s['top_errors']) + '.']
        lines += ['', '| Model role | Calls | Errors | Avg in | Avg out (target) | TTFT p50 | TTFT p95 | TPOT p95 | Duration p95 |',
                  '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        for role, g in sorted(s['role'].items()):
            lines.append('| %s | %d | %d | %d | %d (%s) | %s | %s | %s | %s |' % (
                role, g['n'], g['errors'], g['in_avg'], g['out_avg'], g.get('target_avg', '-'), fmt(g['ttft_p50']),
                fmt(g['ttft_p95']), fmt(g['tpot_p95'], 'ms'), fmt(g['dur_p95'])))
        lines += ['', '| Workflow step | Calls | Errors | Avg in | Avg out | TTFT p95 | Duration p50 | Duration p95 |',
                  '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        for step, g in sorted(s['step'].items()):
            lines.append('| %s | %d | %d | %d | %d | %s | %s | %s |' % (
                step, g['n'], g['errors'], g['in_avg'], g['out_avg'], fmt(g['ttft_p95']), fmt(g['dur_p50']), fmt(g['dur_p95'])))
        if s['sessions']:
            lines += ['', '| Session (end to end) | Finished | Failed | p50 | p95 |', '| --- | ---: | ---: | ---: | ---: |']
            for wf, g in sorted(s['sessions'].items()):
                lines.append('| %s | %d | %d | %s | %s |' % (wf, g['n'], g['failed'], fmt(g['p50']), fmt(g['p95'])))
        if d['servers']:
            lines += ['', '| vLLM server | Running max | Waiting max | KV cache max | Preemptions | Prefix hit rate | Prompt tok/s | Gen tok/s |',
                      '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
            for model, v in sorted(d['servers'].items()):
                lines.append('| %s | %s | %s | %s | %d | %s | %d | %d |' % (
                    model, fmt(v['running_max'], ''), fmt(v['waiting_max'], ''),
                    '-' if v['kv_max'] is None else '%.0f%%' % (100 * v['kv_max']), v['preemptions'],
                    '-' if v['prefix_hit_rate'] is None else '%.0f%%' % (100 * v['prefix_hit_rate']),
                    v['prompt_tok_s'], v['gen_tok_s']))
    (folder / 'summary.md').write_text('\n'.join(lines) + '\n')
    return folder / 'summary.md'
