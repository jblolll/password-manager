#!/usr/bin/env bash
# Password Manager: what's wrong on this computer. Run it with the stick plugged in: bash diag.sh
echo "== USB stick =="
K=$(ls /run/media/$USER/*/Vault/recall-vault.keyx 2>/dev/null | head -1)
if [ -z "$K" ]; then echo "NOT FOUND: no Vault/recall-vault.keyx on a mounted drive. Is the stick mounted? ls /run/media/$USER"; else echo "found: $K"; fi

echo; echo "== KeePassXC =="
command -v keepassxc.cli >/dev/null && echo "keepassxc.cli: found" || echo "MISSING: sudo snap install keepassxc"
snap connections keepassxc 2>/dev/null | grep removable-media || echo "not a snap (ok if installed another way)"
if snap connections keepassxc 2>/dev/null | grep -q "removable-media .*-$"; then echo "NOT CONNECTED: run  sudo snap connect keepassxc:removable-media"; fi

echo; echo "== Vault file =="
VAULT=$(python3 -c "import json;print(json.load(open('$HOME/.config/jblol-password-manager/settings.json')).get('vault',''))" 2>/dev/null)
echo "configured path: ${VAULT:-not set yet}"
[ -n "$VAULT" ] && [ -f "$VAULT" ] && echo "exists: yes ($(stat -c%s "$VAULT") bytes)" || echo "exists: NO (first unlock should fetch it from the server)"

echo; echo "== Server reach / sync key =="
if [ -f ~/.ssh/recall_machines ]; then
  timeout 8 ssh -o BatchMode=yes -o IdentitiesOnly=yes -i ~/.ssh/recall_machines -o StrictHostKeyChecking=accept-new ubuntu@129.213.162.117 'echo SSH-OK' 2>&1
else
  echo "MISSING: ~/.ssh/recall_machines (run: recall hosts keygen)"
fi

echo; echo "== Vault watcher service =="
systemctl --user is-active recall-vault.service 2>&1
journalctl --user -u recall-vault -n 5 --no-pager 2>&1 | tail -5

echo; echo "== The app itself =="
if pgrep -f "share/jblol-password-manager/jblol_pm.py" >/dev/null; then echo "running"; else echo "NOT RUNNING: run  jblol-pm"; fi
echo; echo "== Try the actual unlock, with real error output =="
python3 - <<'PY'
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.expanduser("~/.local/share/jblol-password-manager"))
try:
    import jblol_pm as pm
except Exception as e:
    print("Couldn't even load the app code:", e); raise SystemExit
k = pm.find_keyfile()
print("keyfile the app sees:", k)
if not k:
    print("-> fix the USB section above first"); raise SystemExit
s = pm.load_settings()
try:
    v = pm.Vault(s["vault"], k)
    n = len(v.load())
    print(f"UNLOCKED OK: {n} passwords")
except pm.VaultError as e:
    print("UNLOCK FAILED:", e)
except Exception as e:
    print("UNLOCK FAILED (unexpected):", repr(e))
PY
