"""CPU-only diagnosis of the pinned runtime's shutdown timeout; never loads a model.

Run with Python 3.12+ (matching the container) and --source pointing at vllm/v1/utils.py from the measured
runtime. Only its SHA-pinned shutdown function is executed. The function is extracted with its annotations and receives CPU stand-ins for
its logger, process type and tree killer, without importing torch/vLLM. The
process-tree killer is represented by SIGKILL to a single owned leaf process;
this is a diagnostic harness, not an integration test of distributed shutdown.
"""
import argparse
import ast
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import tempfile
import time

EXPECTED_SHA256 = '4093f1c040a5f3def599bb044b72109301b970f70a5e5b622fd03a450a11e36a'


class Logger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def worker(ready, marker):
    def stop(signum, frame):
        time.sleep(0.2)  # bounded stand-in for resource teardown
        Path(marker).write_text('cleanup complete\n')
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    ready.send(True)
    ready.close()
    while True:
        time.sleep(1)


def shutdown_function(source):
    if hashlib.sha256(source.encode()).hexdigest() != EXPECTED_SHA256:
        raise ValueError('Unknown runtime source')
    tree = ast.parse(source)
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'shutdown']
    assert len(matches) == 1
    function = matches[0]
    tree = ast.parse('')
    tree.body.append(function)
    ast.fix_missing_locations(tree)
    namespace = {'time': time, 'logger': Logger(), 'BaseProcess': multiprocessing.process.BaseProcess}
    exec(compile(tree, 'pinned-vllm-v1-utils-shutdown', 'exec'), namespace)
    return namespace


def exercise(source, timeout, directory):
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    marker = directory / ('cleanup-' + str(timeout))
    process = context.Process(target=worker, args=(sender, str(marker)), name='owned-diagnostic-worker')
    namespace = shutdown_function(source)
    killed = []

    def kill_leaf(pid):
        assert pid == process.pid, 'Refusing to signal an unrelated process'
        killed.append(True)
        os.kill(pid, signal.SIGKILL)
    namespace['kill_process_tree'] = kill_leaf
    process.start()
    sender.close()
    try:
        assert receiver.poll(10) and receiver.recv(), 'Diagnostic worker failed to become ready'
        start = time.monotonic()
        namespace['shutdown']([process], timeout=timeout)
        elapsed = time.monotonic() - start
        process.join(5)
        assert not process.is_alive()
        return {'timeout_seconds': timeout, 'force_kill_called': bool(killed),
                'cleanup_completed': marker.exists(), 'exit_code': process.exitcode,
                'shutdown_elapsed_seconds': round(elapsed, 4)}
    finally:
        receiver.close()
        if process.is_alive():
            process.kill() if hasattr(process, 'kill') else os.kill(process.pid, signal.SIGKILL)
            process.join(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.read_text()
    with tempfile.TemporaryDirectory() as directory:
        results = [exercise(source, timeout, Path(directory)) for timeout in (0, 1)]
    passed = (results[0]['force_kill_called'] and not results[0]['cleanup_completed']
              and results[0]['exit_code'] == -signal.SIGKILL
              and not results[1]['force_kill_called'] and results[1]['cleanup_completed']
              and results[1]['exit_code'] == 0)
    report = {'runtime_source_sha256': EXPECTED_SHA256, 'runtime_function': 'vllm.v1.utils.shutdown',
              'test_kind': 'CPU-only execution of the exact pinned function with one owned leaf process',
              'model_or_gpu_loaded': False, 'cases': results, 'passed': bool(passed),
              'scope': 'Demonstrates zero-timeout process termination semantics. Does not establish that a positive timeout alone makes Slurm multi-node shutdown graceful.'}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
