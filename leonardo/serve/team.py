"""Team-folder commands, run on a Leonardo login node; cli.py runs them for you over SSH.

Every command takes --base, the team folder (e.g. '$WORK/team_vllm'; variables are expanded here).

  init      create the team folder and its inference-site.json
  share     give named users access to the team folder (POSIX ACLs), or take it away
  install   download runtimes and model weights for profiles (CPU-queue jobs with internet)
  up        prepare and submit a model server (inference.py prepare + submit)
  status    your servers, router and install jobs, and the team registry
  logs      show the log of a job, a profile's newest run or the router
  down      cancel servers by profile, served name or job id, or the router
  router    start or stop your router, or print its token
  diagnose  submit a probe, reachability or NCCL check job
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys

import inference
from inference import ROOT, identifier, load, load_site, save, site_base, user_dir
import registry
import router

RELEASE = ROOT.parent  # holds serve/, install/ and diagnostics/


def cpu_partition(site):
    return site.get('cpu_partition', 'lrd_all_serial')  # CPU-only, but with internet


def script_job(base, site, script, arguments, name, output, partition, dependency=None, environment=None):
    """Submit a job script from install/ or diagnostics/ with the site's account."""
    account = os.environ.get('SBATCH_ACCOUNT') or site['account']
    if account == 'YOUR_PROJECT':
        raise ValueError('Set "account" in {}/inference-site.json'.format(base))
    exports = ['ALL', 'BASE=' + str(base), 'LEONARDO_RELEASE=' + str(RELEASE)]
    exports += ['{}={}'.format(k, v) for k, v in sorted((environment or {}).items())]
    if any(',' in value for value in exports):
        raise ValueError('Values passed to jobs must not contain commas')
    command = ['sbatch', '--parsable', '--job-name=' + name, '--account=' + identifier(account),
               '--output=' + str(output), '--export=' + ','.join(exports), '--partition=' + identifier(partition)]
    if dependency:
        command.append('--dependency=afterok:' + dependency)
    marker = inference.private_dir(user_dir(base)) / ('.submitting-' + name)
    job = inference.sbatch(command + [str(script)] + list(arguments), marker)
    print('Submitted {} as job {}; log: {}'.format(name, job, str(output).replace('%x-%j', name + '-' + job)))
    return job


def init(args):
    base = site_base(args.base)
    site_file = base / 'inference-site.json'
    if site_file.exists() and not args.force:
        raise ValueError('{} exists already; edit it, or use --force to overwrite it'.format(site_file))
    site = load(ROOT / 'site.example.json')
    site['account'] = identifier(args.account)
    for key in ('partition', 'qos', 'cpu_partition'):
        if getattr(args, key):
            site[key] = identifier(getattr(args, key))
    base.mkdir(parents=True, exist_ok=True)
    for name in ('runtimes', 'checkpoints', 'endpoints', 'users', 'logs', 'tools'):
        (base / name).mkdir(exist_ok=True)
    save(site_file, site)
    inference.private_dir(user_dir(base))
    print('Team folder ready: {} (account {}).'.format(base, site['account']))
    print('Only you can use it so far; give teammates access with: cli.py share <login> ...')


def setfacl(arguments, targets):
    for start in range(0, len(targets), 500):
        subprocess.check_call(['setfacl'] + arguments + targets[start:start + 500])


