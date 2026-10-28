"""Relocate the installed cubin package without changing its code or binaries."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def prepare(scratch, results):
    spec = importlib.util.find_spec('flashinfer_cubin')
    if spec is None or not spec.origin:
        raise RuntimeError('Expected the pinned image cubin package')
    source = Path(spec.origin).parent
    overlay = scratch / 'flashinfer-package'
    target = overlay / 'flashinfer_cubin'
    if not target.exists():
        shutil.copytree(source, target)
    files = []
    for item in source.rglob('*'):
        if item.is_file() and '__pycache__' not in item.parts:
            rel = item.relative_to(source)
            with item.open('rb') as f:
                original = hashlib.file_digest(f, 'sha256').hexdigest()
            with (target / rel).open('rb') as f:
                copied = hashlib.file_digest(f, 'sha256').hexdigest()
            if original != copied:
                raise RuntimeError('Cubin relocation changed an installed file: ' + str(rel))
            files.append({'path': str(rel), 'sha256': original})
    env = dict(os.environ)
    workspace = scratch / 'cache' / 'flashinfer-preflight'
    workspace.mkdir(parents=True, exist_ok=True)
    env['FLASHINFER_WORKSPACE_BASE'] = str(workspace)
    env['PYTHONPATH'] = str(overlay) + os.pathsep + env.get('PYTHONPATH', '')
    env['FLASHINFER_CUBIN_DIR'] = str(target / 'cubins')
    code = ("import json,pathlib,flashinfer_cubin; from flashinfer.jit import env; "
            "p=pathlib.Path(env.FLASHINFER_CUBIN_DIR); "
            "assert p.resolve().is_relative_to(pathlib.Path(" + repr(str(overlay)) + ").resolve()); "
            "test=p/'flashinfer/trtllm/batched_gemm/.write-check'; test.parent.mkdir(parents=True,exist_ok=True); "
            "test.write_text('probe'); test.unlink(); "
            "from flashinfer.jit.fused_moe import gen_trtllm_gen_fused_moe_sm100_module; "
            "gen_trtllm_gen_fused_moe_sm100_module(); "
            "print(json.dumps({'resolved_cubin_dir':str(p),'trtllm_moe_spec_preflight':True}))")
    check = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=300)
    (results / 'cubin-preflight.log').write_text(check.stdout + check.stderr)
    record = {'source_package': str(source), 'relocated_package': str(target),
              'unchanged_source_and_binary_files': files, 'preflight_exit_code': check.returncode,
              'scope': 'Private byte-identical package copy; no kernel math, checkpoint, classifier prompt, or global image modification.'}
    (results / 'cubin-relocation.json').write_text(json.dumps(record, indent=2) + '\n')
    if check.returncode:
        raise RuntimeError('Writable FlashInfer package preflight failed; inspect cubin-preflight.log')
    return {'PYTHONPATH': env['PYTHONPATH'], 'FLASHINFER_CUBIN_DIR': env['FLASHINFER_CUBIN_DIR']}
