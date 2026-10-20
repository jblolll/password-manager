#!/usr/bin/env bash
# Unlock the encrypted disk at start-up with the USB stick (Ubuntu with a dracut/systemd initrd, as in Ubuntu 26.04).
#   sudo bash boot-unlock.sh            turn it on (stick plugged in)
#   sudo bash boot-unlock.sh --check    do every safety check and build a TEST start-up image in /tmp, change nothing
#   sudo bash boot-unlock.sh --status   say whether it is on
#   sudo bash boot-unlock.sh --remove   turn it off again
#
# What it does: makes a random key, saves it on the stick (Vault/boot/recall-boot.key) and adds it to your disk as a SECOND way in.
# Your passphrase keeps working and stays the fallback: boot without the stick (or if anything goes wrong) and the normal
# passphrase prompt appears after a few seconds. Then it tells the start-up image where to look for the key.
set -euo pipefail
ROOT="${RECALL_ROOT:-}"                                  # tests point this at a scratch folder
CONF="$ROOT/etc/dracut.conf.d/90-recall-stick.conf"
STATE="$ROOT/etc/recall-stick-boot"
CRYPTTAB="$ROOT/etc/crypttab"
MODE=install
for a in "$@"; do case "$a" in --check) MODE=check;; --status) MODE=status;; --remove) MODE=remove;; esac; done
say() { printf '%s\n' "$*"; }
die() { printf 'Stopped: %s\n' "$*" >&2; exit 1; }

if [ "$MODE" = status ]; then
  if [ -f "$CONF" ]; then say "On: the stick unlocks the disk at start-up ($(sed -n 's/^# luks //p' "$CONF"))."; else say "Off."; fi
  exit 0
fi
[ -n "$ROOT" ] || [ "$(id -u)" = 0 ] || die "run with sudo."
U="${SUDO_USER:-${RECALL_USER:-}}"; [ -n "$U" ] || die "run with sudo from your own account."
MEDIA="${RECALL_MEDIA:-/run/media/$U}"

# ---- which tool builds the start-up image here?
if [ -z "$ROOT" ]; then
  grep -q dracut /usr/sbin/update-initramfs 2>/dev/null || die "this computer builds its start-up image with initramfs-tools, not dracut. This installer only knows dracut (Ubuntu 26.04). Nothing was changed."
  command -v dracut >/dev/null || die "dracut isn't installed."
fi

# ---- the encrypted disk (from /etc/crypttab)
entries=$(grep -v '^[[:space:]]*#' "$CRYPTTAB" 2>/dev/null | grep -v '^[[:space:]]*$' || true)
[ -n "$entries" ] || die "no encrypted disk is listed in /etc/crypttab, so there is nothing to unlock."
[ "$(printf '%s\n' "$entries" | wc -l)" = 1 ] || die "more than one encrypted disk is listed in /etc/crypttab; this installer handles one (the system disk)."
src=$(printf '%s\n' "$entries" | awk '{print $2}')
case "$src" in UUID=*) LUKS_UUID="${src#UUID=}";; /dev/*) LUKS_UUID=$(cryptsetup luksUUID "$src");; *) die "can't tell which disk '$src' is.";; esac
LUKS_DEV=$(blkid -U "$LUKS_UUID") || die "can't find the encrypted disk with UUID $LUKS_UUID."

if [ "$MODE" = remove ]; then
  K=$(ls "$MEDIA"/*/Vault/boot/recall-boot.key 2>/dev/null | head -1 || true)
  rm -f "$CONF" "$STATE"
  if [ -n "$K" ]; then
    cryptsetup luksRemoveKey "$LUKS_DEV" "$K" && say "Removed the stick's key from the disk." || say "Couldn't remove the stick's key from the disk (it can stay: it only opens the disk with the stick)."
    shred -u "$K" 2>/dev/null || rm -f "$K"
  else
    say "The stick isn't plugged in, so its key is still one of the disk's keys. Plug it in and run --remove again to remove it too."
  fi
  [ -n "$ROOT" ] || update-initramfs -u
  say "Off. You type the disk passphrase at start-up again."; exit 0
fi

