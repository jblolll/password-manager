#!/usr/bin/env python3
"""Cross-device stick: while the USB stick is plugged into one of your computers, your other paired computers can act as if
it were plugged into them too -- sudo without a password (and the lock screen, if you set that up) and, only if you also
allow it, your passwords. Off by default on every computer; turned on per computer under USB & Security.

How it stays safe:
  * Proof the stick is really plugged in somewhere. A signing key is derived from the stick's key file (HKDF -> Ed25519), so
    only a computer that can read the stick right now can sign. The computer holding it posts a signed "stick is here"
    heartbeat to your own server every BEAT_S seconds, each valid for TTL_S; unplug and it posts a signed "gone" at once,
    and with no network the last one simply runs out. The other computers only ever hold the matching PUBLIC key (written
    when you paired them, with the stick plugged into them), so they can check a heartbeat but never make one. The server
    stores nothing secret; someone who controlled it could at most replay a heartbeat until it expires.
  * Each heartbeat names the computers it is for: the paired computers listed on the stick itself (Vault/peers/). Take a
    computer off the stick's list (or turn the feature off on it) and it stops being included.
  * Passwords on the other computer are a separate switch. That computer has its own encryption key pair (X25519); the
    public half is stored on the stick when it's paired. The computer with the stick seals the key file to it (X25519 +
    HKDF + AES-GCM) inside the heartbeat; it is opened only in memory / a RAM-only folder and wiped when the heartbeat ends.
    Anyone in control of that computer's account while your stick is in the other one could copy the key file then: that
    is the cost of this switch, which is why it is off unless you turn it on.

Run as a user service (`presence.py daemon`): it sends while the stick is here and fetches while it isn't. The app and the
root sudo helper only read the files it writes. No PySide6 here, so the service stays small.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

V = 1
BEAT_S, TTL_S, FETCH_S, SKEW_S = 20, 75, 12, 120     # send every, valid for, look every, allowed clock difference
HOME = Path.home()
CONF_DIR = Path(os.environ.get("JBLOL_PM_CONF", HOME / ".config" / "jblol-password-manager"))
MEDIA = Path(os.environ.get("JBLOL_PM_MEDIA", f"/run/media/{os.environ.get('USER', 'jblol')}"))
STICK_HASH = Path(os.environ.get("JBLOL_PM_STICK_HASH", "/etc/recall-stick.sha256"))
SYNC_DIR = os.environ.get("JBLOL_PM_SYNC_DIR")        # tests: a local folder standing in for the server
KEYFILE_NAME = "recall-vault.keyx"
BOX_KEY = CONF_DIR / "presence-box.key"               # this computer's X25519 private key (only with the passwords switch on)
PUB_FILE = CONF_DIR / "presence.pub"                  # the stick's public signing key, saved when this computer was paired
INCOMING = CONF_DIR / "presence-in.json"              # the newest valid heartbeat from another computer (the sudo helper reads it)
STATE = CONF_DIR / "presence-state.json"              # what the service is doing, for the app to show
ROOT_PUB = Path("/etc/recall-stick.presence.pub")     # the same public key, installed for the root sudo helper
RAM_DIR = Path(f"/dev/shm/snap.keepassxc.jblolpm-{os.getuid()}")   # RAM only, and readable by the KeePassXC snap
LOCK_IN = CONF_DIR / "presence-lock.json"             # another computer told this one to lock (the app locks the vault when it sees it)
LOCK_OUT = CONF_DIR / "presence-lock-out.json"        # the app asks this computer (holding the stick) to tell others to lock
LOCKS_SEEN = CONF_DIR / "presence-locks-seen.json"    # lock orders already carried out, so one order never locks twice
SEEN = CONF_DIR / "presence-seen.json"                # what each computer last reported about itself (for the device list)
DEVICES = CONF_DIR / "devices.json"                   # the computers on the stick's list, remembered for when the stick is away
EVENT_LOG = CONF_DIR / "events.log"
LOCK_TTL_S, BEACON_S, SEEN_FETCH_S = 90, 30, 20


# ------------------------------------------------------------------ keys
def _log(text):
    """One line in the same activity log the app reads (never a secret)."""
    try:
        EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENT_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")
    except OSError:
        pass


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


def machine_name():
    return re.sub(r"[^A-Za-z0-9._-]", "-", os.uname().nodename)[:64] or "computer"


def _hkdf(material, info, n=32):
    return HKDF(hashes.SHA256(), n, None, info).derive(material)


def signing_key(keyfile_bytes):
    return Ed25519PrivateKey.from_private_bytes(_hkdf(keyfile_bytes, b"jblol-pm presence sign v1"))


def public_pem(keyfile_bytes):
    return signing_key(keyfile_bytes).public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def _b64(b):
    return base64.b64encode(b).decode()


def canon(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign(sk, body):
    return {"body": body, "sig": _b64(sk.sign(canon(body)))}


def verify(pub_pem, msg, me, now=None):
    """The heartbeat's body if it is genuine, current, not a "gone" notice, and addressed to `me`; else None.
    (sudo-stick/recall-stick-remote-check.py makes the same checks, standalone, as root.)"""
    now = time.time() if now is None else now
    try:
        body, sig = msg["body"], base64.b64decode(msg["sig"], validate=True)
        serialization.load_pem_public_key(pub_pem).verify(sig, canon(body))
        ok = (body.get("v") == V and not body.get("revoked") and me in body.get("to", []) and body.get("from") != me
              and body["iat"] - SKEW_S <= now <= body["exp"] and body["exp"] - body["iat"] <= TTL_S)
        return body if ok else None
    except (KeyError, TypeError, ValueError, AttributeError, InvalidSignature):
        return None


# ------------------------------------------------------------------ sealing the key file for one paired computer
def new_box():
    k = X25519PrivateKey.generate()
    return k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()), \
        k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def seal(box_pub, data):
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key = _hkdf(eph.exchange(X25519PublicKey.from_public_bytes(box_pub)) + eph_pub + box_pub, b"jblol-pm presence seal v1")
    nonce = secrets.token_bytes(12)
    return _b64(eph_pub + nonce + AESGCM(key).encrypt(nonce, data, b"jblol-pm keyfile"))


def unseal(box_priv, blob):
    raw = base64.b64decode(blob, validate=True)
    eph_pub, nonce, ct = raw[:32], raw[32:44], raw[44:]
    k = X25519PrivateKey.from_private_bytes(box_priv)
    box_pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key = _hkdf(k.exchange(X25519PublicKey.from_public_bytes(eph_pub)) + eph_pub + box_pub, b"jblol-pm presence seal v1")
    return AESGCM(key).decrypt(nonce, ct, b"jblol-pm keyfile")


# ------------------------------------------------------------------ the stick
def find_keyfile():
    try:
        for d in sorted(MEDIA.iterdir()):
            f = d / "Vault" / KEYFILE_NAME
            if f.is_file() and not f.is_symlink():
                return f
    except OSError:
        pass
    return None


def stick_is_mine(keyfile):
    try:
        return bool(keyfile) and not Path(keyfile).is_symlink() and \
            hashlib.sha256(Path(keyfile).read_bytes()).hexdigest() == STICK_HASH.read_text().split()[0].strip()
    except (OSError, IndexError):
        return False


def peers_dir(keyfile):
    return Path(keyfile).parent / "peers"


def read_peers(keyfile):
    out = {}
    try:
        for f in sorted(peers_dir(keyfile).glob("*.json")):
            if f.is_symlink() or f.stat().st_size > 4096:
                continue
            try:
                p = json.loads(f.read_text())
                name = str(p["name"])
                if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name) and f.stem == name:
                    out[name] = {"vault": bool(p.get("vault")), "box": base64.b64decode(p["box"]) if p.get("box") else None}
            except (OSError, ValueError, KeyError, TypeError):
                continue
    except OSError:
        pass
    return out


def _write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def pair(keyfile, vault):
    """Add this computer to the stick's list (the stick must be plugged in here). vault=True also lets it open the
    passwords while the stick is in another computer: it gets an encryption key pair, whose public half goes on the stick."""
    kb = Path(keyfile).read_bytes()
    _write_private(PUB_FILE, public_pem(kb))
    rec = {"name": machine_name(), "vault": bool(vault), "added": time.strftime("%Y-%m-%d")}
    if vault:
        priv, pub = new_box()
        _write_private(BOX_KEY, priv)
        rec["box"] = _b64(pub)
    else:
        forget_box()
    d = peers_dir(keyfile)
    d.mkdir(exist_ok=True)
    (d / f"{rec['name']}.json").write_text(json.dumps(rec))
    server_setup(_settings(), rec["name"])


def unpair(keyfile=None):
    """Leave: forget this computer's keys; and take it off the stick's list if the stick is plugged in here."""
    forget_box()
    clear_incoming()
    if keyfile:
        try:
            (peers_dir(keyfile) / f"{machine_name()}.json").unlink()
        except OSError:
            pass
        try:
            server_forget(_settings(), machine_name())
        except (OSError, subprocess.SubprocessError):
            pass                                        # offline: the key stays, but it can only post/read heartbeats


def forget_box():
    try:
        BOX_KEY.unlink()
    except OSError:
        pass


def clear_incoming():
    try:
        INCOMING.unlink()
    except OSError:
        pass


# ------------------------------------------------------------------ talking to the server (the same place the vault syncs to)
def _settings():
    try:
        return json.loads((CONF_DIR / "settings.json").read_text())
    except (OSError, ValueError):
        return {}


# Each paired computer gets its own SSH key for this, so it works without the stick (whose key is what normally reaches
# the server). On the server that key is pinned to one forced command (SERVER_SCRIPT, `restrict`ed): post this computer's
# own heartbeat, read everyone's. No shell, no other files, no forwarding.
SSH_KEY = CONF_DIR / "presence-ssh"
SERVER_SCRIPT = r"""#!/bin/bash
# Password Manager cross-device presence (installed by the app when a computer is paired). The only thing a paired
# computer's presence key may run: "get" (read every heartbeat), "put" (replace its own, from stdin), and "vault-get" /
# "vault-put" (the encrypted vault file, so a computer without the stick still stays in sync), and "seen" / "seen-get" (a small
# "this computer is here" note per computer, only for the device list). Nothing else.
set -euo pipefail
name="${1:?}"
[[ "$name" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || exit 2
D="$HOME/vault-sync/presence"
umask 077; mkdir -p "$D"
case "${SSH_ORIGINAL_COMMAND:-}" in
  get) cd "$D"; for f in *.json; do [ -f "$f" ] && head -c 65536 "$f" && echo; done; true ;;
  put) head -c 65536 > "$D/$name.json.new" && mv "$D/$name.json.new" "$D/$name.json" ;;
  seen) head -c 4096 > "$D/$name.seen.new" && mv "$D/$name.seen.new" "$D/$name.seen" ;;
  seen-get) cd "$D"; for f in *.seen; do [ -f "$f" ] && head -c 4096 "$f" && echo; done; true ;;
  vault-get) cat "$HOME/vault-sync/recall.kdbx" ;;
  vault-put) head -c 104857600 > "$HOME/vault-sync/recall.kdbx.new" && [ -s "$HOME/vault-sync/recall.kdbx.new" ] && mv "$HOME/vault-sync/recall.kdbx.new" "$HOME/vault-sync/recall.kdbx" ;;
  *) echo "not allowed" >&2; exit 2 ;;
