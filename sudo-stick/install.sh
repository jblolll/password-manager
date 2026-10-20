#!/usr/bin/env bash
# sudo:  sudo bash install.sh
# Also unlock your login/lock screen with the stick (GNOME/GDM):  sudo bash install.sh --login
# The "Authentication required" pop-ups (installing apps in Software, system settings, mounting disks, pkexec...) also approve themselves
# while the stick is in (these come from polkit, not sudo, so they need their own switch):  sudo bash install.sh --polkit
# Forgot to plug the stick in? With --wait, sudo and the pop-ups wait up to 20 seconds for it before asking for a password, so you can plug it in
# then and have it approved (--wait=40 for 40 seconds, --no-wait to stop):  sudo bash install.sh --wait
# Undo:  sudo bash install.sh --remove
# Cross-device (sudo here also works while the stick is in your OTHER paired computer; turn it on in the Password Manager first,
# with the stick plugged into THIS computer):  sudo bash install.sh --cross-device      (add --login as well for the lock screen)
# Stop just that:  sudo bash install.sh --no-cross-device
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
U="${SUDO_USER:?run with sudo from your own account}"
GDM=/etc/pam.d/gdm-password
PAM_FILES=(/etc/pam.d/sudo)
LOGIN=0; CROSS=0; POLKIT=0; WAIT=0; NOWAIT=0
WAIT_FILE=/etc/recall-stick-wait; WAIT_MSG=/etc/recall-stick-wait.msg
POLKIT_PAM=/etc/pam.d/polkit-1; POLKIT_PAM_VENDOR=/usr/lib/pam.d/polkit-1
RULE=/etc/polkit-1/rules.d/10-recall-stick.rules
PK_HELPER=/usr/local/sbin/recall-stick-polkit
for a in "$@"; do case "$a" in --login) LOGIN=1;; --cross-device) CROSS=1;; --polkit) POLKIT=1;; --wait) WAIT=20;; --wait=*) WAIT="${a#--wait=}";; --no-wait) NOWAIT=1;; esac; done
case "$WAIT" in ''|*[!0-9]*) echo "--wait=SECONDS needs a number, for example --wait=30"; exit 1;; esac
[ "$WAIT" -le 120 ] || { echo "--wait can be at most 120 seconds."; exit 1; }
# re-running without --wait keeps it if it was on (the number of seconds stays as it was)
if [ "$WAIT" = 0 ] && [ "$NOWAIT" = 0 ] && [ -r "$WAIT_FILE" ]; then WAIT=$(tr -dc 0-9 < "$WAIT_FILE"); WAIT=${WAIT:-0}; fi
# re-running without --polkit keeps it if it was on
grep -qxF polkit-1 /etc/recall-stick-services 2>/dev/null && POLKIT=1
# re-running without --login used to quietly switch the lock screen back off: keep it if it was on
grep -qxF gdm-password /etc/recall-stick-services 2>/dev/null && LOGIN=1
[ "$LOGIN" = 1 ] && [ -f "$GDM" ] && PAM_FILES+=("$GDM")
if [ "$LOGIN" = 1 ] && [ ! -f "$GDM" ]; then
  echo "Note: no GNOME login screen here ($GDM is missing), so --login can't unlock the lock screen on this computer."
  echo "      Login screens found: $(ls /etc/pam.d | grep -E 'gdm|lightdm|sddm|greetd|cosmic' | tr '\n' ' ')"
fi
REMOTE_FILES=(/usr/local/sbin/recall-stick-remote-check /etc/recall-stick.presence.pub /etc/recall-stick.presence.name)