def share(args):
    """Grant (or revoke) named users rwX on the team folder, except other users' personal folders."""
    base = site_base(args.base)
    load_site(base)
    for name in args.users:
        try:
            pwd.getpwnam(name)
        except KeyError:
            raise ValueError('No such user on Leonardo: ' + name)
    files, directories = [], []
    for root, dirnames, filenames in os.walk(str(base)):
        directories.append(root)
        files += [os.path.join(root, f) for f in filenames]
        if Path(root) == base / 'users':
            dirnames[:] = []  # users/<login> stays private to its owner
    inherited = [d for d in directories if Path(d) != base / 'users']
    if args.revoke:
        entries = ','.join('u:' + name for name in args.users)
        setfacl(['-x', entries], files + directories)
        setfacl(['-d', '-x', entries], inherited)
        print('Removed access for: ' + ', '.join(args.users))
        return
    entries = ','.join('u:{}:rwX'.format(name) for name in args.users)
    setfacl(['-m', entries], files + directories)
    # Default entries make new files and folders inherit the access.
    setfacl(['-d', '-m', entries], inherited)
    # Users must be able to pass through the folders above $BASE; add that where we own them.
    for parent in base.parents:
        if parent.stat().st_uid != os.getuid():
            print('Note: {} belongs to someone else; the users also need to be able to enter it.'.format(parent))
            break
        setfacl(['-m', ','.join('u:{}:--x'.format(name) for name in args.users)], [str(parent)])
    print('Shared {} with: {}'.format(base, ', '.join(args.users)))


def install(args):
    base = site_base(args.base)
    site = load_site(base)
    jobs = router.active_jobs() or {}
    running = {j['name']: j['job_id'] for j in jobs.values()}
    logs = base / 'logs' / '%x-%j.out'
    runtime_jobs = {}
    if not args.profiles and not args.link_client:
        raise ValueError('Name profiles to install (e.g. qwen06b), and/or --link-client')
    for name in args.profiles:
        profile = inference.profile_check(load(ROOT / 'profiles' / (identifier(name) + '.json')))
        runtime = profile['runtime']
        if (inference.runtime_dir(base, site, runtime) / 'runtime.json').exists():
            print('runtime {}: installed'.format(runtime))
        elif runtime not in runtime_jobs:
            job_name = 'install-runtime-' + runtime
            runtime_jobs[runtime] = running.get(job_name) or script_job(
                base, site, RELEASE / 'install/runtime.sbatch', [runtime], job_name, logs, cpu_partition(site))
        checkpoint = profile['checkpoint']
        if (inference.checkpoint_dir(base, site, checkpoint) / 'download_manifest.json').exists():
            print('checkpoint {}: installed'.format(checkpoint))
        elif 'install-checkpoint-' + checkpoint in running:
            print('checkpoint {}: job {} is already running'.format(checkpoint, running['install-checkpoint-' + checkpoint]))
        else:
            # The download runs inside the profile's own image, so it waits for that runtime.
            script_job(base, site, RELEASE / 'install/checkpoint.sbatch', [name], 'install-checkpoint-' + checkpoint,
                       logs, cpu_partition(site), dependency=runtime_jobs.get(runtime))
    if args.link_client:
        tool = inference.catalog()['tools']['chisel']
        if (base / 'tools' / ('chisel-' + tool['version'])).exists():
            print('cluster link client: installed')
        else:
            script_job(base, site, RELEASE / 'install/link_client.sbatch', [], 'install-link-client', logs, cpu_partition(site))
    print('Follow progress with: cli.py status, and cli.py logs <job id>.')


def up(args):
    run = inference.prepare_run(args.profile, args.base)
    print('Run directory: ' + str(run), flush=True)
    command = [sys.executable, str(run / 'inference.py'), 'submit']
    for flag in ('reservation', 'qos', 'time'):
        if getattr(args, flag):
            command += ['--' + flag, getattr(args, flag)]
    subprocess.check_call(command, cwd=str(run))


