# VPN Hotspot Tray

A one-click tray switch that shares the machine's **VPN tunnel** with wifi clients,
without dropping the machine's own wifi connection.

Most "hotspot" buttons replace the wifi link: the machine stops being a client and
becomes an access point, and the internet it hands out is whatever the LAN gives it.
This one does both at once — the wifi radio keeps its normal association *and*
broadcasts a second network — and every packet from that second network is NATed
onto the machine's default route, which (when a VPN is up) is the tunnel.

## What you need

- Linux with NetworkManager and a wifi card whose driver accepts a second virtual
  interface in AP mode on the same channel as the station (`iw list` shows the
  interface combinations). One radio is enough.
- `hostapd`, `dnsmasq`, `iw`, `iptables`, `polkit`.
- Python 3 with PySide6 (`python3-pyside6.qtwidgets` on Debian/Ubuntu, or `pip install PySide6`).
- A connected 2.4 GHz wifi network, and an active VPN. Both are enforced by the app:
  * the AP must sit on the station's channel, so a 5 GHz station means 2.4 GHz-only
    phones cannot join;
  * without a tunnel, hotspot clients would silently use the ordinary internet
    connection, so the app refuses to start the hotspot instead of leaking.

## Install

```bash
sudo ./install.sh              # helper, polkit rule, app, launcher
sudo ./install.sh --autostart  # also start the tray on login
sudo ./install.sh --uninstall
```

`install.sh` puts the privileged helper at `/usr/local/bin/vpn-hotspot` and a polkit
rule that lets **the installing user only** run that one program without a password
prompt. Everything else stays as it was.

## Use

Start `VPN Hotspot Tray` from the application menu (or `python3 /usr/local/lib/vpn-hotspot-tray/vpn_hotspot_tray.py`).
The window shows the current state, the network name and password, the channel and
the device traffic leaves through, plus every reason the hotspot cannot run right now.
The tray icon is the same switch: grey = off, green = on, amber = half-up.

Verify from a client: join the network and open `https://api.ipify.org` — it must show
the VPN's exit address, not the ordinary internet one.

**Password**: the «تغییر رمز» button asks twice, saves the new key and restarts a
running hotspot with it (clients have to join again). Same thing from a terminal:
`printf '%s' 'new-password' > /tmp/psk && sudo vpn-hotspot passwd /tmp/psk`.
Allowed characters: letters, digits, dot, dash, underscore, 8–63 of them.

**Closing the window keeps the app in the tray** — the hotspot and the tray icon stay;
quit from the tray menu (or the window's close button never stops the hotspot).

## Settings

`/etc/vpn-hotspot.env`, read by the helper (`KEY=value`):

| key | default | meaning |
|---|---|---|
| `IF` | auto | station interface; detected from NetworkManager |
| `AP` | `ap0` | name of the access-point interface created on the same radio |
| `SSID` | `VPN-Hotspot` | network name |
| `PASS` | existing / random | password; reused from the current config, otherwise generated once and saved here |

## How it works

1. Reads the station's channel from `iw dev`.
2. Creates `ap0` on the same phy (`iw dev <station> interface add ap0 type __ap`) and
   runs `hostapd` on that channel with WPA2.
3. `10.42.0.1/24` on `ap0`, its own `dnsmasq` for DHCP/DNS, forwarding enabled.
4. NAT (`MASQUERADE`) and two `FORWARD` rules; clients exit through the default route.
5. Stops by killing `hostapd`/`dnsmasq`, removing the NAT rules and deleting the vif.

Tray actions run `pkexec /usr/local/bin/vpn-hotspot up|down`; the GUI itself never
runs privileged code.

## Troubleshooting

- **`up` fails with "driver refused a second vif"** — the card cannot do station + AP
  at the same time. Use a wired uplink, or a second adapter.
- **Clients join but have no internet** — check `ip route get 8.8.8.8`: it must name
  the tunnel device. The app shows this; a VPN that only marks some traffic will leak.
- **Large transfers stall, small pages work** — MTU. The tunnel is usually ~1420 while
  the AP is 1500; normally path-MTU discovery covers it, otherwise clamp MSS:
  `iptables -t mangle -A FORWARD -o <tun> -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu`
- **The wifi drops for a few seconds when the hotspot starts** — expected: starting an
  AP on the same radio can force one re-association. The helper waits for it and the
  app re-reads the state a few seconds later.

## Tests

```bash
python3 -m unittest discover -s tests   # state detection, no network or root needed
```

## License

MIT — see `LICENSE`.
