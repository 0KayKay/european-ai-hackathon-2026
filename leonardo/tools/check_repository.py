"""Check script syntax, the serve/ manifest, the rendered agentgateway config, GPU-validated code and
document links locally."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

import render_agentgateway
from update_manifest import SERVE, build

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    checked = 0
    for p in sorted(ROOT.rglob('*')):
        if '__pycache__' in p.parts or not p.is_file():
            continue
        if p.suffix == '.py':
            ast.parse(p.read_text(), filename=str(p))
            checked += 1
        elif p.suffix in {'.sh', '.sbatch'}:
            subprocess.check_call(['bash', '-n', str(p)])
            for embedded in re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", p.read_text(), re.S):
                ast.parse(embedded, filename=str(p) + ':embedded-python')
            checked += 1
        elif p.suffix == '.md':
            for target in re.findall(r'\[[^\]]+\]\(([^\s)]+)\)', p.read_text()):
                if '://' in target or target.startswith('#'):
                    continue
                assert (p.parent / target.split('#')[0]).exists(), 'Broken document link in {}: {}'.format(p, target)

    manifest = json.loads((SERVE / 'RELEASE_MANIFEST.json').read_text())
    if build(manifest['release'])['files'] != manifest['files']:
        raise SystemExit('FAIL: serve/RELEASE_MANIFEST.json is stale: run python3 tools/update_manifest.py')
    if render_agentgateway.OUTPUT.read_text() != render_agentgateway.render():
        raise SystemExit('FAIL: infrastructure/leonardo/agentgateway.yaml is stale: run python3 tools/render_agentgateway.py')

    record = json.loads((ROOT / 'validation/validated_code.json').read_text())
    for model, run in record['runs'].items():
        for suite in ('api-startup.json', 'api-client-1.json', 'api-client-2.json'):
            checks = json.loads((ROOT / 'validation' / run['evidence'] / suite).read_text())
            assert len(checks) == 7 and all(c['passed'] for c in checks.values()), 'Incomplete evidence: ' + model + '/' + suite
    drifted = [name for name, digest in sorted(record['validated_files_sha256'].items())
               if sha256(ROOT / 'serve' / name) != digest]
    unvalidated = sorted(set(manifest['files']) - set(record['validated_files_sha256']) - {'site.example.json'})
    print('PASS: syntax for {} scripts, manifest, agentgateway config, GPU evidence and local document links.'.format(checked))
    if unvalidated:
        print('NOTE: never run on GPUs: ' + ', '.join(unvalidated))
    if drifted:
        print('NOTE: serve/ differs from the GPU-validated code in: ' + ', '.join(drifted))
        print('      Run a GPU validation on Leonardo, then record the new hashes in validation/validated_code.json.')
    else:
        print('serve/ matches the GPU-validated code byte for byte.')


if __name__ == '__main__':
    main()
