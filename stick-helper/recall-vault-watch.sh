#!/usr/bin/env bash
# Stick in = open the Password Manager and load the vault's SSH key; stick out = remove the key and close KeePassXC if it was started.
# The Password Manager locks itself when the stick is removed. Needs the keyfile-only vault.
export SSH_AUTH_SOCK="${SSH_AUTH_SOCK:-/run/user/$UID/gcr/ssh}"
CONF="$HOME/.config/jblol-password-manager/settings.json"
DB="$HOME/Documents/Vault/recall.kdbx"
ENTRY="VPS server"; ATT=recall_server
PUB="$HOME/.ssh/recall_server.pub"
state=out
setting() { python3 -c "import json,sys; print(str(json.load(open('$CONF')).get('$1', '$2')).lower())" 2>/dev/null || echo "$2"; }
stopkp() { for s in $(systemctl --user list-units --plain --no-legend 'snap.keepassxc*.scope' | awk '{print $1}'); do systemctl --user kill --signal=SIGKILL "$s" 2>/dev/null; done; }
load() { echo | keepassxc.cli attachment-export --no-password -k "$1" --stdout "$DB" "$ENTRY" "$ATT" 2>/dev/null | ssh-add - >/dev/null 2>&1; }
while sleep 2; do
  key=$(ls /run/media/$USER/*/Vault/recall-vault.keyx 2>/dev/null | head -1)
  if [ -n "$key" ] && [ "$state" = out ]; then
    state=in
    ssh-add -d "$PUB" >/dev/null 2>&1; load "$key"
    if [ "$(setting launch_on_plug true)" = true ] && [ -x "$HOME/.local/bin/jblol-pm" ]; then
      nohup "$HOME/.local/bin/jblol-pm" >/dev/null 2>&1 &
      ( sleep 3; pgrep -f "share/jblol-password-manager/jblol_pm.py" >/dev/null || nohup "$HOME/.local/bin/jblol-pm" >/dev/null 2>&1 & ) &   # one retry if it didn't stay up
    fi
    if [ "$(setting open_keepassxc false)" = true ]; then
      stopkp; sleep 1; echo | keepassxc --pw-stdin --keyfile "$key" "$DB" >/dev/null 2>&1 &
    fi
  elif [ -z "$key" ] && [ "$state" = in ]; then
    state=out
    stopkp; ssh-add -d "$PUB" >/dev/null 2>&1
  fi
done
