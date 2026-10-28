"""User client for the authenticated B300 Nemotron endpoints. Standard library only."""
import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
from validation import SAFETY_POLICY, completion, validate


def forward_specs(document, kind, local_port=None, ultra_port=None, safety_port=None):
    address = ipaddress.ip_address(document['host_ip'])
    if not address.is_private or address.is_loopback or address.is_unspecified:
        raise ValueError('Expected private compute-node IP')
    if kind == 'both' and local_port:
        raise ValueError('Use --local-ultra-port and --local-safety-port with --kind both')
    kinds = ('ultra', 'safety') if kind == 'both' else (kind,)
    output = []
    for selected in kinds:
        remote_port = document['models'][selected]['port']
        chosen = ultra_port if selected == 'ultra' else safety_port
        chosen = chosen or local_port or remote_port
        if any(type(port) is not int or not 1024 <= port <= 65535 for port in (remote_port, chosen)):
            raise ValueError('Invalid forwarding port')
        output.append((selected, chosen, '127.0.0.1:%s:%s:%s' % (chosen, address, remote_port)))
    if len({port for _, port, _ in output}) != len(output):
        raise ValueError('Each model needs a different local port')
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['key', 'tunnel', 'chat', 'safety', 'test'])
    p.add_argument('--kind', choices=['ultra', 'safety', 'both'], default='ultra')
    p.add_argument('--endpoint', type=Path, default=Path('endpoint.json'))
    p.add_argument('--key-file', type=Path, default=Path('.runtime/client.api-key'))
    p.add_argument('--local-port', type=int)
    p.add_argument('--local-ultra-port', type=int)
    p.add_argument('--local-safety-port', type=int)
    p.add_argument('--ssh-command-file', type=Path, help='Private file containing the current AXIS-issued ssh command')
    p.add_argument('--text', help='One message; otherwise read interactively')
    a = p.parse_args()
    if a.action == 'key':
        a.key_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = getpass.getpass('Session API key: ').strip()
        if not value:
            raise ValueError('Empty key')
        fd = os.open(a.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(value + '\n')
        print('Private API key saved.')
        return
    document = json.loads(a.endpoint.read_text())
    if a.action == 'tunnel':
        if not a.ssh_command_file:
            p.error('--ssh-command-file is required for an AXIS tunnel')
        forwards = forward_specs(document, a.kind, a.local_port, a.local_ultra_port, a.local_safety_port)
        args = shlex.split(a.ssh_command_file.read_text())
        if len(args) != 4 or args[0] != 'ssh' or args[2] != '-l' or args[1].startswith('-'):
            raise ValueError('Expected the AXIS ssh HOST -l USER command')
        print('Forwarding %s through one SSH connection. Keep this terminal open.' %
              ', '.join('%s localhost:%s' % (kind, port) for kind, port, _ in forwards), flush=True)
        command = ['ssh', '-o', 'StrictHostKeyChecking=yes', '-o', 'ExitOnForwardFailure=yes',
                   '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=30',
                   '-o', 'ServerAliveCountMax=3', '-N']
        for _, _, spec in forwards:
            command += ['-L', spec]
        result = subprocess.run(command + [args[1], '-l', args[3]])
        if result.returncode:
            raise RuntimeError('SSH forward ended with code %s; check or refresh the private AXIS connection.' % result.returncode)
        return
    if a.kind == 'both':
        p.error('--kind both is available only for tunnel')
    entry = document['models'][a.kind]
    local_port = a.local_port or entry['port']
    if not 1024 <= local_port <= 65535:
        raise ValueError('Invalid local port')
    key = a.key_file.read_text().strip()
    base = 'http://127.0.0.1:%s/v1' % local_port
    if a.action == 'test':
        print(json.dumps(validate(base, key, entry['model'], a.kind), indent=2))
        return
    if a.action == 'safety' and a.kind != 'safety':
        p.error('Use --kind safety for safety classification')
    while True:
        try:
            text = a.text if a.text is not None else input('You: ')
        except (EOFError, KeyboardInterrupt):
            break
        if not text.strip():
            break
        extra = {'chat_template_kwargs': {'enable_thinking': False}} if a.kind == 'ultra' else {}
        content = SAFETY_POLICY.format(text=text) if a.kind == 'safety' else text
        response = completion(base, key, entry['model'], content, max_tokens=1024, **extra)
        print(response['choices'][0]['message'].get('content') or '[No final content]')
        if a.text is not None:
            break


if __name__ == '__main__':
    main()
