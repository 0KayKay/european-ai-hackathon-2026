"""Freeze the portable B300 serving source; no network or Slurm operations."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ('common.py', 'manage.py', 'server.py', 'run.sbatch', 'validation.py',
    'cubin_overlay.py', 'check_release.py', 'profiles.json', 'models.json', 'images.json',
    'site.example.json', 'glm-client-profile.json', 'safeguard.py', 'vendor/inference.py', 'vendor/test_endpoint.py')


def files():
    return {name: hashlib.sha256((ROOT / 'serve' / name).read_bytes()).hexdigest()
            for name in sorted(SOURCE_FILES)}


if __name__ == '__main__':
    (ROOT / 'serve/RELEASE_MANIFEST.json').write_text(json.dumps(
        {'release': 'b300-team-2026-10-28', 'files_sha256': files()}, indent=2) + '\n')
    print('Serving source manifest updated.')
