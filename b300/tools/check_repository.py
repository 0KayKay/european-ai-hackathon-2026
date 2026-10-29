"""Offline checks only. These do not establish GPU or multi-user live validation."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'serve'))
from common import time_seconds, verify_release


def main():
    entries = verify_release()
    actual = {str(p.relative_to(ROOT / 'serve')) for p in (ROOT / 'serve').rglob('*')
              if p.is_file() and '__pycache__' not in p.parts and p.name != 'RELEASE_MANIFEST.json'}
    assert set(entries) == actual, 'Unmanifested serving source exists'
    for file in ROOT.rglob('*.py'):
        if '.runtime' not in file.parts:
            ast.parse(file.read_text(), filename=str(file))
    subprocess.run(['bash', '-n', str(ROOT / 'serve/run.sbatch')], check=True)
    models = json.loads((ROOT / 'serve/models.json').read_text())
    profiles = json.loads((ROOT / 'serve/profiles.json').read_text())
    images = json.loads((ROOT / 'serve/images.json').read_text())
    for name, profile in profiles.items():
        assert profile['gpus'] == sum(models[k]['tp'] for k in profile['models'])
        assert profile['gpus'] <= 8
        time_seconds(profile['time'])
        assert '@sha256:' in images[profile['image']]
    clients = json.loads((ROOT / 'profiles.json').read_text())
    for name, model in models.items():
        assert re.fullmatch('[a-f0-9]{40}', model['revision'])
        # The laptop profile, the frozen model spec and the literal engine flags must agree.
        assert clients[name]['max_model_len'] == model['max_model_len'], name
        assert clients[name]['tensor_parallel'] == model['tp'], name
        engine_args = model.get('engine_args', [])
        if '--max-model-len' in engine_args:
            value = int(engine_args[engine_args.index('--max-model-len') + 1])
            assert value == model['max_model_len'], name
    for file in ROOT.rglob('*.md'):
        if '.runtime' in file.parts:
            continue
        for link in re.findall(r'\]\(([^)]+)\)', file.read_text()):
            if not link.startswith(('https://', 'http://', '#')):
                assert (file.parent / link.split('#')[0]).exists(), (str(file), link)
    print('PASS: syntax, shell, source manifest, pinned profiles, consistent context settings, GPU totals and Markdown file links.')
    print('No SSH login, resource allocation or model request was performed.')


if __name__ == '__main__':
    main()