# the lines that go in a PAM file: approve at once if the stick is in; otherwise (only with --wait) say so, and give it a few seconds to be plugged in
pam_block() {   # $1 = file, $2 = wait on/off
  local f="$1" tmp; tmp=$(mktemp)
  sed -i '/recall-stick-check/d;/recall-stick-wait.msg/d' "$f"
  echo "auth sufficient pam_exec.so quiet /usr/local/sbin/recall-stick-check" > "$tmp"
  if [ "$2" = 1 ]; then
    echo "auth optional pam_echo.so file=$WAIT_MSG" >> "$tmp"
    echo "auth sufficient pam_exec.so quiet /usr/local/sbin/recall-stick-check --wait" >> "$tmp"
  fi
  sed -i "1r $tmp" "$f"; rm -f "$tmp"
}
write_wait() {
  printf '%s\n' "$WAIT" > "$WAIT_FILE"; chmod 644 "$WAIT_FILE"; chown root:root "$WAIT_FILE"
  printf 'Plug in your USB stick to approve this, or wait %s seconds to type your password.\n' "$WAIT" > "$WAIT_MSG"; chmod 644 "$WAIT_MSG"; chown root:root "$WAIT_MSG"
}

if [ "${1:-}" = "--no-wait" ]; then
  for f in /etc/pam.d/sudo "$POLKIT_PAM"; do [ -f "$f" ] && sed -i '/recall-stick-wait.msg/d;/recall-stick-check --wait/d' "$f"; done
  rm -f "$WAIT_FILE" "$WAIT_MSG"
  echo "sudo and the pop-ups no longer wait for the stick: without it they ask for your password straight away."; exit 0
fi

if [ "${1:-}" = "--no-cross-device" ]; then
  rm -f "${REMOTE_FILES[@]}"
  echo "Cross-device is off on this computer: sudo here only accepts the stick when it's plugged in here."; exit 0
fi

if [ "${1:-}" = "--remove" ]; then
  for f in /etc/pam.d/sudo "$GDM" "$POLKIT_PAM"; do
    [ -f "$f" ] && sed -i '/recall-stick-check/d;/recall-stick-wait.msg/d' "$f" || true
  done
  [ -f "$POLKIT_PAM" ] && [ -f "$POLKIT_PAM_VENDOR" ] && cmp -s "$POLKIT_PAM" "$POLKIT_PAM_VENDOR" && rm -f "$POLKIT_PAM"      # the copy this installer made
  rm -f "$WAIT_FILE" "$WAIT_MSG"
  systemctl disable --now recall-stick-watch.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/recall-stick-watch.service /usr/local/sbin/recall-stick-watch; rm -rf /run/recall-stick
  systemctl daemon-reload 2>/dev/null || true
  rm -f /usr/local/sbin/recall-stick-check /etc/recall-stick.sha256 /etc/recall-stick-services "${REMOTE_FILES[@]}" "$RULE" "$PK_HELPER"
  echo "Removed. sudo, the login screen and the Authentication required pop-ups ask for your password again."; exit 0
fi

