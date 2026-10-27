"""The opt-in component infrastructure/leonardo, rendered with the GKE overlay exactly as enabling it would.

The cluster link PoC deploys the component on infrastructure/base; this checks what only the GKE overlay
can break, such as its agentgateway patch dropping the router token mount. Needs kubectl (preinstalled on
GitHub's runners); skipped without it.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

INFRASTRUCTURE = Path(__file__).resolve().parents[2] / 'infrastructure'


def render(directory):
    result = subprocess.run(['kubectl', 'kustomize', str(directory)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return {document.split('\nkind: ', 1)[1].split('\n', 1)[0] + '/' + document.split('\n  name: ', 1)[1].split('\n', 1)[0]:
            document for document in result.stdout.split('\n---\n')}


@unittest.skipIf(shutil.which('kubectl') is None, 'needs kubectl')
class GkeWithLeonardo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as temp:
            for name in ('base', 'gke', 'leonardo'):
                shutil.copytree(str(INFRASTRUCTURE / name), str(Path(temp) / name))
            with (Path(temp) / 'gke/kustomization.yaml').open('a') as handle:
                handle.write('components:\n  - ../leonardo\n')
            cls.before = render(INFRASTRUCTURE / 'gke')
            cls.after = render(Path(temp) / 'gke')

    def test_agentgateway_mounts_the_router_token_it_reads(self):
        deployment = self.after['Deployment/agentgateway']
        self.assertIn('mountPath: /var/run/leonardo-router', deployment)
        self.assertIn('secretName: leonardo-router', deployment)
        self.assertNotIn('/var/run/google', deployment)  # the GKE overlay's own deletion still applies
        config = [d for k, d in self.after.items() if k.startswith('ConfigMap/agentgateway-config')]
        self.assertEqual(len(config), 1)
        self.assertIn('file: /var/run/leonardo-router/token', config[0])
        self.assertIn('name: ' + config[0].split('\n  name: ', 1)[1].split('\n', 1)[0], deployment)

    def test_link_is_served_on_the_public_host(self):
        ingress = self.after['Ingress/frontend']
        self.assertIn('path: /leonardo-link', ingress)
        self.assertIn('european-ai-hackathon-2026.codify.ch', ingress)
        self.assertIn('timeoutSec: 86400', self.after['BackendConfig/leonardo-link'])


if __name__ == '__main__':
    unittest.main()