esac
"""


def _ssh(remote, cmd, stdin=b"", timeout=25, key=SSH_KEY):
    o = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]
    if key is not None:
        o += ["-o", "IdentitiesOnly=yes", "-i", str(key), "-o", "ControlMaster=auto", "-o", f"ControlPath={CONF_DIR}/ssh-%C", "-o", "ControlPersist=300"]
    r = subprocess.run(["ssh", *o, remote, cmd], input=stdin, capture_output=True, timeout=timeout)
    if r.returncode:
        raise OSError((r.stderr.decode(errors="replace").strip().splitlines() or ["couldn't reach the server"])[-1])
    return r.stdout


def _remote(s):
    return s.get("sync_remote", "ubuntu@129.213.162.117")


def _sed(name):
    return name.replace(".", r"\.")                # names are [A-Za-z0-9._-] only (machine_name); dots made literal


def server_setup(s, name):
    """(At pairing, with the stick in, so the stick's own server key is available through the agent.) Make this computer's
    presence key and let the server accept it for the presence command only."""
    if SYNC_DIR:
        return
    if not SSH_KEY.exists():
        SSH_KEY.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"pm-presence@{name}", "-f", str(SSH_KEY)], check=True,
                       capture_output=True, timeout=30)
    pub = SSH_KEY.with_suffix(".pub").read_text().split()
    line = f'restrict,command="bin/pm-presence {name}" {pub[0]} {pub[1]} pm-presence@{name}\n'      # sshd starts in the home folder
    b64 = base64.b64encode(line.encode()).decode()
    _ssh(_remote(s), "umask 077; mkdir -p ~/bin ~/.ssh && cat > ~/bin/pm-presence.new && chmod 755 ~/bin/pm-presence.new && mv ~/bin/pm-presence.new ~/bin/pm-presence"
         f" && touch ~/.ssh/authorized_keys && sed -i '/ pm-presence@{_sed(name)}$/d' ~/.ssh/authorized_keys"
         f" && echo {b64} | base64 -d >> ~/.ssh/authorized_keys",      # only ever this computer's own line is replaced
         stdin=SERVER_SCRIPT.encode(), key=None)


def server_forget(s, name):
    """(Turning it off, with the stick in.) Take this computer's presence key off the server."""
    if SYNC_DIR:
        return
    _ssh(_remote(s), f"sed -i '/ pm-presence@{_sed(name)}$/d' ~/.ssh/authorized_keys && rm -f ~/vault-sync/presence/{name}.json", key=None)


def vault_get(s, dest):
    """The server's vault through this computer's presence key -> True, or False when the server has none."""
    data = _ssh(_remote(s), "vault-get", timeout=60)
    if not data:
        return False
    _write_private(Path(dest), data)
    return True


def vault_put(s, src):
    _ssh(_remote(s), "vault-put", stdin=Path(src).read_bytes(), timeout=60)


def post(s, msg):
    data = json.dumps(msg, separators=(",", ":")).encode()
    if SYNC_DIR:
        d = Path(SYNC_DIR) / "presence"
        d.mkdir(parents=True, exist_ok=True)
        _write_private(d / f"{msg['body']['from']}.json", data)
        return
    _ssh(_remote(s), "put", stdin=data)


def fetch(s):
    if SYNC_DIR:
        d = Path(SYNC_DIR) / "presence"
        return [f.read_bytes() for f in sorted(d.glob("*.json"))] if d.exists() else []
    return [l for l in _ssh(_remote(s), "get").splitlines() if l.strip()]


def heartbeat(sk, keyfile_bytes, peers, me, now=None, revoked=False, lock=None, to=None):
    """`lock` = {"id": ..., "to": [names]} adds an order for those computers to lock their screens; it is signed with the rest,
    so only a computer that holds the stick right now can give it."""
    now = int(time.time() if now is None else now)
    body = {"v": V, "from": me, "iat": now, "exp": now + (0 if revoked else TTL_S), "to": sorted(n for n in (to if to is not None else peers) if n != me)}
    if lock:
        body["lock"] = {"id": str(lock["id"]), "to": sorted(n for n in lock["to"] if n != me)}
    if revoked:
        body["revoked"] = True
    else:
        body["keys"] = {n: seal(p["box"], keyfile_bytes) for n, p in peers.items() if n != me and p["vault"] and p["box"]}
    return sign(sk, body)


def best_incoming(blobs, pub_pem, me, now=None):
    """The heartbeat to use from what the server holds: the one that runs longest."""
    best = None
    for b in blobs:
        try:
            msg = json.loads(b)
        except ValueError:
            continue
        body = verify(pub_pem, msg, me, now)
        if body and (best is None or body["exp"] > best[1]["exp"]):
            best = (msg, body)
    return best


def find_lock(blobs, pub_pem, me, now=None):
    """(id, from) of the newest genuine lock order addressed to `me` from another computer, else None. A "stick pulled" notice counts
    too: it is a revoked heartbeat that carries a lock order."""
    now = time.time() if now is None else now
    best = None
    for b in blobs:
        try:
            msg = json.loads(b)
            body, sig = msg["body"], base64.b64decode(msg["sig"], validate=True)
            serialization.load_pem_public_key(pub_pem).verify(sig, canon(body))
            lock = body.get("lock")
            if body.get("v") == V and lock and me in lock.get("to", []) and body.get("from") != me and body["iat"] - SKEW_S <= now <= body["iat"] + LOCK_TTL_S:
                if best is None or body["iat"] > best[2]:
                    best = (lock["id"], body["from"], body["iat"])
        except (ValueError, KeyError, TypeError, AttributeError, InvalidSignature):
            continue
    return best[:2] if best else None


def beacon(now=None):
    """What this computer says about itself, for the other computers' device lists. Not signed: it is only shown, never trusted."""
    locked = False
    try:
        sid = os.environ.get("XDG_SESSION_ID", "")
        out = subprocess.run(["loginctl", "show-session", sid or "self", "-p", "LockedHint", "--value"], capture_output=True, text=True, timeout=5).stdout.strip()
        locked = out == "yes"
    except (OSError, subprocess.SubprocessError):
        pass
    osn = "Linux"
    try:
        for l in Path("/etc/os-release").read_text().splitlines():
            if l.startswith("PRETTY_NAME="):
                osn = l.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return {"name": machine_name(), "at": int(time.time() if now is None else now), "os": osn, "locked": locked, "stick": bool(find_keyfile())}


def seen_put(s, info):
    data = json.dumps(info, separators=(",", ":")).encode()
    if SYNC_DIR:
        d = Path(SYNC_DIR) / "presence"
        d.mkdir(parents=True, exist_ok=True)
        _write_private(d / f"{info['name']}.seen", data)
        return
    _ssh(_remote(s), "seen", stdin=data)


def seen_get(s):
    if SYNC_DIR:
        d = Path(SYNC_DIR) / "presence"
        lines = [f.read_bytes() for f in sorted(d.glob("*.seen"))] if d.exists() else []
    else:
        lines = [l for l in _ssh(_remote(s), "seen-get").splitlines() if l.strip()]
    out = {}
    for l in lines:
        try:
            j = json.loads(l)
            if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", str(j.get("name", ""))):
                out[j["name"]] = {"at": int(j.get("at", 0)), "os": str(j.get("os", ""))[:60], "locked": bool(j.get("locked")), "stick": bool(j.get("stick"))}
        except (ValueError, TypeError, AttributeError):
            continue
    return out


def devices(keyfile=None):
    """The computers on the stick's list: [{name, vault, seen: {...} or None}], this one included. Remembers the list while the stick is away."""
    names = {}
    if keyfile:
        names = {n: {"vault": p["vault"]} for n, p in read_peers(keyfile).items()}
        try:
            _write_private(DEVICES, json.dumps(names).encode())
        except OSError:
            pass
    else:
        try:
            names = json.loads(DEVICES.read_text())
        except (OSError, ValueError):
            names = {}
    try:
        seen = json.loads(SEEN.read_text())
    except (OSError, ValueError):
        seen = {}
    me = machine_name()
    out = [{"name": me, "me": True, "vault": bool(names.get(me, {}).get("vault")), "seen": None}]
    for n in sorted(set(names) | set(k for k in seen if k != me and k in names)):
        if n != me:
            out.append({"name": n, "me": False, "vault": bool(names.get(n, {}).get("vault")), "seen": seen.get(n)})
    return out


def request_lock(names):
    """The app asks this computer (it must be holding the stick) to tell `names` to lock. Returns the order's id."""
    oid = secrets.token_hex(6)
    _write_private(LOCK_OUT, json.dumps({"id": oid, "to": list(names), "at": int(time.time())}).encode())
    return oid


# ------------------------------------------------------------------ what the app reads
def remote_status(now=None):
    """(computer name, seconds left) while a valid heartbeat from another computer is here, else None."""
    try:
        body = verify(PUB_FILE.read_bytes(), json.loads(INCOMING.read_text()), machine_name(), now)
    except (OSError, ValueError):
        return None
    if not body:
        return None
    return body["from"], int(body["exp"] - (time.time() if now is None else now))


def remote_keyfile(now=None):
    """The key file bytes, while another computer holds the stick and has sealed it for this one; else None."""
    try:
        msg = json.loads(INCOMING.read_text())
        body = verify(PUB_FILE.read_bytes(), msg, machine_name(), now)
        if not body:
            return None
        blob = body.get("keys", {}).get(machine_name())
        return unseal(BOX_KEY.read_bytes(), blob) if blob else None
    except (OSError, ValueError, KeyError, TypeError, InvalidTag):
        return None


def ram_keyfile(data):
    """Put the key file where the KeePassXC snap can read it but nothing is ever written to disk."""
    RAM_DIR.mkdir(mode=0o700, exist_ok=True)
    os.chmod(RAM_DIR, 0o700)
    p = RAM_DIR / "remote.keyx"
    _write_private(p, data)
    return p


def wipe_ram_keyfile():
    for f in RAM_DIR.glob("*") if RAM_DIR.exists() else []:
        try:
            f.write_bytes(b"\0" * f.stat().st_size)
            f.unlink()
        except OSError:
            pass


def root_ready():
    return ROOT_PUB.exists()


def state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ the service
class Daemon:
    def __init__(self):
        self.me = machine_name()
        self.sk = None                 # held only while this computer has (just had) the stick
        self.sent_at = self.fetched_at = 0.0
        self.st = {}
        self.had = None                # was the stick here on the last look (for "lock when it is pulled")
        self.peer_names = []           # the paired computers, remembered from the last heartbeat (the stick's list is gone once it is pulled)
        self.pull_lock = False
        self.beacon_at = self.seen_at = 0.0
        self.server_old = False
        try:
            self.locks_seen = set(json.loads(LOCKS_SEEN.read_text()))
        except (OSError, ValueError):
            self.locks_seen = set()

    def write_state(self, **kw):
        st = {"me": self.me, "at": int(time.time()), **kw}
        if st != self.st:
            self.st = st
            _write_private(STATE, json.dumps(st).encode())

    def pull_watch(self, s, here):
        """Lock this computer's screen the moment the stick is pulled (if turned on), and tell the other computers to do the same."""
        if self.had is None:
            self.had = here
            return
        if self.had and not here:
            if s.get("lock_screen_on_pull"):
                ok = lock_session()
                _log("The stick was pulled, so this computer's screen was locked" + ("" if ok else " (the lock request failed)"))
            if s.get("cross_device") and s.get("lock_peers_on_pull") and self.sk is not None and self.peer_names:
                self.pull_lock = True
        self.had = here

    def handle_lock(self, s, blobs, pub, now):
        cmd = find_lock(blobs, pub, self.me, now)
        if not cmd or cmd[0] in self.locks_seen:
            return
        self.locks_seen = set(list(self.locks_seen)[-50:]) | {cmd[0]}
        try:
            _write_private(LOCKS_SEEN, json.dumps(sorted(self.locks_seen)).encode())
        except OSError:
            pass
        ok = lock_session()
        _log(f"{cmd[1]} told this computer to lock" + ("" if ok else " (the lock request failed)"))
        try:
            _write_private(LOCK_IN, json.dumps({"id": cmd[0], "from": cmd[1], "at": int(now)}).encode())
        except OSError:
            pass

    def beacons(self, s, now):
        """Say that this computer is here, and learn when the others last said so (only for the device list)."""
        try:
            if now - self.beacon_at >= BEACON_S:
                self.beacon_at = now
                seen_put(s, beacon(now))
                self.server_old = False
            if now - self.seen_at >= SEEN_FETCH_S:
                self.seen_at = now
                _write_private(SEEN, json.dumps(seen_get(s)).encode())
                self.server_old = False
        except (OSError, subprocess.SubprocessError) as e:
            if "not allowed" in str(e):
                self.server_old = True                  # the server's script is from before the device list: the app updates it while the stick is here

    def step(self, now=None):
        now = time.time() if now is None else now
        s = _settings()
        kf0 = find_keyfile()
        self.pull_watch(s, bool(kf0 and stick_is_mine(kf0)))
        if s.get("cross_device") and not False:
            self.beacons(s, now)
        if not s.get("cross_device"):
            if self.sk is not None:
                self._gone(s)
            clear_incoming()
            self.write_state(on=False)
            return
        kf = find_keyfile()
        if kf and stick_is_mine(kf):
            kb = kf.read_bytes()
            self.sk = signing_key(kb)
            clear_incoming()                            # the stick is here: nothing to take from elsewhere
            order = None
            try:
                o = json.loads(LOCK_OUT.read_text())
                if now - o["at"] <= LOCK_TTL_S:
                    order = o
                else:
                    LOCK_OUT.unlink()
            except (OSError, ValueError, KeyError):
                pass
            if order and order["id"] != getattr(self, "_order_sent", None):
                self.sent_at = 0.0                      # a new order goes out at once, not at the next beat
            if now - self.sent_at >= BEAT_S:
                peers = read_peers(kf)
                self.peer_names = sorted(n for n in peers if n != self.me)
                try:
                    post(s, heartbeat(self.sk, kb, peers, self.me, now, lock=order))
                    self.sent_at = now
                    if order and order["id"] != getattr(self, "_order_sent", None):
                        self._order_sent = order["id"]
                        _log("Told " + ", ".join(order["to"]) + " to lock")
                    self.write_state(on=True, sending=True, to=self.peer_names, error="", server_old=self.server_old)
                except (OSError, subprocess.SubprocessError) as e:
                    self.write_state(on=True, sending=True, error=str(e)[:200])
            return
        if self.sk is not None:
            self._gone(s)
        if now - self.fetched_at >= FETCH_S:
            self.fetched_at = now
            try:
                pub = PUB_FILE.read_bytes()
            except OSError:
                self.write_state(on=True, error="Not paired yet: plug the stick into this computer once.")
                return
            try:
                blobs = fetch(s)
                best = best_incoming(blobs, pub, self.me, now)
                self.handle_lock(s, blobs, pub, now)
            except (OSError, subprocess.SubprocessError) as e:
                best = None
                err = str(e)[:200]
            else:
                err = ""
            if best:
                _write_private(INCOMING, json.dumps(best[0]).encode())
                self.write_state(on=True, receiving=best[1]["from"], until=best[1]["exp"], error=err, server_old=self.server_old)
            else:
                if not err:
                    clear_incoming()
                self.write_state(on=True, error=err)
        elif INCOMING.exists() and remote_status(now) is None:
            clear_incoming()                            # ran out between looks

    def _gone(self, s):
        """The stick just left this computer: tell the others at once (signed with the key it held), then forget the key."""
        lock = {"id": f"pull-{int(time.time())}", "to": self.peer_names} if self.pull_lock else None
        try:
            post(s, heartbeat(self.sk, b"", {}, self.me, revoked=True, lock=lock, to=self.peer_names if lock else None))
            if lock:
                _log("The stick was pulled: told " + ", ".join(self.peer_names) + " to lock")
        except (OSError, subprocess.SubprocessError):
            pass                                        # offline: the last heartbeat simply runs out
        self.pull_lock = False
        self.sk, self.sent_at = None, 0.0
        self.write_state(on=True, sending=False)

    def run(self):
        while True:
            try:
                self.step()
            except Exception as e:                      # keep the service up whatever happens; say what went wrong
                try:
                    self.write_state(on=True, error=f"{type(e).__name__}: {e}"[:200])
                except OSError:
                    pass
            time.sleep(2)


if __name__ == "__main__":
    if sys.argv[1:] == ["daemon"]:
        Daemon().run()
    else:
        print("usage: presence.py daemon", file=sys.stderr)
        sys.exit(2)
