#!/usr/bin/env bash
# PAM helper: succeed only if the Recall USB stick is mounted and its keyfile matches the stored hash. Checks PAM_SERVICE against the
# allowed list in /etc/recall-stick-services (one per line; "sudo" by default, "gdm-password" added by --login). One user only.
#   recall-stick-check           look once
#   recall-stick-check --wait    if the stick isn't in, give it up to the number of seconds in /etc/recall-stick-wait to be plugged in (install.sh --wait)
[ "${PAM_USER:-}" = "__USER__" ] || exit 1
SVC=/etc/recall-stick-services            # fixed paths only: a root auth helper never takes them from the environment
[ -r "$SVC" ] || exit 1
grep -qxF "${PAM_SERVICE:-}" "$SVC" || exit 1
HASH=/etc/recall-stick.sha256
[ -r "$HASH" ] || exit 1
REMOTE=/usr/local/sbin/recall-stick-remote-check

here() {
  local f src
  for f in /run/media/__USER__/*/Vault/recall-vault.keyx; do
    [ -f "$f" ] && [ ! -L "$f" ] || continue
    src=$(findmnt -T "$f" -no SOURCE 2>/dev/null)
    case "$src" in /dev/sd*|/dev/nvme*|/dev/mmcblk*) ;; *) continue ;; esac
    [ "$(sha256sum "$f" | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$HASH")" ] && return 0
  done
  return 1
}
elsewhere() {   # plugged into your other computer (only if this one was paired with install.sh --cross-device)
  [ -x "$REMOTE" ] && [ -r /etc/recall-stick.presence.pub ] && "$REMOTE"
}

here && exit 0
if [ "${1:-}" != "--wait" ]; then elsewhere; exit $?; fi

# --wait: only where somebody can actually plug the stick in (a terminal, or a pop-up), never for scripts and background jobs
N=$(tr -dc 0-9 < /etc/recall-stick-wait 2>/dev/null); N=${N:-0}
[ "$N" -gt 0 ] && [ "$N" -le 120 ] || exit 1
[ -n "${PAM_TTY:-}" ] || [ "${PAM_SERVICE:-}" = polkit-1 ] || exit 1
now_us() { local t=${EPOCHREALTIME/./}; echo "${t}"; }       # (whole-second SECONDS can cut the wait up to a second short)
end=$(( $(now_us) + N * 1000000 )); i=1
while [ "$(now_us)" -lt "$end" ]; do
  here && exit 0
  [ $((i % 6)) = 0 ] && elsewhere && exit 0        # the other computer's heartbeat only every ~3 seconds (each miss writes a log line)
  i=$((i + 1)); sleep 0.5
done
exit 1
