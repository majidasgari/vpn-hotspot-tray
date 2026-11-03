#!/bin/bash
# Install the VPN hotspot: the root helper, a polkit rule so the tray app does
# not ask for a password every time, the app itself and its launcher.
#
#   sudo ./install.sh              # system parts + launcher for the caller
#   sudo ./install.sh --autostart  # ... and start the tray on login
#   sudo ./install.sh --uninstall
set -eu

SRC=$(cd "$(dirname "$0")" && pwd)
PREFIX=/usr/local
LIB="$PREFIX/lib/vpn-hotspot-tray"
HELPER="$PREFIX/bin/vpn-hotspot"
POLKIT=/etc/polkit-1/rules.d/49-vpn-hotspot.rules
ICON="$PREFIX/share/icons/hicolor/scalable/apps/vpn-hotspot-tray.svg"

[ "$(id -u)" -eq 0 ] || { echo "run me with sudo (or pkexec ./install.sh)" >&2; exit 1; }

# the desktop user this is being installed for: pkexec sets PKEXEC_UID, sudo sets SUDO_USER
TARGET_USER=${TARGET_USER:-$(id -un "${PKEXEC_UID:-${SUDO_USER:-root}}")}
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)

uninstall() {
  rm -f "$HELPER" "$POLKIT" "$ICON"
  rm -rf "$LIB"
  rm -f "$TARGET_HOME/.local/share/applications/vpn-hotspot-tray.desktop"
  rm -f "$TARGET_HOME/.config/autostart/vpn-hotspot-tray.desktop"
  echo "uninstalled (the hotspot itself, if running, is left alone: $HELPER down)"
}

if [ "${1:-}" = "--uninstall" ]; then uninstall; exit 0; fi

install -d "$LIB" "$PREFIX/bin" /etc/hostapd "$(dirname "$POLKIT")" "$(dirname "$ICON")"
install -m 755 "$SRC/bin/vpn-hotspot" "$HELPER"
install -m 644 "$SRC"/vpn_hotspot_state.py "$SRC"/vpn_hotspot_tray.py "$LIB/"
install -m 644 "$SRC/assets/vpn-hotspot-tray.svg" "$ICON"
command -v update-desktop-database >/dev/null && update-desktop-database -q || true

# pkexec normally asks for the admin password for every call; allow just this
# program for just this user, so the tray switch is one click.
cat >"$POLKIT" <<EOF
// allow $TARGET_USER to run the VPN hotspot helper without a password prompt
polkit.addRule(function (action, subject) {
  if (action.id == "org.freedesktop.policykit.exec" &&
      action.lookup("program") == "$HELPER" &&
      subject.user == "$TARGET_USER") {
    return polkit.Result.YES;
  }
});
EOF
chmod 644 "$POLKIT"

mkdir -p "$TARGET_HOME/.local/share/applications"
cat >"$TARGET_HOME/.local/share/applications/vpn-hotspot-tray.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=هات‌اسپات وی‌پی‌ان
Name[en]=VPN Hotspot Tray
Comment=Share the VPN tunnel with wifi clients
Exec=python3 $LIB/vpn_hotspot_tray.py
Icon=$ICON
Terminal=false
Categories=Network;Utility;
StartupNotify=false
EOF

if [ "${1:-}" = "--autostart" ]; then
  mkdir -p "$TARGET_HOME/.config/autostart"
  cat >"$TARGET_HOME/.config/autostart/vpn-hotspot-tray.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=VPN Hotspot Tray
Exec=python3 $LIB/vpn_hotspot_tray.py --hidden
Icon=$ICON
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
fi

chown -R "$TARGET_USER": "$TARGET_HOME/.local/share/applications" "$TARGET_HOME/.config/autostart" 2>/dev/null || true
echo "installed for $TARGET_USER"
echo "  helper : $HELPER"
echo "  app    : $LIB/vpn_hotspot_tray.py"
echo "  launcher: $TARGET_HOME/.local/share/applications/vpn-hotspot-tray.desktop"
echo
echo "requires PySide6: sudo apt install python3-pyside6.qtwidgets  (or: pip install PySide6)"
