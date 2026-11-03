"""State detection tests — no Qt, no root, no real network.

Run: python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vpn_hotspot_state as hs  # noqa: E402

NMCLI = """wlp69s0:wifi:connected
utun420:tun:connected (externally)
ap0:wifi:unmanaged
"""

IW_DEV = """phy#0
\tInterface ap0
\t\ttype AP
\t\tssid VPN-Hotspot
\t\tchannel 10 (2457 MHz), width: 20 MHz, center1: 2457 MHz
\tInterface wlp69s0
\t\ttype managed
\t\tchannel 10 (2457 MHz), width: 20 MHz, center1: 2457 MHz
"""

IW_DEV_NO_AP = """phy#0
\tInterface wlp69s0
\t\ttype managed
\t\tchannel 10 (2457 MHz), width: 20 MHz, center1: 2457 MHz
"""

IW_DEV_5G = IW_DEV.replace("channel 10 (2457 MHz)", "channel 44 (5220 MHz)")


def runner(nmcli=NMCLI, iw=IW_DEV, route="8.8.8.8 dev utun420 table 51820 src 100.72.241.125\n",
           hostapd=True, missing=()):
    def run(*cmd, **_):
        if cmd[0] in missing:
            return 127, "", "not found"
        if cmd[0] == "nmcli":
            return 0, nmcli, ""
        if cmd[0] == "iw":
            return 0, iw, ""
        if cmd[0] == "ip":
            return 0, route, ""
        if cmd[0] == "pgrep":
            return 0, "76834 hostapd -B -P /run/x.pid /etc/hostapd/vpn-hotspot.conf\n" if hostapd else "", ""
        return 0, "", ""
    return run


class StatusTests(unittest.TestCase):
    def conf(self, text="ssid=VPN-Hotspot\nchannel=10\nwpa_passphrase=maxvpn1404\n"):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        self.addCleanup(os.remove, path)
        return path

    def snap(self, **kw):
        return hs.snapshot(run=runner(**kw), conf_path=self.conf())

    def test_healthy(self):
        s = self.snap()
        self.assertEqual(s.state, "on")
        self.assertEqual(s.station, "wlp69s0")
        self.assertEqual(s.vpn, "utun420")
        self.assertEqual((s.ap_ssid, s.ap_channel), ("VPN-Hotspot", 10))
        self.assertEqual(s.psk, "maxvpn1404")
        self.assertEqual(s.blockers(), [])

    def test_channel_falls_back_to_conf(self):
        """Real drivers omit ssid/channel from `iw dev <ap> info`."""
        iw = "phy#0\n\tInterface ap0\n\t\ttype AP\n\tInterface wlp69s0\n\t\ttype managed\n"
        s = hs.snapshot(run=runner(iw=iw), conf_path=self.conf("ssid=VPN-Hotspot\nchannel=6\n"))
        self.assertEqual(s.ap_channel, 6)
        self.assertEqual(s.ap_ssid, "VPN-Hotspot")
        self.assertEqual(s.state, "on")

    def test_off_without_ap(self):
        s = self.snap(iw=IW_DEV_NO_AP, hostapd=False)
        self.assertEqual(s.state, "off")

    def test_partial_when_hostapd_dead(self):
        s = self.snap(hostapd=False)
        self.assertEqual(s.state, "partial")
        self.assertTrue(any("نیمه" in w for w in s.warnings()))

    def test_no_wifi_blocks(self):
        s = self.snap(nmcli="utun420:tun:connected (externally)\n", iw=IW_DEV_NO_AP, hostapd=False)
        self.assertIsNone(s.station)
        self.assertTrue(any("۲.۴" in b for b in s.blockers()))

    def test_5ghz_station_blocks(self):
        s = self.snap(iw=IW_DEV_5G.replace("ssid VPN-Hotspot\n", ""), hostapd=False)
        self.assertEqual(s.station_freq, 5220)
        self.assertTrue(any("۵ گیگاهرتز" in b for b in s.blockers()))

    def test_no_vpn_blocks(self):
        s = self.snap(route="8.8.8.8 via 192.168.1.1 dev wlp69s0\n", hostapd=False, iw=IW_DEV_NO_AP)
        self.assertIsNone(s.vpn)
        self.assertTrue(any("وی‌پی‌ان" in b for b in s.blockers()))

    def test_running_without_vpn_warns(self):
        s = self.snap(route="8.8.8.8 via 192.168.1.1 dev wlp69s0\n")
        self.assertEqual(s.state, "on")
        self.assertTrue(any("وی‌پی‌ان وصل نیست" in w for w in s.warnings()))

    def test_missing_tools_do_not_crash(self):
        s = self.snap(missing=("nmcli", "iw", "ip", "pgrep"))
        self.assertEqual(s.state, "off")
        self.assertIsNone(s.station)
        self.assertIsNone(s.vpn)


if __name__ == "__main__":
    unittest.main()
