"""List the model profiles, or add one for another checkpoint of a model that already has a profile.

Runs on your laptop (Python 3.7+, standard library only); cli.py profiles calls it.

    python3 leonardo/tools/profiles.py                       # the profiles and their validation status
    python3 leonardo/tools/profiles.py add NAME --from BASE --model ORG/REPO [--revision REV] [--served-name NAME]

`add` is for fine-tunes and other checkpoints with the same architecture as BASE: the new profile keeps
BASE's runtime, hardware and vLLM settings, which were found for that architecture. It looks the model up
on Hugging Face (HF_TOKEN for gated or private models, HF_ENDPOINT for a mirror), pins the revision,
counts the weight files, and refuses a different architecture or a LoRA adapter. Then it writes
serve/profiles/NAME.json and the model's agentgateway route. Nothing is uploaded or started.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

LEONARDO = Path(__file__).resolve().parents[1]
PROFILES = LEONARDO / 'serve/profiles'
sys.path.insert(0, str(LEONARDO / 'serve'))
sys.path.insert(0, str(LEONARDO / 'tools'))


def load(path):
    return json.loads(Path(path).read_text())


def hub(path):
    """GET a Hugging Face Hub URL path as JSON."""
    endpoint = os.environ.get('HF_ENDPOINT', 'https://huggingface.co').rstrip('/')
    request = urllib.request.Request(endpoint + '/' + path)
    if os.environ.get('HF_TOKEN'):
        request.add_header('Authorization', 'Bearer ' + os.environ['HF_TOKEN'])
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise SystemExit('Hugging Face refused {} ({}): a gated or private model needs its licence accepted on '
                             'huggingface.co and HF_TOKEN set'.format(path, error.code))
        if error.code == 404:
            raise SystemExit('Not found on Hugging Face: ' + path)
        raise


def model(repo, revision):
    """Revision sha, weight files with sizes, and config.json of a Hub model."""
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
        raise SystemExit('Expected a Hugging Face model id like org/name, got: ' + repo)
    info = hub('api/models/{}/revision/{}?blobs=true'.format(repo, urllib.parse.quote(revision, safe='')))
    files = {s['rfilename']: s.get('size') for s in info.get('siblings', [])}
    if 'adapter_config.json' in files and 'config.json' not in files:
        raise SystemExit('{} is a LoRA adapter, not a full model. Merge it into its base model and upload the '
                         'result, or ask for adapter support in the router.'.format(repo))
    for needed in ('config.json', 'tokenizer_config.json'):
        if needed not in files:
            raise SystemExit('{} has no {} at its top level, which a model server needs. {}'.format(
                repo, needed, contents(files)))
    weights = {name: size for name, size in files.items() if name.endswith('.safetensors')}
    if not weights:
        raise SystemExit('{} has no .safetensors weights; the install job downloads only those'.format(repo))
    config = hub('{}/resolve/{}/config.json'.format(repo, info['sha']))
    return {'sha': info['sha'], 'weights': weights, 'config': config}


def contents(files):
    """What a repository holds instead, so a refusal can be checked without opening the Hub."""
    top = sorted(name for name in files if '/' not in name)
    folders = sorted({name.split('/')[0] + '/' for name in files if '/' in name})
    nested = sorted(name for name in files if name.endswith('/config.json'))
    text = 'It holds: {}{}.'.format(', '.join(top[:12]) or 'no files', ' and {} more'.format(len(top) - 12) if len(top) > 12 else '')
    if folders:
        text += ' Folders: {}.'.format(', '.join(folders[:8]))
    if nested:
        text += ' A config.json sits in {}; profiles add handles models at the top level only.'.format(', '.join(nested[:4]))
    return text + (' A draft model for speculative decoding or an adapter is used together with its base model,'
                   ' not served on its own; profiles add does not set that up.')


def quantization(config):
    return (config.get('quantization_config') or {}).get('quant_method')


def served_name(repo):
    return re.sub(r'[^a-z0-9_.-]+', '-', repo.split('/')[-1].lower()).strip('-.') + '-leonardo'


def add(name, base_name, repo, revision='main', served=None, force=False, today=None):
    import inference
    import render_agentgateway
    inference.identifier(name)
    path = PROFILES / (name + '.json')
    if path.exists():
        raise SystemExit('Profile {} already exists: {}'.format(name, path))
    base_path = PROFILES / (base_name + '.json')
    if not base_path.exists():
        raise SystemExit('No base profile {}; see: cli.py profiles'.format(base_name))
    base = load(base_path)
    others = [load(p) for p in PROFILES.glob('*.json')]
    served = served or served_name(repo)

    new = model(repo, revision)
    reference = model(base['model_id'], base.get('revision') or 'main')
    warnings = []
    if new['config'].get('architectures') != reference['config'].get('architectures'):
        message = 'architecture {} differs from {} ({}); its settings may not fit'.format(
            new['config'].get('architectures'), base_name, reference['config'].get('architectures'))
        if not force:
            raise SystemExit('Refusing: ' + message + '. Pick a base profile with the same architecture, '
                             'or write a profile by hand (README, "Add a model").')
        warnings.append(message)
    if quantization(new['config']) != quantization(reference['config']):
        warnings.append('quantization {} differs from {} ({}); check engine_args such as --quantization'.format(
            quantization(new['config']), base_name, quantization(reference['config'])))
    sizes, base_sizes = list(new['weights'].values()), list(reference['weights'].values())
    if None not in sizes + base_sizes and sum(sizes) > 1.15 * sum(base_sizes):
        warnings.append('weights are {:.0f} GB against {:.0f} GB for {}; they may not fit its GPUs'.format(
            sum(sizes) / 1e9, sum(base_sizes) / 1e9, base_name))

    # After the model checks, so a model that does not fit is refused for that reason first.
    if served in {p['served_model'] for p in others}:
        raise SystemExit('Served name {} is taken; choose one with --served-name'.format(served))
    profile = dict(base)
    profile.update(id=name, model_id=repo, revision=new['sha'], checkpoint=name, served_model=served)
    if 'expected_shards' in base:
        profile['expected_shards'] = len(new['weights'])
    profile['client'] = dict(base['client'], local_port=max(p['client']['local_port'] for p in others) + 1)
    profile['derived_from'] = base_name
    profile['validation'] = 'Not GPU-validated yet. Created by cli.py profiles add from {} on {}.'.format(
        base_name, today or datetime.date.today().isoformat())
    inference.profile_check(profile)
    path.write_text(json.dumps(profile, indent=2) + '\n')
    render_agentgateway.OUTPUT.write_text(render_agentgateway.render())
    print('Wrote {}: {} at {}, {} weight files, served as {}.'.format(
        path.relative_to(LEONARDO.parent), repo, new['sha'][:12], len(new['weights']), served))
    for warning in warnings:
        print('WARNING: ' + warning)
    print('Updated {} with its route.'.format(render_agentgateway.OUTPUT.relative_to(LEONARDO.parent)))
    print('Next: ./leonardo/cli.py install {0} && ./leonardo/cli.py up {0} --qos boost_qos_dbg --time 00:30:00\n'
          'Then commit both files.'.format(name))
    return profile


def validations():
    """Newest validated run per profile id."""
    newest = {}
    for key, run in load(LEONARDO / 'validation/validated_code.json')['runs'].items():
        profile = run.get('profile_id', key)
        if profile not in newest or run['date'] > newest[profile]['date']:
            newest[profile] = run
    return newest


def listing():
    newest = validations()
    rows = [('PROFILE', 'SERVED AS', 'MODEL', 'GPUS', 'RUNTIME', 'STATUS')]
    for path in sorted(PROFILES.glob('*.json'), key=lambda p: p.stem):
        p = load(path)
        run = newest.get(p['id'])
        status = 'validated {} (job {})'.format(run['date'], run['job_id']) if run else 'not validated yet'
        if p.get('derived_from') and not run:
            status += ', from ' + p['derived_from']
        rows.append((p['id'], p['served_model'], p['model_id'],
                     str(p['resources']['nodes'] * p['resources']['gpus_per_node']), p['runtime'], status))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    for row in rows:
        print('  '.join(cell.ljust(width) for cell, width in zip(row, widths)) + '  ' + row[-1])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action')
    p = sub.add_parser('add', help='add a profile for another checkpoint of an existing profile\'s model')
    p.add_argument('name', help='new profile name, e.g. qwen235b-ourtune')
    p.add_argument('--from', dest='base', required=True, help='existing profile with the same architecture, e.g. qwen235b')
    p.add_argument('--model', required=True, help='Hugging Face model id, e.g. our-org/qwen3-235b-guard-tune')
    p.add_argument('--revision', default='main', help='branch, tag or commit to pin (default: main, pinned to its commit)')
    p.add_argument('--served-name', help='model name for API callers (default: <repo name>-leonardo)')
    p.add_argument('--force', action='store_true', help='allow an architecture that differs from the base profile')
    args = parser.parse_args(argv)
    if args.action == 'add':
        add(args.name, args.base, args.model, args.revision, args.served_name, args.force)
    else:
        listing()


if __name__ == '__main__':
    main()
