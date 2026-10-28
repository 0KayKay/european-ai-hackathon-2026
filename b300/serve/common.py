"""Small shared contracts for the B300 laptop and cluster tools."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def private(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Private path must not be a symlink')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise ValueError('Private directory belongs to another account')
    path.chmod(0o700)
    return path


def identifier(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise ValueError('Invalid identifier')
    return value


def time_seconds(value):
    if not re.fullmatch(r'\d{1,2}:[0-5]\d:[0-5]\d', value):
        raise ValueError('Time must be HH:MM:SS')
    h, m, s = map(int, value.split(':'))
    total = 3600 * h + 60 * m + s
    if not 1 <= total <= 14400:
        raise ValueError('B300 jobs must be bounded to at most four hours')
    return total


def verify_release(root=ROOT):
    entries = load(root / 'RELEASE_MANIFEST.json')['files_sha256']
    for name, digest in entries.items():
        path = root / name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink():
            raise ValueError('Unsafe release path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Release checksum mismatch: ' + name)
    return entries


def ready_session(job, state, endpoint, uid, jobs):
    """An old ready file never substitutes for a current owned Slurm allocation."""
    live = jobs.get(str(job))
    return bool(live and live['state'] == 'RUNNING' and live['uid'] == uid
                and state.get('ready') is True and state.get('phase') == 'ready'
                and str(state.get('job_id')) == str(job)
                and str(endpoint.get('job_id')) == str(job)
                and endpoint.get('owner_uid') == uid)
