#!/usr/bin/env python3
"""Plan, gateway configuration and status for the full-node B300 lineup.

Runs on the batch host with the system Python (standard library only).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
APIS = ('chat', 'embed', 'rerank', 'transcribe')
# Path-routed endpoints agentgateway's LLM routes do not translate.
PATH_ROUTES = {
    'rerank': ['/v1/rerank', '/v2/rerank', '/rerank', '/v1/score', '/score'],
    'transcribe': ['/v1/audio/'],
}


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def sqsh_name(key, ref):
    digest = ref.rsplit('@sha256:', 1)[-1] if '@sha256:' in ref else hashlib.sha256(ref.encode()).hexdigest()
    return '%s-%s.sqsh' % (key, digest[:16])


def make_plan(lineup, wanted=None, gateway_port=None):
    """Select models and reject GPU, port and name conflicts before anything is started."""
    models = lineup['models']
    ids = [m['id'] for m in models]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate model ids in lineup')
    if wanted:
        unknown = sorted(set(wanted) - set(ids))
        if unknown:
            raise ValueError('Unknown model ids: ' + ', '.join(unknown))
        chosen = [m for m in models if m['id'] in wanted]
    else:
        chosen = [m for m in models if m.get('enabled', True)]
    if not chosen:
        raise ValueError('No models selected')
    errors, names, ports, share = [], {}, {}, {}
    gpus_total = lineup['node_gpus']
    for m in chosen:
        if m['api'] not in APIS:
            errors.append('%s: unknown api %r' % (m['id'], m['api']))
        if m['image'] not in lineup['images']:
            errors.append('%s: unknown image %r' % (m['id'], m['image']))
        for name in [m['name']] + m.get('aliases', []):
            if name in names:
                errors.append('%s: model name %s also served by %s' % (m['id'], name, names[name]))
            names[name] = m['id']
        if m['port'] in ports or m['port'] == gateway_port:
            errors.append('%s: port %s already used' % (m['id'], m['port']))
        ports[m['port']] = m['id']
        if not m['gpus'] or len(set(m['gpus'])) != len(m['gpus']):
            errors.append('%s: gpus must be a non-empty list of distinct indexes' % m['id'])
        for gpu in m['gpus']:
            if not 0 <= gpu < gpus_total:
                errors.append('%s: GPU %s does not exist on a %s-GPU node' % (m['id'], gpu, gpus_total))
            share[gpu] = share.get(gpu, 0) + m['memory']
    for api in PATH_ROUTES:
        owners = [m['id'] for m in chosen if m['api'] == api]
        if len(owners) > 1:
            errors.append('Only one %s model can be routed, got %s' % (api, ', '.join(owners)))
    for gpu, total in sorted(share.items()):
        if total > lineup['max_gpu_share'] + 1e-9:
            errors.append('GPU %s is oversubscribed: memory fractions add up to %.2f' % (gpu, total))
    if errors:
        raise ValueError('\n'.join(errors))
    planned = []
    for index, m in enumerate(chosen):
        # vLLM sizes its memory from what is free at startup, so models sharing a GPU start in lineup order.
        after = [p['id'] for p in chosen[:index] if set(p['gpus']) & set(m['gpus'])]
        planned.append(dict(m, after=after, sqsh=sqsh_name(m['image'], lineup['images'][m['image']])))
    images = {m['image']: {'ref': lineup['images'][m['image']], 'sqsh': sqsh_name(m['image'], lineup['images'][m['image']])}
              for m in chosen}
    return {'node_gpus': gpus_total, 'images': images, 'gateway': lineup['gateway'], 'models': planned}


def gateway_config(plan, port, key_file, backend_key_file):
    key = Path(key_file).read_text().strip()
    keys = [{'keyHash': 'sha256:' + hashlib.sha256(key.encode()).hexdigest()}]
    client_auth = {'mode': 'strict', 'keys': keys}
    backend_auth = {'key': {'file': str(backend_key_file)}}
    llm_models, routes = [], []
    for m in plan['models']:
        for name in [m['name']] + m.get('aliases', []):
            llm_models.append({'name': name, 'provider': 'openAI',
                               'params': {'baseUrl': 'http://127.0.0.1:%d/v1' % m['port'], 'model': m['name']},
                               'auth': backend_auth})
        if m['api'] in PATH_ROUTES:
            routes.append({'name': m['id'],
                           'matches': [{'path': {'pathPrefix': p}} for p in PATH_ROUTES[m['api']]],
                           'policies': {'apiKey': client_auth, 'backendAuth': backend_auth,
                                        'timeout': {'requestTimeout': '600s'}},
                           'backends': [{'host': '127.0.0.1:%d' % m['port']}]})
    return {'config': {'logging': {'level': 'info', 'format': 'json'}},
            'gateways': {'default': {'port': port, 'bindAddress': '0.0.0.0'}},
            'routes': routes,
            'llm': {'policies': {'apiKey': client_auth}, 'models': llm_models}}


def probe(url, key=None, timeout=5):
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + key} if key else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except Exception as error:  # noqa: BLE001 - status reporting must never fail the job
        return getattr(error, 'code', None), b''


def kv_lines(log):
    """vLLM's own capacity report, the number that replaces the report's estimates."""
    found = []
    try:
        with open(log, errors='replace') as handle:
            for line in handle:
                if 'GPU KV cache size' in line or 'Maximum concurrency' in line:
                    found.append(line.strip()[-160:])
    except OSError:
        pass
    return found[-2:]


def status(plan, run, gateway_port, key_file):
    run = Path(run)
    previous = load(run / 'status.json') if (run / 'status.json').exists() else {'models': {}}
    started = previous.get('started', time.time())
    models, events = {}, []
    for m in plan['models']:
        before = previous['models'].get(m['id'], {})
        if (run / 'state' / (m['id'] + '.exited')).exists():
            state = 'exited'
        elif probe('http://127.0.0.1:%d/health' % m['port'])[0] == 200:
            state = 'ready'
        else:
            state = 'starting'
        entry = {'state': state, 'name': m['name'], 'gpus': m['gpus'], 'port': m['port']}
        entry['since'] = before.get('since', time.time()) if before.get('state') == state else time.time()
        entry['kv_cache'] = kv_lines(run / 'logs' / (m['id'] + '.log')) if state == 'ready' else []
        if before.get('state') != state:
            events.append('%s %s (%s) after %ds' % (state.upper(), m['id'], m['name'], time.time() - started))
        models[m['id']] = entry
    code, body = probe('http://127.0.0.1:%d/v1/models' % gateway_port, Path(key_file).read_text().strip())
    listed = sorted(item['id'] for item in json.loads(body)['data']) if code == 200 else []
    gateway = {'http_status': code, 'models_listed': len(listed)}
    if previous.get('gateway', {}).get('http_status') != code:
        events.append('GATEWAY http %s, %d models listed' % (code, len(listed)))
    save(run / 'status.json', {'started': started, 'updated': time.time(), 'gateway': gateway, 'models': models})
    for event in events:
        print(time.strftime('%H:%M:%S'), event, flush=True)
    return models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan', help='Validate the selected lineup and write plan.json')
    p.add_argument('--lineup', default=str(HERE / 'lineup.json'))
    p.add_argument('--models', default='', help='Comma-separated model ids; default: enabled ones')
    p.add_argument('--gateway-port', type=int)
    p.add_argument('--out', required=True)
    p = sub.add_parser('get', help='Print plan fields for the batch script')
    p.add_argument('--plan', required=True)
    p.add_argument('what', choices=['images', 'models', 'gateway', 'gpus'])
    p = sub.add_parser('gateway', help='Write the agentgateway configuration')
    p.add_argument('--plan', required=True)
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--key-file', required=True)
    p.add_argument('--backend-key-file', required=True)
    p.add_argument('--out', required=True)
    p = sub.add_parser('status', help='Probe models and gateway, write status.json, print changes')
    p.add_argument('--plan', required=True)
    p.add_argument('--run', required=True)
    p.add_argument('--gateway-port', type=int, required=True)
    p.add_argument('--key-file', required=True)
    p.add_argument('--exit-code', action='store_true', help='Exit 3 once every model step has exited')
    args = parser.parse_args()

    if args.command == 'plan':
        wanted = [m.strip() for m in args.models.split(',') if m.strip()]
        try:
            plan = make_plan(load(args.lineup), wanted, args.gateway_port)
        except ValueError as error:
            sys.exit('Invalid lineup:\n' + str(error))
        save(args.out, plan)
        for m in plan['models']:
            print('%-10s %-40s GPUs %-12s port %d  %s' % (m['id'], m['name'], ','.join(map(str, m['gpus'])),
                                                          m['port'], ('after ' + ','.join(m['after'])) if m['after'] else ''))
    elif args.command == 'get':
        plan = load(args.plan)
        if args.what == 'images':
            for key, image in plan['images'].items():
                print(key, image['ref'], image['sqsh'])
        elif args.what == 'models':
            for m in plan['models']:
                print(m['id'], m['image'], m['sqsh'])
        elif args.what == 'gpus':
            print(1 + max(gpu for m in plan['models'] for gpu in m['gpus']))
        else:
            arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}[os.uname().machine]
            gateway = plan['gateway']
            print(gateway['version'], arch, gateway['url'].format(version=gateway['version'], arch=arch),
                  gateway['sha256'][arch])
    elif args.command == 'gateway':
        plan = load(args.plan)
        # JSON is valid YAML, so agentgateway reads this without a YAML library here.
        save(args.out, gateway_config(plan, args.port, args.key_file, args.backend_key_file))
    else:
        plan = load(args.plan)
        models = status(plan, args.run, args.gateway_port, args.key_file)
        if args.exit_code and all(m['state'] == 'exited' for m in models.values()):
            sys.exit(3)


if __name__ == '__main__':
    main()