K=$(ls /run/media/$U/*/Vault/recall-vault.keyx 2>/dev/null | head -1)
[ -n "$K" ] || { echo "Plug the stick in first (Vault/recall-vault.keyx not found)."; exit 1; }
install -m 755 -o root -g root "$(dirname "$0")/recall-stick-check.sh" /usr/local/sbin/recall-stick-check
sed -i "s/__USER__/$U/g" /usr/local/sbin/recall-stick-check
sha256sum "$K" | cut -d' ' -f1 > /etc/recall-stick.sha256; chmod 644 /etc/recall-stick.sha256; chown root:root /etc/recall-stick.sha256

{ echo sudo; [ "$LOGIN" = 1 ] && echo gdm-password; [ "$POLKIT" = 1 ] && echo polkit-1; true; } > /etc/recall-stick-services
chmod 644 /etc/recall-stick-services; chown root:root /etc/recall-stick-services

for f in "${PAM_FILES[@]}"; do
  cp -n "$f" "$f.recall-backup"
  if [ "$f" = /etc/pam.d/sudo ] && [ "$WAIT" -gt 0 ]; then pam_block "$f" 1; else pam_block "$f" 0; fi
done
if [ "$WAIT" -gt 0 ]; then
  write_wait
  if [ "$POLKIT" = 1 ]; then                       # the pop-ups too: polkit has its own PAM file (Ubuntu ships it under /usr/lib/pam.d; ours in /etc/pam.d wins)
    [ -f "$POLKIT_PAM" ] || { [ -f "$POLKIT_PAM_VENDOR" ] && cp "$POLKIT_PAM_VENDOR" "$POLKIT_PAM"; } || true
    [ -f "$POLKIT_PAM" ] && pam_block "$POLKIT_PAM" 1 || echo "Note: no polkit PAM file found, so the pop-ups won't wait for the stick (sudo will)."
  fi
fi

if [ "$POLKIT" = 1 ]; then
  # polkit runs its rule helpers as its own unprivileged user ("polkitd"), which can't see your stick (your mount is private to you), so:
  #  1. a tiny root service (recall-stick-watch) checks the stick every 2 seconds, the same way sudo does (right key file, matching hash), and keeps
  #     a flag file in /run (root-only to write) up to date: present while the stick is in, gone (or stale) when it is not;
  #  2. the polkit rule asks a read-only helper that only looks at that flag (it must be root-owned, a plain file, and under 8 seconds old);
  #  3. the rule approves for you only, in your own active session at this computer. Without the stick nothing changes.
  [ -d /etc/polkit-1/rules.d ] || { echo "No polkit rules folder here, so the Authentication required pop-ups can't be approved."; exit 1; }
  command -v systemctl >/dev/null || { echo "This needs systemd."; exit 1; }
  cat > /usr/local/sbin/recall-stick-watch <<WATCH
#!/bin/bash
# root service: keep /run/recall-stick/ok fresh while the right stick is plugged into this computer, or (if this computer was paired with
# --cross-device) into your other paired computer. The second check only reads the signed heartbeat the Password Manager already downloaded: no network here.
U="$U"; DIR=/run/recall-stick; FLAG=\$DIR/ok; REMOTE=/usr/local/sbin/recall-stick-remote-check
mkdir -p "\$DIR"; chmod 755 "\$DIR"
local_stick() {
  [ -r /etc/recall-stick.sha256 ] || return 1
  local f src
  for f in /run/media/\$U/*/Vault/recall-vault.keyx; do
    [ -f "\$f" ] && [ ! -L "\$f" ] || continue
    src=\$(findmnt -T "\$f" -no SOURCE 2>/dev/null)
    case "\$src" in /dev/sd*|/dev/nvme*|/dev/mmcblk*) ;; *) continue ;; esac
    [ "\$(sha256sum "\$f" | cut -d' ' -f1)" = "\$(cut -d' ' -f1 /etc/recall-stick.sha256)" ] && return 0
  done
  return 1
}
other_computer() {
  [ -x "\$REMOTE" ] && [ -r /etc/recall-stick.presence.pub ] || return 1
  # run as you, not root: it only reads, and that way it doesn't write a log line every few seconds when the stick is simply not there
  runuser -u "\$U" -- env PAM_USER="\$U" PAM_SERVICE=polkit-1 "\$REMOTE" >/dev/null 2>&1
}
trap 'rm -f "\$FLAG"; exit 0' TERM INT
rt=0; rok=0
while :; do
  now=\$(date +%s)
  if local_stick; then ok=1
  else
    # the other computer's heartbeat is looked at every 4 seconds (it's a small Python script; the answer is kept in between)
    if [ \$((now - rt)) -ge 4 ]; then rt=\$now; if other_computer; then rok=1; else rok=0; fi; fi
    ok=\$rok
  fi
  if [ "\$ok" = 1 ]; then touch "\$FLAG"; chmod 644 "\$FLAG"; else rm -f "\$FLAG"; fi
  sleep 2
done
WATCH
  chmod 755 /usr/local/sbin/recall-stick-watch; chown root:root /usr/local/sbin/recall-stick-watch
  cat > /etc/systemd/system/recall-stick-watch.service <<UNIT
