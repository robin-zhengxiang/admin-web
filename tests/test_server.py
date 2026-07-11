import unittest
from unittest import mock

import server


def _ifconfig_output(*ips):
    return "\n".join(f"\tinet {ip} netmask 0xffffff00" for ip in ips)


class DetectVpnIpTests(unittest.TestCase):
    def _run_with(self, ifconfig_stdout):
        fake = mock.Mock(stdout=ifconfig_stdout)
        with mock.patch("server.subprocess.run", return_value=fake):
            return server._detect_vpn_ip()

    def test_finds_cgnat_range_ip(self):
        self.assertEqual(self._run_with(_ifconfig_output("192.168.1.5", "100.90.246.116")), "100.90.246.116")

    def test_lower_bound_of_range_matches(self):
        self.assertEqual(self._run_with(_ifconfig_output("100.64.0.1")), "100.64.0.1")

    def test_upper_bound_of_range_matches(self):
        self.assertEqual(self._run_with(_ifconfig_output("100.127.255.254")), "100.127.255.254")

    def test_just_outside_range_is_ignored(self):
        self.assertIsNone(self._run_with(_ifconfig_output("100.128.0.1")))
        self.assertIsNone(self._run_with(_ifconfig_output("100.63.255.255")))

    def test_no_vpn_interface_returns_none(self):
        self.assertIsNone(self._run_with(_ifconfig_output("127.0.0.1", "192.168.1.5")))

    def test_ifconfig_failure_returns_none(self):
        with mock.patch("server.subprocess.run", side_effect=OSError()):
            self.assertIsNone(server._detect_vpn_ip())


class BindHostsTests(unittest.TestCase):
    def test_always_includes_loopback(self):
        with mock.patch.object(server, "_detect_vpn_ip", return_value=None):
            self.assertEqual(server._bind_hosts(), ["127.0.0.1"])

    def test_adds_vpn_ip_when_present(self):
        with mock.patch.object(server, "_detect_vpn_ip", return_value="100.90.246.116"):
            self.assertEqual(server._bind_hosts(), ["127.0.0.1", "100.90.246.116"])

    def test_never_binds_all_interfaces(self):
        with mock.patch.object(server, "_detect_vpn_ip", return_value="100.90.246.116"):
            self.assertNotIn("0.0.0.0", server._bind_hosts())


if __name__ == "__main__":
    unittest.main()