# ---- the stick
KEYFILE=$(ls "$MEDIA"/*/Vault/recall-vault.keyx 2>/dev/null | head -1 || true)
[ -n "$KEYFILE" ] || die "plug the stick in first (Vault/recall-vault.keyx not found)."
if [ -r "$ROOT/etc/recall-stick.sha256" ]; then
  [ "$(sha256sum "$KEYFILE" | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$ROOT/etc/recall-stick.sha256")" ] || die "this isn't your stick (its key file doesn't match the one set up for sudo)."
fi
STICK_MNT=$(dirname "$(dirname "$KEYFILE")")
STICK_DEV=$(findmnt -no SOURCE -T "$KEYFILE")
STICK_UUID=$(blkid -s UUID -o value "$STICK_DEV")
STICK_FS=$(blkid -s TYPE -o value "$STICK_DEV")
case "$STICK_FS" in vfat|ext4|exfat) ;; *) die "the stick's file system ($STICK_FS) can't be read at start-up. vfat, ext4 and exfat can.";; esac
[ "$STICK_DEV" != "$LUKS_DEV" ] || die "the stick and the encrypted disk are the same device?!"
say "Disk to unlock:  $LUKS_DEV  (UUID $LUKS_UUID)"
say "Stick:           $STICK_DEV  ($STICK_FS, UUID $STICK_UUID)"

BOOTKEY_DIR="$STICK_MNT/Vault/boot"
BOOTKEY="$BOOTKEY_DIR/recall-boot.key"
REL=/Vault/boot/recall-boot.key

# ---- is the stick's key already a way in to the disk?
have_key=0
if [ -f "$BOOTKEY" ] && cryptsetup open --test-passphrase --key-file "$BOOTKEY" "$LUKS_DEV" 2>/dev/null; then have_key=1; fi

if [ "$MODE" = install ] && [ "$have_key" = 0 ]; then
  mkdir -p "$BOOTKEY_DIR"
  tmp=$(mktemp "$BOOTKEY_DIR/.new.XXXXXX"); chmod 600 "$tmp"
  openssl rand -hex 32 | tr -d '\n' > "$tmp"                       # 64 letters and digits, no newline: safe to hand over exactly as it is
  say ""
  say "Next you are asked for your disk passphrase ONCE, to add the stick's key as a second way in."
  cryptsetup luksAddKey "$LUKS_DEV" "$tmp"
  cryptsetup open --test-passphrase --key-file "$tmp" "$LUKS_DEV" || die "the new key doesn't open the disk. Your passphrase still does and nothing else was changed."
  mv -f "$tmp" "$BOOTKEY"
  have_key=1
fi
[ "$MODE" = check ] && [ "$have_key" = 0 ] && say "(--check: the stick's key isn't on the disk yet, so the test image uses the setting only; a real install adds it.)"

# ---- the start-up image settings
HAVE_UUID=0
if [ -z "$ROOT" ]; then dracut --print-cmdline 2>/dev/null | grep -q "$LUKS_UUID" && HAVE_UUID=1; fi
newconf=$(mktemp)
{
  echo "# Written by Password Manager (sudo-stick/boot-unlock.sh). Remove with: sudo bash boot-unlock.sh --remove"
  echo "# luks $LUKS_UUID, stick $STICK_UUID"
  echo 'add_drivers+=" usb_storage uas xhci_hcd xhci_pci ehci_pci vfat fat exfat nls_cp437 nls_iso8859_1 "'
  echo 'filesystems+=" vfat exfat "'
  line="rd.luks.key=$LUKS_UUID=$REL:UUID=$STICK_UUID rd.luks.options=$LUKS_UUID=keyfile-timeout=20s"
  [ "$HAVE_UUID" = 1 ] || line="rd.luks.uuid=$LUKS_UUID $line"
  echo "kernel_cmdline+=\" $line \""
} > "$newconf"

if [ "$MODE" = check ]; then
  if [ -n "$ROOT" ]; then cat "$newconf"; rm -f "$newconf"; exit 0; fi
  KVER=$(uname -r); IMG=$(mktemp /tmp/recall-boot-test.XXXXXX.img)
  say "Building a TEST start-up image (nothing in /boot is touched)…"
  dracut --force --conf "$newconf" --confdir /etc/dracut.conf.d --kver "$KVER" "$IMG" >/dev/null 2>&1 || { rm -f "$newconf" "$IMG"; die "the test image couldn't be built. Nothing was changed."; }
  list=$(mktemp); lsinitrd "$IMG" > "$list" 2>/dev/null || true         # (to a file: grep -q on a pipe can close it early and fool "pipefail")
  builtin="/lib/modules/$KVER/modules.builtin"
  ok=1
  have() { grep -Eq "$1" "$list"; }
  builtin_has() { [ -r "$builtin" ] && grep -Eq "$1" "$builtin"; }
  have 'usb[-_]storage' && say "  ✓ USB storage support is in the image" || { builtin_has 'usb[-_]storage' && say "  ✓ USB storage support is built into the kernel" || { say "  ✗ USB storage support is NOT in the image"; ok=0; }; }
  have 'fat/vfat' && say "  ✓ the stick's file system (vfat) is in the image" || { builtin_has 'fat/vfat|ext4' && say "  ✓ the stick's file system is built into the kernel" || { say "  ✗ the stick's file system is NOT available at start-up"; ok=0; }; }
  have 'systemd-cryptsetup$' && say "  ✓ the disk-unlocking tool is in the image" || { say "  ✗ the disk-unlocking tool (systemd-cryptsetup) is NOT in the image"; ok=0; }
  cmd=$(for f in $(grep -oE 'etc/cmdline\.d/[^ ]+' "$list" | sort -u); do lsinitrd -f "$f" "$IMG" 2>/dev/null; done)
  case "$cmd" in *"rd.luks.key=$LUKS_UUID="*) say "  ✓ the start-up settings carry the key location";; *) say "  ✗ the key location isn't in the image"; ok=0;; esac
  rm -f "$list"
  rm -f "$newconf" "$IMG"
  [ "$ok" = 1 ] && say "Check passed. Run without --check to turn it on." || die "the check found a problem. Nothing was changed."
  exit 0
fi

# ---- install: keep a copy of today's image, write the settings, rebuild
mkdir -p "$(dirname "$CONF")"
install -m 644 "$newconf" "$CONF"; rm -f "$newconf"
echo "luks=$LUKS_UUID stick=$STICK_UUID" > "$STATE"; chmod 644 "$STATE"
if [ -z "$ROOT" ]; then
  KVER=$(uname -r)
  [ -f "/boot/initrd.img-$KVER" ] && cp -n "/boot/initrd.img-$KVER" "/boot/initrd.img-$KVER.recall-backup"
  update-initramfs -u || { rm -f "$CONF" "$STATE"; die "rebuilding the start-up image failed; the old one is untouched and the settings were removed."; }
fi
say ""
say "On. Next time you start this computer with the stick plugged in, the disk unlocks by itself."
say "No stick, or anything unexpected: after about 20 seconds you get the usual passphrase prompt, and your passphrase works as always."
say "A copy of the previous start-up image is /boot/initrd.img-$(uname -r).recall-backup. Turn this off any time: sudo bash boot-unlock.sh --remove"
say "Please restart once while you're at the computer, to see it work."
