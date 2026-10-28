"""Local forwarding contracts; no SSH connection or model execution."""
import unittest
from nemotron_client import forward_specs


class TunnelContracts(unittest.TestCase):
    def setUp(self):
        self.endpoint = {'host_ip': '10.1.2.3', 'models': {
            'ultra': {'port': 18085}, 'safety': {'port': 18086}}}

    def test_both_models_use_two_forward_specs(self):
        rows = forward_specs(self.endpoint, 'both')
        self.assertEqual([r[0] for r in rows], ['ultra', 'safety'])
        self.assertEqual([r[1] for r in rows], [18085, 18086])

    def test_duplicate_or_ambiguous_local_ports_are_rejected(self):
        with self.assertRaises(ValueError):
            forward_specs(self.endpoint, 'both', local_port=19000)
        with self.assertRaises(ValueError):
            forward_specs(self.endpoint, 'both', ultra_port=19000, safety_port=19000)

    def test_loopback_destination_is_not_a_compute_node(self):
        self.endpoint['host_ip'] = '127.0.0.1'
        with self.assertRaises(ValueError):
            forward_specs(self.endpoint, 'both')


if __name__ == '__main__':
    unittest.main()
