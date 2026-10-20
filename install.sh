#!/usr/bin/env bash
# Install the Password Manager for this user (no sudo needed):  bash install.sh
set -euo pipefail
if [ "$(id -u)" = 0 ]; then
  echo "Run this WITHOUT sudo:  bash $0"
  echo "(With sudo it would install into root's home folder, not yours. Only the separate sudo-stick/install.sh step uses sudo.)"
  exit 1
fi
# A terminal opened some other way (su, ssh, a TTY) may not know where your desktop session's service manager is:
# point it there, so "systemctl --user" works ("Failed to connect to user scope bus via local transport" otherwise).
if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then export XDG_RUNTIME_DIR="/run/user/$(id -u)"; fi
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "${XDG_RUNTIME_DIR:-/nonexistent}/bus" ]; then export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"; fi
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/jblol-password-manager"
mkdir -p "$DEST" "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.config/systemd/user" "$HOME/Documents/Vault"
cp "$SRC/jblol_pm.py" "$SRC/presence.py" "$SRC/icon.svg" "$DEST/"; rm -rf "$DEST/sudo-stick" "$DEST/pmapp"; cp -r "$SRC/sudo-stick" "$SRC/pmapp" "$DEST/"
find "$DEST" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
printf '#!/usr/bin/env bash\nexec python3 "%s/jblol_pm.py" "$@"\n' "$DEST" > "$HOME/.local/bin/jblol-pm"; chmod +x "$HOME/.local/bin/jblol-pm"
cat > "$HOME/.local/share/applications/jblol-password-manager.desktop" <<D
[Desktop Entry]
Type=Application
Name=Password Manager
Comment=jblol.systems password manager (opens with your USB stick)
Exec=$HOME/.local/bin/jblol-pm
Icon=$DEST/icon.svg
Categories=Utility;Security;
StartupWMClass=jblol-password-manager
Terminal=false
D
# the browser extension, in a normal (non-hidden) folder so snap browsers can read it, plus a zip for Firefox signing
rm -rf "$HOME/PasswordManager-Extension"; cp -r "$SRC/browser-extension" "$HOME/PasswordManager-Extension"
python3 - <<'PY'
import zipfile, pathlib
src = pathlib.Path.home() / "PasswordManager-Extension"
with zipfile.ZipFile(pathlib.Path.home() / "PasswordManager-Extension.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(src.iterdir()):
        if f.name not in ("README.txt", "pairing.json"):
            z.write(f, f.name)
PY
python3 - <<'PY'
import json, pathlib
home = pathlib.Path.home()
try:
    s = json.loads((home / ".config" / "jblol-password-manager" / "settings.json").read_text())
    if s.get("bridge_token"):
        f = home / "PasswordManager-Extension" / "pairing.json"
        f.write_text(json.dumps({"token": s["bridge_token"], "port": int(s.get("bridge_port", 47619))}))
        f.chmod(0o600)
except Exception:
    pass                                   # the app writes it itself the first time it runs
PY
install -m 755 "$SRC/stick-helper/recall-vault-watch.sh" "$HOME/.local/bin/recall-vault-watch"
install -m 644 "$SRC/stick-helper/recall-vault.service" "$HOME/.config/systemd/user/recall-vault.service"
install -m 644 "$SRC/stick-helper/jblol-pm-presence.service" "$HOME/.config/systemd/user/jblol-pm-presence.service"
if systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user daemon-reload && systemctl --user enable --now recall-vault.service
  systemctl --user enable jblol-pm-presence.service && systemctl --user restart jblol-pm-presence.service
else
  echo "Couldn't reach your desktop session's services from this terminal, so the background helpers weren't started."
  echo "Everything else is installed. Run this again from a terminal opened on the desktop itself (not over ssh, not with sudo)."
fi
python3 -c "import PySide6" 2>/dev/null || echo "PySide6 is missing: install it with  sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtnetwork python3-pyside6.qtsvg"
python3 -c "import cryptography" 2>/dev/null || echo "The iPhone copy needs: sudo apt install python3-cryptography"
command -v keepassxc.cli >/dev/null || command -v keepassxc-cli >/dev/null || echo "KeePassXC is missing: sudo snap install keepassxc   (the Password Manager uses its engine)"
echo "Installed. Open it from the applications menu (Password Manager) or run: jblol-pm"