def status(args):
    base = site_base(args.base)
    if not (base / 'inference-site.json').exists():
        if args.json:
            print(json.dumps({'initialised': False}))
        else:
            print('{} is not a team folder yet. Create it with: cli.py init --account <project>'.format(base))
        return
    site = load_site(base)
    jobs = router.active_jobs()
    report = {'initialised': True, 'jobs_known': jobs is not None, 'servers': [], 'router': router.state(base, jobs),
              'installs': [j for j in (jobs or {}).values() if j['name'].startswith('install-')],
              'installed': {'runtimes': sorted(p.parent.name for p in (base / 'runtimes').glob('*/runtime.json')),
                            'checkpoints': sorted(p.parent.name for p in (base / 'checkpoints').glob('*/download_manifest.json'))}}
    for run in router.own_runs(base):
        if run['job'] and (jobs is None or run['job'] in jobs):
            job = (jobs or {}).get(run['job'], {})
            report['servers'].append({'profile': run['profile']['id'], 'model': run['profile']['served_model'],
                                      'job_id': run['job'], 'state': job.get('state', 'unknown'),
                                      'elapsed': job.get('elapsed', '?'), 'limit': job.get('limit', '?'),
                                      'ready': run['endpoint'] is not None, 'run': str(run['run']),
                                      'endpoint': run['endpoint'] and '{}:{}'.format(run['endpoint']['host'], run['endpoint']['port'])})
    shared = registry.directory({'base': str(base), 'site': site})
    report['registry'] = registry.records(shared) if shared else None
    if args.json:
        print(json.dumps(report, indent=2))
        return
    if jobs is None:
        print('WARNING: squeue failed; showing runs that may already have ended.')
    print('Installed: runtimes {}; checkpoints {}'.format(', '.join(report['installed']['runtimes']) or 'none',
                                                         ', '.join(report['installed']['checkpoints']) or 'none'))
    for j in report['installs']:
        print('  installing: {name} job {job_id} {state} {elapsed}'.format(**j))
    print('Your model servers:')
    for s in report['servers']:
        print('  {profile:<12} job {job_id:<10} {state:<10} {elapsed}/{limit}  {0}'.format(
            ('READY ' + s['model'] + ' at ' + s['endpoint']) if s['ready'] else 'starting (not ready yet)', **s))
    if not report['servers']:
        print('  none')
    print('Your router: ' + (report['router']['summary'] if report['router'] else 'not running'))
    if report['registry'] is not None:
        print('Team registry (ready servers of everyone sharing this folder):')
        for r in report['registry']:
            r = {k: r.get(k, '?') for k in ('name', 'owner', 'host', 'port', 'job_id', 'slurm_expected_end')}
            print('  {name:<36} {owner:<10} {host}:{port} job {job_id} until {slurm_expected_end}'.format(**r))
        if not report['registry']:
            print('  none')


def find_log(base, target):
    mine = user_dir(base)
    if target == 'router':
        candidates = list((mine / 'router').glob('*.out'))
    elif target.isdigit():
        patterns = [(mine / 'runs', '*/*-{}.out'), (mine / 'router', '*-{}.out'), (mine / 'logs', '*-{}.out'),
                    (base / 'logs', '*-{}.out')]
        candidates = [p for root, pattern in patterns for p in root.glob(pattern.format(target))]
    else:
        runs = [r for r in router.own_runs(base) if target in (r['profile']['id'], r['profile']['served_model'])]
        candidates = list(runs[0]['run'].glob('*.out')) if runs else []
    if not candidates:
        raise ValueError('No log found for {} (yet). A pending job has no log until it starts.'.format(target))
    return max(candidates, key=lambda p: p.stat().st_mtime)


def logs(args):
    path = find_log(site_base(args.base), args.target)
    print('==> {} <=='.format(path), flush=True)
    command = ['tail', '-n', str(args.lines)] + (['-f'] if args.follow else []) + [str(path)]
    os.execvp('tail', command)


def down(args):
    base = site_base(args.base)
    if args.target == 'router':
        router.down()
        return
    jobs = router.active_jobs()
    targets = []
    for run in router.own_runs(base):
        p = run['profile']
        if run['job'] and args.target in (p['id'], p['served_model'], run['job']):
            if jobs is None or run['job'] in jobs:
                targets.append(run['job'])
    if not targets:
        raise ValueError('No running server of yours matches {}; see status'.format(args.target))
    subprocess.check_call(['scancel'] + targets)
    print('Cancelled job(s) ' + ' '.join(targets))