[Unit]
Description=Recall USB stick: keep the "stick is in" flag up to date (for the Authentication required pop-ups)
[Service]
ExecStart=/usr/local/sbin/recall-stick-watch
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
UNIT
  # the helper polkit asks (it runs as polkitd, so it can only read): a root-owned, plain, fresh flag file means the stick is in
  cat > "$PK_HELPER" <<'HELPER'
#!/bin/sh
F=/run/recall-stick/ok
[ -f "$F" ] && [ ! -L "$F" ] || exit 1
[ "$(stat -c %U "$F")" = root ] || exit 1
[ $(( $(date +%s) - $(stat -c %Y "$F") )) -lt 8 ] || exit 1
exit 0
HELPER
  chmod 755 "$PK_HELPER"; chown root:root "$PK_HELPER"
  cat > "$RULE" <<RULES
// Recall USB stick: while it is plugged in, the "Authentication required" prompts are approved for $U.
// Installed by sudo-stick/install.sh --polkit; remove with: sudo bash install.sh --remove
polkit.addRule(function (action, subject) {
  if (subject.user !== "$U" || !subject.local || !subject.active) return polkit.Result.NOT_HANDLED;
  try {
    polkit.spawn(["$PK_HELPER"]);                       // exits 0 only while the stick is in (see recall-stick-watch)
    return polkit.Result.YES;
  } catch (e) {
    return polkit.Result.NOT_HANDLED;                   // no stick: the normal password prompt
  }
});
RULES
  chmod 644 "$RULE"; chown root:root "$RULE"
  systemctl daemon-reload
  systemctl enable --now recall-stick-watch.service >/dev/null 2>&1 || systemctl restart recall-stick-watch.service
  systemctl restart recall-stick-watch.service
  sleep 3
  echo "The Authentication required pop-ups now approve themselves while the stick is in$([ -f /run/recall-stick/ok ] && echo " (the stick is detected right now)" || echo " (the stick isn't detected right now: is it plugged in and mounted?)")."
fi

if [ "$CROSS" = 1 ]; then
  # The stick's PUBLIC presence key (derived from the key file; it can check a heartbeat but never make one) and this
  # computer's name, for recall-stick-remote-check. Root-owned; nothing secret is stored.
  python3 -I - "$K" > /etc/recall-stick.presence.pub.new <<'PY'
import sys
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
kb = open(sys.argv[1], "rb").read()
sk = Ed25519PrivateKey.from_private_bytes(HKDF(hashes.SHA256(), 32, None, b"jblol-pm presence sign v1").derive(kb))
sys.stdout.buffer.write(sk.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
PY
  mv /etc/recall-stick.presence.pub.new /etc/recall-stick.presence.pub
  hostname | sed 's/[^A-Za-z0-9._-]/-/g' | cut -c1-64 > /etc/recall-stick.presence.name
  install -m 755 -o root -g root "$(dirname "$0")/recall-stick-remote-check.py" /usr/local/sbin/recall-stick-remote-check
  sed -i "s/__USER__/$U/g" /usr/local/sbin/recall-stick-remote-check
  chown root:root /etc/recall-stick.presence.pub /etc/recall-stick.presence.name; chmod 644 /etc/recall-stick.presence.pub /etc/recall-stick.presence.name
  echo "Cross-device is on for this computer: sudo$([ "$LOGIN" = 1 ] && echo " and the lock screen") also work while the stick is in your other paired computer."
fi
echo "Done."
echo "Test sudo in a NEW terminal:  sudo -k && sudo true     (no password with the stick in; password without it)"
[ "$POLKIT" = 1 ] && echo "Test the pop-ups:  pkexec true     (no pop-up with the stick in; a password pop-up without it), or install an app in Software."
if [ "${1:-}" = "--login" ]; then
  echo
  echo "Login screen: lock your screen (Super+L) with the stick plugged in, and it should let you straight in without typing your password."
  echo "If it ever refuses to let you in at all, switch to a text console (Ctrl+Alt+F3), log in with your password, and run:  sudo bash $(cd "$(dirname "$0")" && pwd)/install.sh --remove"
fi
