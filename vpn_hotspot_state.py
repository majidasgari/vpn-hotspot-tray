"""Hotspot state detection — no Qt, no root, no side effects.

Reads NetworkManager, the kernel routing table and hostapd so the tray app (and
`python3 vpn_hotspot_state.py`) can report the truth before touching anything.
Every function takes an injectable `run` so tests can feed canned output.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field

AP_IFACE = os.environ.get("VH_AP", "ap0")
HELPER = os.environ.get("VH_HELPER", "/usr/local/bin/vpn-hotspot")
HOSTAPD_CONF = os.environ.get("VH_HOSTAPD_CONF", "/etc/hostapd/vpn-hotspot.conf")
# interface-name prefixes that mean "the default route leaves through a tunnel"
TUN_PREFIXES = ("tun", "tap", "wg", "utun", "ppp", "ipsec", "vpn")
FIVE_GHZ = 3000  # MHz; lower is 2.4GHz


def _run(*cmd: str, timeout: int = 6):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)


def wifi_station(run=_run):
    """The wifi device NetworkManager has associated (not the AP vif)."""
    rc, out, _ = run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status")
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        dev, typ, state = parts[0], parts[1], ":".join(parts[2:])
        if typ == "wifi" and state.startswith("connected") and dev != AP_IFACE:
            return dev
    return None


def wifi_ifaces(run=_run, iface=None):
    """{iface: {type, ssid, channel, freq}} parsed from `iw dev [<iface> info]`.

    Plain `iw dev` only lists the interfaces; ssid/channel need the per-interface
    form, so ask for that once the interface is known.
    """
    rc, out, _ = run("iw", "dev", iface, "info") if iface else run("iw", "dev")
    found: dict[str, dict] = {}
    cur = None
    for raw in out.splitlines():
        line = raw.strip()
        m = re.match(r"Interface (\S+)$", line)
        if m:
            cur = found.setdefault(m.group(1), {})
            continue
        if cur is None:
            continue
        m = re.match(r"type (\S+)", line)
        if m:
            cur["type"] = m.group(1)
        m = re.match(r"ssid (.+)$", line)
        if m:
            cur["ssid"] = m.group(1).strip()
        m = re.search(r"channel (\d+) \((\d+) MHz\)", line)
        if m:
            cur["channel"], cur["freq"] = int(m.group(1)), int(m.group(2))
    return found


def iface_info(iface, run=_run):
    """Full `iw dev <iface> info`, or {}."""
    return wifi_ifaces(run, iface).get(iface, {}) if iface else {}


def station_band(iface, run=_run):
    """(channel, freq) of an interface, or (None, None)."""
    info = iface_info(iface, run)
    return info.get("channel"), info.get("freq")


def vpn_device(run=_run):
    """Device the default route actually uses — `ip route get` honours policy
    routing, so a tunnel that lives in its own table is still reported."""
    rc, out, _ = run("ip", "route", "get", "8.8.8.8")
    m = re.search(r"\bdev (\S+)", out)
    dev = m.group(1) if m else None
    return dev if dev and dev.startswith(TUN_PREFIXES) else None


def hostapd_running(run=_run):
    rc, out, _ = run("pgrep", "-af", "hostapd")
    return any("vpn-hotspot" in line for line in out.splitlines())


def hostapd_conf(path=HOSTAPD_CONF):
    """ssid/psk from the hostapd config (mode 644, so no root needed)."""
    conf: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                key, sep, val = line.partition("=")
                if sep:
                    conf[key.strip()] = val.strip()
    except OSError:
        return {}
    return conf


@dataclass
class Status:
    station: str | None = None
    station_channel: int | None = None
    station_freq: int | None = None
    vpn: str | None = None
    ap_iface: str | None = None
    ap_ssid: str | None = None
    conf_ssid: str | None = None
    ap_channel: int | None = None
    hostapd: bool = False
    psk: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def state(self) -> str:
        if self.hostapd and self.ap_iface:
            return "on"
        if self.hostapd or self.ap_iface:
            return "partial"
        return "off"

    def blockers(self) -> list[str]:
        """Why `up` cannot work right now — the things a user does not know."""
        out = []
        if not self.station:
            out.append(
                "به هیچ شبکه‌ی وای‌فای وصل نیستی. اول به یک وای‌فای ۲.۴ هرتزی وصل شو؛ "
                "هات‌اسپات از همان آنتن و همان کانال ساخته می‌شود."
            )
        elif self.station_freq and self.station_freq >= FIVE_GHZ:
            out.append(
                "وای‌فای فعلی روی ۵ گیگاهرتز است. آنتن همزمان هات‌اسپات را هم می‌دهد، "
                "پس هر دو باید روی یک کانال باشند و گوشی‌های ۲.۴ هرتزی نمی‌توانند وصل شوند. "
                "به یک شبکه‌ی ۲.۴ هرتزی وصل شو."
            )
        if not self.vpn:
            out.append(
                "وی‌پی‌ان وصل نیست. هات‌اسپات ترافیک را از مسیر پیش‌فرض سیستم می‌فرستد، "
                "یعنی اینترنت عادی؛ اول وی‌پی‌ان را وصل کن."
            )
        return out

    def warnings(self) -> list[str]:
        """Things to show even when the hotspot is usable/running."""
        out = list(self.blockers()) if self.state == "off" else []
        if self.state == "on" and not self.vpn:
            out.append(
                "هات‌اسپات روشن است ولی وی‌پی‌ان وصل نیست — گوشی‌ها همین حالا "
                "از اینترنت عادی استفاده می‌کنند. وی‌پی‌ان را وصل کن یا هات‌اسپات را خاموش کن."
            )
        if self.state == "partial":
            out.append(
                "هات‌اسپات نیمه‌کاره بالاست (اینترفیس هست ولی سرویس hostapd نیست). "
                "یک بار خاموش و دوباره روشن کن."
            )
        return out

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "station": self.station,
            "station_channel": self.station_channel,
            "station_freq_mhz": self.station_freq,
            "vpn_device": self.vpn,
            "ap_iface": self.ap_iface,
            "ap_ssid": self.ap_ssid,
            "ap_channel": self.ap_channel,
            "hostapd": self.hostapd,
            "psk": self.psk,
            "blockers": self.blockers(),
        }


def ap_channel(detail: dict, conf: dict):
    """`iw dev <ap> info` omits ssid/channel for an AP vif on some drivers, so fall
    back to the config the helper wrote — hostapd refuses a channel it cannot use,
    so a running AP means that file is truthful."""
    if detail.get("channel"):
        return detail["channel"]
    raw = conf.get("channel", "")
    return int(raw) if raw.isdigit() else None


def snapshot(run=_run, conf_path=HOSTAPD_CONF) -> Status:
    station = wifi_station(run)
    chan, freq = station_band(station, run)
    ifaces = wifi_ifaces(run)
    ap = next((n for n in ifaces if ifaces[n].get("type") == "AP"), None)
    ap_detail = iface_info(ap, run)
    conf = hostapd_conf(conf_path)
    return Status(
        station=station,
        station_channel=chan,
        station_freq=freq,
        vpn=vpn_device(run),
        ap_iface=ap,
        ap_ssid=ap_detail.get("ssid") or conf.get("ssid"),
        ap_channel=ap_channel(ap_detail, conf),
        conf_ssid=conf.get("ssid"),
        hostapd=hostapd_running(run),
        psk=conf.get("wpa_passphrase"),
        raw=ifaces,
    )


if __name__ == "__main__":
    print(json.dumps(snapshot().as_dict(), ensure_ascii=False, indent=2))