def router_command(args):
    base = site_base(args.base)
    if args.action == 'up':
        router.up(base, load_site(base), args.hours, args.restart, router.active_jobs())
    elif args.action == 'down':
        router.down()
    elif args.action == 'token':
        router.token(base, 'set' if args.set else 'ensure')
        if not args.set:
            router.token(base, 'show')
    elif args.action == 'link-auth':
        router.set_link_auth(base)


def diagnose(args):
    base = site_base(args.base)
    site = load_site(base)
    output = inference.private_dir(inference.private_dir(user_dir(base)) / 'logs') / '%x-%j.out'
    script = RELEASE / 'diagnostics' / {'probe': 'probe.sbatch', 'reach': 'reach.sbatch', 'nccl': 'nccl_check.sbatch'}[args.check]
    environment = {}
    if args.check == 'reach':
        environment = {k: v for k, v in (('TARGET', args.target), ('PROBE_PROXY', args.proxy), ('LINK_URL', args.link_url)) if v}
    elif args.check == 'probe' and args.proxy:
        environment = {'PROBE_PROXY': args.proxy}
    elif args.check == 'nccl':
        environment = {'RUNTIME': args.runtime}
    name = {'probe': 'probe', 'reach': 'reach', 'nccl': 'nccl-check'}[args.check]
    # reach tests what the router's CPU queue can see; probe and NCCL need GPUs (debug QoS, set in the scripts).
    partition = cpu_partition(site) if args.check == 'reach' else site['partition']
    script_job(base, site, script, [], name, output, partition, environment=environment)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command')

    def command(name, help_text):
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument('--base', required=True, help="team folder, e.g. '$WORK/team_vllm'")
        return p
    p = command('init', 'create the team folder and inference-site.json')
    p.add_argument('--account', required=True, help='Slurm project account')
    p.add_argument('--partition', help='GPU partition (default boost_usr_prod)')
    p.add_argument('--qos', help='default GPU QoS (default boost_qos_lprod)')
    p.add_argument('--cpu-partition', help='CPU partition with internet (default lrd_all_serial)')
    p.add_argument('--force', action='store_true', help='overwrite an existing inference-site.json')
    p = command('share', 'give users access to the team folder (or --revoke it)')
    p.add_argument('users', nargs='+', metavar='LOGIN')
    p.add_argument('--revoke', action='store_true')
    p = command('install', 'download runtimes and weights for profiles')
    p.add_argument('profiles', nargs='*', metavar='PROFILE')
    p.add_argument('--link-client', action='store_true', help='also the chisel client for the cluster link')
    p = command('up', 'prepare and submit a model server')
    p.add_argument('profile')
    p.add_argument('--qos')
    p.add_argument('--time')
    p.add_argument('--reservation')
    p = command('status', 'show servers, router, installs and the team registry')
    p.add_argument('--json', action='store_true')
    p = command('logs', 'show a log: job id, profile, served name or "router"')
    p.add_argument('target')
    p.add_argument('--lines', type=int, default=60)
    p.add_argument('--follow', action='store_true')
    p = command('down', 'cancel servers by profile, served name or job id, or "router"')
    p.add_argument('target')
    p = command('router', 'start/stop the router, print its token, or store the cluster link password')
    p.add_argument('action', choices=['up', 'down', 'token', 'link-auth'])
    p.add_argument('--hours', type=float, default=12)
    p.add_argument('--restart', action='store_true')
    p.add_argument('--set', action='store_true', help='token: replace it with the one on stdin')
    p = command('diagnose', 'submit a probe, reachability or NCCL check job')
    p.add_argument('check', choices=['probe', 'reach', 'nccl'])
    p.add_argument('--target', help='reach: host:port of a running probe')
    p.add_argument('--proxy', help='probe/reach: also try this HTTP proxy')
    p.add_argument('--link-url', help='reach: also check this cluster link URL')
    p.add_argument('--runtime', default='vllm-0.31.0', help='nccl: runtime image to use')
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    {'init': init, 'share': share, 'install': install, 'up': up, 'status': status, 'logs': logs, 'down': down,
     'router': router_command, 'diagnose': diagnose}[args.command](args)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
