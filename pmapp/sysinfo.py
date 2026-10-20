"""Facts about this computer and the helpers installed on it (no windows here, so it is easy to test)."""
import os
import re
import subprocess
import time
from pathlib import Path

ETC = Path(os.environ.get("JBLOL_PM_ETC", "/etc"))
POLKIT_RULE = ETC / "polkit-1" / "rules.d" / "10-recall-stick.rules"
BOOT_FLAG = ETC / "recall-stick-boot"
CRYPTTAB = ETC / "crypttab"


def run(cmd, timeout=6):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def service_active(name, user=True):
    out = run(["systemctl", *(["--user"] if user else []), "is-active", name]).strip()
    return out == "active"


def os_name():
    try:
        for l in Path("/etc/os-release").read_text().splitlines():
            if l.startswith("PRETTY_NAME="):
                return l.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return "Linux"


def machine_name():
    return re.sub(r"[^A-Za-z0-9._-]", "-", os.uname().nodename)[:64] or "computer"


def guess_kind(name):
    n = name.lower()
    return "desktop" if any(w in n for w in ("desktop", "tower", "pc", "workstation")) else "laptop"


def polkit_state():
    """('on'|'off'|'idle', text): the 'Authentication required' pop-ups.
    (The rule file sits in a folder only root and polkit can read, so what shows it is installed is the helper and the watcher's flag folder.)"""
    helper = ETC.parent / "usr" / "local" / "sbin" / "recall-stick-polkit" if os.environ.get("JBLOL_PM_ETC") else Path("/usr/local/sbin/recall-stick-polkit")
    run_dir = Path(os.environ.get("JBLOL_PM_RUN", "/run/recall-stick"))
    if not helper.exists():
        return "off", "Not set up"
    if not run_dir.exists():
        return "idle", "Set up, waiting for the helper service"
    ok = run_dir / "ok"
    try:
        fresh = ok.exists() and time.time() - ok.stat().st_mtime < 8
    except OSError:
        fresh = False
    return ("on", "Approved while the stick is in") if fresh else ("idle", "Set up, stick not in")


def boot_state():
    """('na'|'off'|'on', text): unlocking the encrypted disk at start-up with the stick (sudo-stick/boot-unlock.sh)."""
    try:
        lines = [l for l in CRYPTTAB.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    except OSError:
        return "na", "No encrypted disk found"
    if not lines:
        return "na", "This disk isn't encrypted"
    if (ETC / "dracut.conf.d" / "90-recall-stick.conf").exists():
        return "on", "On: the stick unlocks the disk, the passphrase is the backup"
    return "off", "Not set up: you type the disk passphrase at start-up"


def wait_seconds():
    """How long sudo and the pop-ups wait for a stick that isn't in yet (install.sh --wait); 0 = they don't."""
    try:
        n = int("".join(c for c in (ETC / "recall-stick-wait").read_text() if c.isdigit()) or 0)
    except (OSError, ValueError):
        return 0
    return n if 0 < n <= 120 else 0


def polkit_wait_ready():
    """True if the permission pop-ups (not just sudo) also wait: polkit's PAM file has the waiting line."""
    try:
        return "recall-stick-check --wait" in (ETC / "pam.d" / "polkit-1").read_text()
    except OSError:
        return False


def human_size(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def lock_session():
    """Lock the screen of this user's desktop session. Works from a background service too (which has no session of its own): it finds the
    graphical session and asks for that one, then falls back to GNOME's screen saver. True if a request went through."""
    try:
        sid = os.environ.get("XDG_SESSION_ID", "")
        if not sid:
            ids = subprocess.run(["loginctl", "list-sessions", "--no-legend"], capture_output=True, text=True, timeout=8).stdout.split("\n")
            for line in ids:
                parts = line.split()
                if len(parts) < 3 or parts[2] != os.environ.get("USER", parts[2]):
                    continue
                info = dict(l.split("=", 1) for l in subprocess.run(["loginctl", "show-session", parts[0], "-p", "Class", "-p", "Type", "-p", "State"],
                                                                   capture_output=True, text=True, timeout=8).stdout.splitlines() if "=" in l)
                if info.get("Class") == "user" and info.get("Type") in ("wayland", "x11", "mir") and info.get("State") in ("active", "online"):
                    sid = parts[0]
                    break
        if sid and subprocess.run(["loginctl", "lock-session", sid], capture_output=True, timeout=8).returncode == 0:
            return True
        return subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver", "--object-path", "/org/gnome/ScreenSaver",
                               "--method", "org.gnome.ScreenSaver.Lock"], capture_output=True, timeout=8).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
