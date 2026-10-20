#!/usr/bin/env python3
"""Password Manager (jblol.systems): a desktop app for the passwords in your KeePass vault.

The vault is a standard KeePass (.kdbx) file, opened with a key file that lives on your USB stick (no master password).
All reading and writing goes through KeePassXC's own command-line tool, so this app has no encryption code of its own.
Plug the stick in: the vault opens. Take it out: the vault locks and everything is wiped from memory.
Without the stick the app still runs (settings, status, the password generator) but the passwords stay locked.
"""
import ast
import csv
import base64
import hashlib
import hmac
import struct
import http.server
import io
import json
import math
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

try:
    from pmapp import model as _typed  # cards, secure notes and Wi-Fi networks ride in the notes (pmapp/model.py)
except ImportError:                     # an old install without the new package: logins only
    class _typed:
        GROUP_KIND = {}
        unpack = staticmethod(lambda n: (n or "", "login", {}))
        pack = staticmethod(lambda n, k, d: n or "")
try:
    import presence                    # cross-device stick (presence.py, beside this file; needs python3-cryptography)
except ImportError:
    presence = None
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtNetwork import QLocalSocket

APP_NAME = "Password Manager"
VERSION = "2026.10.19-1"                                # shown under USB & Security, to tell which computers are up to date
BRAND = "jblol.systems"
HERE = Path(__file__).resolve().parent
HOME = Path.home()
CONF_DIR = Path(os.environ.get("JBLOL_PM_CONF", HOME / ".config" / "jblol-password-manager"))
SETTINGS = CONF_DIR / "settings.json"
DEFAULTS = {"vault": str(HOME / "Documents" / "Vault" / "recall.kdbx"), "launch_on_plug": True, "lock_on_unplug": True,
            "clip_clear_s": 20, "idle_lock_min": 0, "open_keepassxc": False,
            "sync_enabled": True, "sync_remote": "ubuntu@129.213.162.117", "sync_path": "vault-sync/recall.kdbx",
            "bridge_enabled": True, "bridge_port": 47619, "bridge_token": "",
            "phone_enabled": False, "phone_key": "",
            "share_url": "https://pm.jblol.systems", "share_token": "",
            "lock_on_screen": True, "unlock_screen_with_stick": True,
            "app_fill": True, "apps": {},
            "cross_device": False, "cross_device_vault": False,
            "lock_screen_on_pull": False, "lock_peers_on_pull": False, "audit_journal": True, "reveal_timeout_s": 30,
            "warn_passwords": True, "warn_security": True}
HELPER = Path(os.environ.get("JBLOL_PM_HELPER", "/usr/local/sbin/recall-stick-check"))
PAM_FILE = Path(os.environ.get("JBLOL_PM_PAM", "/etc/pam.d/sudo"))
MEDIA = Path(os.environ.get("JBLOL_PM_MEDIA", f"/run/media/{os.environ.get('USER', 'jblol')}"))
KEYFILE_NAME = "recall-vault.keyx"
STARTER_GROUPS = ["Websites", "Apps", "Servers & SSH", "Other"]
TYPED_GROUPS = ["Cards", "Secure notes", "Wi-Fi"]               # where cards, secure notes and Wi-Fi networks live (see pmapp/model.py)
PENDING_GROUP = "Pending changes"                      # new passwords that are waiting for you to confirm the site accepted them
HIDDEN_GROUPS = {"Recycle Bin"}
SERVER_URL = os.environ.get("STUDYBOT_URL", "https://recall.jblol.systems").rstrip("/")
TOKEN_FILE = Path(os.environ.get("RECALL_CONF") or (HOME / ".config" / "studybot")) / "token"

C = dict(muted="#8b94a9", good="#34d399", warn="#fbbf24", bad="#f87171")      # the colours strength() hands back (the app's own look is in pmapp/theme.py)


# ------------------------------------------------------------------ settings
def load_settings():
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(SETTINGS.read_text()))
    except (OSError, ValueError):
        pass
    s["sync_enabled"] = True                    # not optional: your computers always share one vault
    return s


def save_settings(s):
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=1))
    try:
        SETTINGS.chmod(0o600)                   # it holds the browser pairing code
    except OSError:
        pass


# ------------------------------------------------------------------ the USB stick
def find_keyfile():
    """The key file on a mounted USB stick (never a symlink, only under the user's removable-media folder), or None."""
    try:
        for d in sorted(MEDIA.iterdir()):
            f = d / "Vault" / KEYFILE_NAME
            if f.is_file() and not f.is_symlink():
                return f
    except OSError:
        pass
    return None


STICK_HASH = Path("/etc/recall-stick.sha256")


def stick_is_mine(keyfile):
    """True only for the real key file: it must be on a real disk (not a lookalike folder) and match the hash stored when the stick was set up."""
    if not keyfile:
        return False
    try:
        if Path(keyfile).is_symlink():
            return False
        want = STICK_HASH.read_text().split()[0].strip()
        return hashlib.sha256(Path(keyfile).read_bytes()).hexdigest() == want
    except (OSError, IndexError):
        return False


def _read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def stick_info(keyfile):
    """{device, model, size, mount, fingerprint} for the stick holding this key file (best effort)."""
    out = {"mount": str(keyfile.parent.parent) if keyfile else "", "device": "", "model": "", "size": "", "fingerprint": ""}
    if not keyfile:
        return out
    try:
        out["fingerprint"] = hashlib.sha256(keyfile.read_bytes()).hexdigest()[:12]
        src = subprocess.run(["findmnt", "-T", str(keyfile), "-no", "SOURCE"], capture_output=True, text=True, timeout=5).stdout.strip()
        out["device"] = src
        j = json.loads(subprocess.run(["lsblk", "-J", "-o", "NAME,SIZE,MODEL,PKNAME", src], capture_output=True, text=True, timeout=5).stdout)
        d = j["blockdevices"][0]
        out["size"] = d.get("size", "")
        parent = d.get("pkname")
        if parent:
            j2 = json.loads(subprocess.run(["lsblk", "-J", "-d", "-o", "NAME,MODEL", f"/dev/{parent}"], capture_output=True, text=True, timeout=5).stdout)
            out["model"] = (j2["blockdevices"][0].get("model") or "").strip()
    except Exception:
        pass
    return out


def sudo_state(keyfile):
    """(code, text): is sudo set to accept the stick, and would it accept it right now?"""
    if not HELPER.exists():
        return "off", "Not set up"
    try:
        pam_ok = "recall-stick-check" in PAM_FILE.read_text()
    except OSError:
        pam_ok = False
    if not pam_ok:
        return "off", "Not set up"
    env = dict(os.environ, PAM_SERVICE="sudo", PAM_USER=os.environ.get("USER", ""))
    try:
        ok = subprocess.run([str(HELPER)], env=env, capture_output=True, timeout=5).returncode == 0
    except Exception:
        ok = False
    if not keyfile:
        # the helper also accepts the stick while it's in your other computer (cross-device), so ask it either way
        return ("on", "Active through the stick in your other computer") if ok else ("idle", "Set up, stick not plugged in")
    return ("on", "Active: sudo needs no password") if ok else ("warn", "Set up, but this stick isn't recognized")


# ------------------------------------------------------------------ passwords
LOWER, UPPER, DIGITS, SYMBOLS = string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%^&*()-_=+[]{};:,.?/"
SIMILAR = set("O0oIl1|")


def generate(length=20, lower=True, upper=True, digits=True, symbols=True, no_similar=True):
    pools = [p for p, on in ((LOWER, lower), (UPPER, upper), (DIGITS, digits), (SYMBOLS, symbols)) if on] or [LOWER]
    if no_similar:
        pools = ["".join(c for c in p if c not in SIMILAR) for p in pools]
    length = max(length, len(pools))
    chars = [secrets.choice(p) for p in pools]                       # at least one of each chosen kind
    allc = "".join(pools)
    chars += [secrets.choice(allc) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


OTP_LINE = re.compile(r"^[ \t]*(otpauth://\S+)[ \t]*$", re.M)


def split_otp(notes):
    """(notes without the setup line, otpauth URI or '')."""
    m = OTP_LINE.search(notes or "")
    if not m:
        return (notes or ""), ""
    rest = (notes[:m.start()] + notes[m.end():]).strip("\n")
    return rest, m.group(1)


HIST_LINE = re.compile(r"^pm-history\|(\d{4}-\d{2}-\d{2})\|(.+)$", re.M)
PEND_LINE = re.compile(r"^pm-pending\|([^|]*)\|(.*)$", re.M)
MAX_HISTORY = 5


def split_meta(notes):
    """(plain notes, otpauth link, [(date, old password)...], (group, title) this is the pending new password for, or None)."""
    rest, otp = split_otp(notes)
    hist = [(m.group(1), m.group(2)) for m in HIST_LINE.finditer(rest)]
    pm_ = PEND_LINE.search(rest)
    pend = (pm_.group(1), pm_.group(2)) if pm_ else None
    rest = PEND_LINE.sub("", HIST_LINE.sub("", rest)).strip("\n")
    return rest, otp, hist, pend


def join_meta(notes, otp="", hist=(), pend=None):
    parts = [(notes or "").rstrip("\n")]
    parts += [f"pm-history|{d}|{p}" for d, p in list(hist)[:MAX_HISTORY]]
    if pend:
        parts.append(f"pm-pending|{pend[0]}|{pend[1]}")
    if otp:
        parts.append(otp)
    return "\n".join(p for p in parts if p)


def join_otp(notes, otp):
    notes = (notes or "").rstrip("\n")
    return f"{notes}\n{otp}" if otp and notes else (otp or notes)


def normalize_otp(text):
    """Accepts an otpauth:// link or a plain setup key (letters and numbers, spaces allowed). -> an otpauth:// link, or '' if empty. Raises ValueError if it isn't one."""
    t = (text or "").strip()
    if not t:
        return ""
    if t.lower().startswith("otpauth://"):
        otp_params(t)                                                      # validates
        return t
    key = re.sub(r"[\s-]", "", t).upper().rstrip("=")
    otp_params(f"otpauth://totp/x?secret={key}")
    return f"otpauth://totp/Account?secret={key}"


def otp_params(uri):
    u = urllib.parse.urlparse(uri)
    if u.scheme != "otpauth" or u.netloc.lower() != "totp":
        raise ValueError("Only time-based codes (otpauth://totp) are supported.")
    q = {k.lower(): v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
    secret = re.sub(r"[\s-]", "", q.get("secret", "")).upper()
    if not secret or not re.fullmatch(r"[A-Z2-7]+=*", secret):
        raise ValueError("That doesn't look like a setup key.")
    algo = q.get("algorithm", "SHA1").upper()
    if algo not in ("SHA1", "SHA256", "SHA512"):
        raise ValueError("Unsupported code type.")
    digits = int(q.get("digits", "6"))
    period = int(q.get("period", "30"))
    if digits not in (6, 7, 8) or not 5 <= period <= 120:
        raise ValueError("Unsupported code settings.")
    return secret, algo.lower(), digits, period


def totp_now(uri, now=None):
    """(code, seconds left) for an otpauth:// link (RFC 6238)."""
    secret, algo, digits, period = otp_params(uri)
    now = time.time() if now is None else now
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", int(now // period)), algo).digest()
    off = mac[-1] & 15
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits), period - int(now % period)


COMMON = ("password", "passw0rd", "123456", "qwerty", "letmein", "welcome", "iloveyou", "admin", "abc123", "monkey", "dragon", "football",
          "baseball", "master", "login", "princess", "sunshine", "trustno1", "changeme")


def weakness(pw):
    """Why a password is easy to guess ('' when it isn't). Looks at length, common words, simple patterns and repeats."""
    if not pw:
        return ""
    low = pw.lower()
    if len(pw) < 8:
        return "it is shorter than 8 characters"
    if len(set(pw)) <= 3:
        return "it repeats a few characters"
    if any(w in low for w in COMMON):
        return "it contains a very common password or word"
    if re.fullmatch(r"[A-Za-z]+\d{1,4}[^\w\s]?", pw) and len(pw) < 14:
        return "it is a word followed by a few numbers, which is easy to guess"
    if re.fullmatch(r"\d+", pw) and len(pw) < 14:
        return "it is only numbers"
    if len(pw) < 12 and re.search(r"(0123|1234|2345|3456|4567|5678|6789|abcd|qwer|asdf|zxcv)", low):
        return "it contains a keyboard or number sequence"
    return ""


def strength(pw):
    """(bits, label, colour) from length and character kinds, lowered for the easy-to-guess cases above. A rough guide: it can't know about reuse or breaches."""
    if not pw:
        return 0, "", C["muted"]
    pool = (26 if any(c.islower() for c in pw) else 0) + (26 if any(c.isupper() for c in pw) else 0) + (10 if any(c.isdigit() for c in pw) else 0) \
        + (32 if any(not c.isalnum() for c in pw) else 0)
    bits = len(pw) * math.log2(max(pool, 2))
    if weakness(pw):
        bits = min(bits, 38)
    if bits < 40:
        return bits, "Weak", C["bad"]
    if bits < 60:
        return bits, "Fair", C["warn"]
    if bits < 90:
        return bits, "Strong", C["good"]
    return bits, "Excellent", C["good"]


def new_phone_key():
    """A fresh 256-bit key, shown to you as groups of letters and numbers to type into the phone once."""
    raw = base64.b32encode(secrets.token_bytes(32)).decode().rstrip("=")
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def phone_key_bytes(code):
    t = re.sub(r"[\s-]", "", code or "").upper()
    try:
        key = base64.b32decode(t + "=" * (-len(t) % 8))
    except ValueError:
        raise VaultError("The phone key isn't valid. Make a new one in Settings.")
    if len(key) != 32:
        raise VaultError("The phone key isn't valid. Make a new one in Settings.")
    return key


def build_phone_blob(entries, code):
    """PMV1 + nonce + ChaCha20-Poly1305(JSON of the logins). Only the key holder (you, on your computers and phone) can read it."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    except ImportError:
        raise VaultError("The phone copy needs the Python 'cryptography' package: sudo apt install python3-cryptography")
    items = [{"title": e["title"], "group": e["group"], "username": e["username"], "password": e["password"], "url": e["url"], "notes": e["notes"],
              "otp": e.get("otp", "")} for e in entries if e["group"].split("/")[0] != PENDING_GROUP and (e["password"] or e.get("otp"))]
    payload = json.dumps({"v": 1, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "entries": items}, separators=(",", ":")).encode()
    nonce = os.urandom(12)
    return b"PMV1" + nonce + ChaCha20Poly1305(phone_key_bytes(code)).encrypt(nonce, payload, b"PMV1")


def open_phone_blob(blob, code):
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    if not blob.startswith(b"PMV1"):
        raise VaultError("Not a phone vault.")
    return json.loads(ChaCha20Poly1305(phone_key_bytes(code)).decrypt(blob[4:16], blob[16:], b"PMV1"))


def phone_request(method, body=None):
    try:
        tok = TOKEN_FILE.read_text().strip()
    except OSError:
        raise VaultError("No Recall sign-in on this computer. Run setup-autosync.sh once.")
    req = urllib.request.Request(SERVER_URL + "/vault-phone", data=body, method=method,
                                 headers={"Authorization": f"Bearer {tok}", "User-Agent": "jblol-pm/1", "X-From": os.uname().nodename})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        e.close()
        raise VaultError(f"The server refused the phone copy ({e.code}).")
    except (urllib.error.URLError, OSError) as e:
        raise VaultError(f"Couldn't reach the server ({getattr(e, 'reason', e)}).")


def compute_health(entries):
    """{(group, title): [(kind, text), ...]} for entries with a weak or reused password."""
    counts = {}
    for e in entries:
        if e["password"]:
            counts.setdefault(e["password"], []).append(e)
    out = {}
    for e in entries:
        if not e["password"]:
            continue
        iss = []
        why = weakness(e["password"])
        if why or strength(e["password"])[1] == "Weak":
            iss.append(("weak", f"Weak: {why or 'it is too easy to guess'}."))
        same = counts[e["password"]]
        if len(same) > 1:
            names = sorted({o["title"] for o in same if o is not e})
            clip = lambda t: t if len(t) <= 34 else t[:33] + "…"
            shown = ", ".join(clip(n) for n in names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
            iss.append(("reused", f"Reused: the same password is used for {shown}."))
        if iss:
            out[(e["group"], e["title"])] = iss
    return out


SHARED_HOSTS = ("github.io", "blogspot.com", "herokuapp.com", "netlify.app", "vercel.app", "pages.dev", "web.app", "firebaseapp.com", "azurewebsites.net",
                "appspot.com", "wixsite.com", "weebly.com", "000webhostapp.com")
TWO_LEVEL = ("co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.jp", "co.nz", "co.in", "com.br", "com.mx", "co.za", "com.cn", "com.sg", "edu.au")


def reg_domain(host):
    """Roughly the site a host belongs to (example.com for login.example.com). Hosting sites that give each customer a subdomain stay separate."""
    h = (host or "").lower().strip(".")
    for sh in SHARED_HOSTS:
        if h == sh or h.endswith("." + sh):
            return ".".join(h.split(".")[-(sh.count(".") + 2):])
    p = h.split(".")
    if len(p) <= 2:
        return h
    return ".".join(p[-3:]) if ".".join(p[-2:]) in TWO_LEVEL else ".".join(p[-2:])


def match_entries(entries, host):
    """Entries saved for the site this page is on. Same site (not just similar text)."""
    host = (host or "").lower().strip(".")
    if not host:
        return []
    want = reg_domain(host)
    out = []
    for e in entries:
        u = (e.get("url") or "").strip()
        if not u or u.startswith(("ssh://", "steam")):
            continue
        h = host_of(u)
        if h and reg_domain(h) == want and e.get("password"):
            out.append(e)
    return out


# ------------------------------------------------------------------ the vault (KeePassXC's command-line tool)
class VaultError(Exception):
    pass


VAULT_LOCK = threading.RLock()                  # one vault command (or a sync merge) at a time


def cli_path():
    return shutil.which("keepassxc.cli") or shutil.which("keepassxc-cli")


class Vault:
    def __init__(self, db, key):
        self.db, self.key = str(db), str(key)
        self.cli = shutil.which("keepassxc.cli") or shutil.which("keepassxc-cli")
        if not self.cli:
            raise VaultError("KeePassXC isn't installed. Install it with: sudo snap install keepassxc")
        self.root = "Passwords"

    def run(self, sub, opts=(), args=(), stdin="", timeout=40):
        cmd = [self.cli, sub, "--no-password", "-k", self.key, *opts, self.db, *args]
        try:
            with VAULT_LOCK:
                r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise VaultError("The vault took too long to answer.")
        if r.returncode != 0:
            msg = (r.stderr or r.stdout).strip().splitlines()
            text = msg[-1] if msg else "KeePassXC refused the request."
            if "ermission denied" in text or "ailed to load key file" in text:
                raise VaultError("KeePassXC isn't allowed to read the USB stick yet. Run this once in a terminal, then unplug and replug the stick:\n"
                                 "sudo snap connect keepassxc:removable-media")
            raise VaultError(text)
        return r.stdout

    def load(self):
        rows = csv.DictReader(io.StringIO(self.run("export", ("-f", "csv"), stdin="\n")))
        out = []
        for r in rows:
            g = (r.get("Group") or "")
            parts = g.split("/")
            self.root = parts[0] or self.root
            group = "/".join(parts[1:])
            if group.split("/")[0] in HIDDEN_GROUPS:
                continue
            notes, otp, hist, pend = split_meta(r.get("Notes", ""))
            notes, kind, data = _typed.unpack(notes)
            if kind == "login":
                kind = _typed.GROUP_KIND.get(group.split("/")[0], "login")
            out.append({"group": group, "title": r.get("Title", ""), "username": r.get("Username", ""), "password": r.get("Password", ""),
                        "url": r.get("URL", ""), "notes": notes, "otp": otp, "history": hist, "pending_for": pend, "modified": r.get("Last Modified", ""),
                        "kind": kind, "data": data})
        out.sort(key=lambda e: (e["title"].lower(), e["group"]))
        return out

    def groups(self):
        names = [l.rstrip("/") for l in self.run("ls", ("-R", "-f"), stdin="\n").splitlines() if l.endswith("/")]
        return [n for n in names if n.split("/")[0] not in HIDDEN_GROUPS]

    @staticmethod
    def path(group, title):
        return f"{group}/{title}" if group else title

    @staticmethod
    def check_name(name, what):
        if not name.strip():
            raise VaultError(f"Give the {what} a name.")
        if "/" in name:
            raise VaultError(f"The {what} name can't contain “/”.")

    def ensure_group(self, group):
        if group and group not in self.groups():
            self.run("mkdir", args=(group,), stdin="\n")

    def attachments(self, e):
        try:
            txt = self.run("show", ("-q",), args=(self.path(e["group"], e["title"]),), stdin="\n")
        except VaultError:
            return []
        return [l.split(":", 1)[1].strip() for l in txt.splitlines() if l.startswith("Attachment")] if "Attachment" in txt else []

    def add(self, e):
        self.check_name(e["title"], "entry")
        self.ensure_group(e["group"])
        self.run("add", ("-q", "-u", e["username"], "--url", e["url"], "--notes", join_meta(_typed.pack(e["notes"], e.get("kind", "login"), e.get("data")), e.get("otp", ""), e.get("history", ()), e.get("pending_for")), "-p"), args=(self.path(e["group"], e["title"]),),
                 stdin=e["password"] + "\n")

    def edit(self, old, new):
        self.check_name(new["title"], "entry")
        p = self.path(old["group"], old["title"])
        hist = list(new.get("history", old.get("history", ())))
        if new["password"] != old["password"] and old["password"]:
            hist = [(date.today().isoformat(), old["password"])] + hist
        new["history"] = hist[:MAX_HISTORY]
        opts = ["-q", "-u", new["username"], "--url", new["url"], "--notes", join_meta(_typed.pack(new["notes"], new.get("kind", "login"), new.get("data")), new.get("otp", ""), new["history"], new.get("pending_for"))]
        if new["title"] != old["title"]:
            opts += ["-t", new["title"]]
        if new["password"] != old["password"]:
            opts += ["-p"]
            self.run("edit", opts, args=(p,), stdin=new["password"] + "\n")
        else:
            self.run("edit", opts, args=(p,), stdin="\n")
        if new["group"] != old["group"]:
            self.ensure_group(new["group"])
            self.run("mv", ("-q",), args=(self.path(old["group"], new["title"]), new["group"] or "/"), stdin="\n")

    def delete(self, e):
        self.run("rm", ("-q",), args=(self.path(e["group"], e["title"]),), stdin="\n")

    def attach(self, group, title, file_path):
        """Attach a file (an SSH private key) to an entry, under the file's own name. The KeePassXC snap can't read a
        dot-folder like ~/.ssh, so such a file is staged into a private plain folder for the moment it takes, then removed."""
        file_path = Path(file_path)
        if not file_path.is_file():
            raise VaultError(f"{file_path.name} doesn't exist.")
        src, stage = file_path, None
        if any(p.startswith(".") for p in file_path.parts[len(HOME.parts):]):
            d = HOME / "PasswordManager-Stage"
            d.mkdir(mode=0o700, exist_ok=True)
            stage = d / file_path.name
            shutil.copy2(file_path, stage)
            stage.chmod(0o600)
            src = stage
        try:
            self.run("attachment-import", args=(self.path(group, title), file_path.name, str(src)), stdin="\n")
        finally:
            if stage is not None:
                wipe_file(stage)


# ------------------------------------------------------------------ keeping every computer's vault the same (through your server)
SYNC_DIR = os.environ.get("JBLOL_PM_SYNC_DIR")  # tests: a local folder standing in for the server


def _ssh_opts():
    o = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]
    k = HOME / ".ssh" / "recall_machines"
    if k.exists():
        o += ["-i", str(k)]                       # this computer's own key, in case the vault's server key isn't loaded yet
    return o


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _remote_get(remote, rpath, dest):
    """True if the server has a copy (now at dest), False if there is none yet."""
    if SYNC_DIR:
        src = Path(SYNC_DIR) / Path(rpath).name
        if not src.exists():
            return False
        shutil.copy2(src, dest)
        return True
    r = subprocess.run(["scp", "-q", *_ssh_opts(), f"{remote}:{rpath}", str(dest)], capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        return True
    if "No such file" in r.stderr:
        return False
    if _presence_sync():                           # no stick here (its key is what normally reaches the server): use this computer's own
        try:
            return presence.vault_get(load_settings(), dest)
        except (OSError, subprocess.SubprocessError):
            pass
    raise VaultError("Couldn't reach the server (" + (r.stderr.strip().splitlines() or ["unknown error"])[-1] + "). Is the stick plugged in?")


def _presence_sync():
    """This computer was paired for cross-device (it has its own limited server key), and the vault is at the usual place."""
    return presence is not None and presence.SSH_KEY.exists() and load_settings().get("sync_path") == "vault-sync/recall.kdbx"


def _remote_put(remote, rpath, src):
    if SYNC_DIR:
        Path(SYNC_DIR).mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, Path(SYNC_DIR) / Path(rpath).name)
        return
    d = str(Path(rpath).parent)
    r = subprocess.run(["ssh", *_ssh_opts(), remote, f"mkdir -p {d} && chmod 700 {d}"], capture_output=True, text=True, timeout=30)
    if r.returncode and _presence_sync():
        try:
            presence.vault_put(load_settings(), src)
            return
        except (OSError, subprocess.SubprocessError) as e:
            raise VaultError(f"Couldn't upload the vault: {e}")
    if r.returncode:
        raise VaultError("Couldn't reach the server: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1])
    r = subprocess.run(["scp", "-q", *_ssh_opts(), str(src), f"{remote}:{rpath}.new"], capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        r = subprocess.run(["ssh", *_ssh_opts(), remote, f"chmod 600 '{rpath}.new' && mv '{rpath}.new' '{rpath}'"], capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise VaultError("Couldn't upload the vault: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1])


def _fingerprint(db, key):
    """A hash of what is IN the vault (not of the file's bytes, which change every time KeePass saves it)."""
    cli = cli_path()
    with VAULT_LOCK:
        r = subprocess.run([cli, "export", "-f", "csv", "--no-password", "-k", str(key), str(db)], input="\n", capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise VaultError("Couldn't read the vault: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1])
    return hashlib.sha256("\n".join(sorted(r.stdout.splitlines())).encode()).hexdigest()


def sync_vault(db, key, remote, rpath):
    """Make this computer's vault and the server's copy the same: fetch, merge (KeePass keeps the newest version of each entry), upload.
    -> (message, changed_locally). The vault file is encrypted; the server never sees the key file."""
    local = Path(db)
    inc = local.with_name("recall-sync-incoming.kdbx")
    try:
        if not _remote_get(remote, rpath, inc):
            if local.exists():
                _remote_put(remote, rpath, local)
                return "Uploaded to the server", False
            return "Nothing to sync yet", False
        if not local.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(inc), str(local))
            return "Downloaded from the server", True
        cli = cli_path()
        if not cli:
            raise VaultError("KeePassXC isn't installed.")
        before = _fingerprint(local, key)
        if before == _fingerprint(inc, key):
            return "Up to date", False
        with VAULT_LOCK:
            shutil.copy2(local, local.with_name(local.name + ".bak"))
            r = subprocess.run([cli, "merge", "-q", "--no-password", "-k", str(key), "--no-password-from", "--key-file-from", str(key), str(local), str(inc)],
                               input="\n", capture_output=True, text=True, timeout=60)
            if r.returncode:
                raise VaultError("Couldn't merge with the server's copy: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1])
            changed = _fingerprint(local, key) != before
        _remote_put(remote, rpath, local)
        return "Merged with the server", changed
    finally:
        try:
            inc.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ filling logins in the browser (talks only to the browser extension, on this computer)
class Bridge:
    """A tiny web server on 127.0.0.1 that the browser extension asks "what do you have for this site?".
    Every request needs the pairing code and must come from a browser extension (not a web page); it is refused while the vault is locked."""

    def __init__(self, port, token, state, on_fill, on_capture=None):
        self.port, self.token, self.state, self.on_fill, self.on_capture = port, token, state, on_fill, on_capture
        self.last = None                      # the most recent request: {at, code, path, host}
        self.last_n = "-"
        self.log_path = CONF_DIR / "bridge.log"

        bridge = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body, origin=None):
                try:
                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    bridge.last = {"at": time.time(), "code": code, "path": urllib.parse.urlparse(self.path).path, "host": (q.get("host") or [""])[0]}
                    bridge.log_line(f"{time.strftime('%H:%M:%S')} {code} {bridge.last['path']} host={bridge.last['host'] or '-'} matches={getattr(bridge, 'last_n', '-')} "
                                    f"origin={'extension' if (self.headers.get('Origin') or '').startswith(('moz-extension', 'chrome-extension')) else (self.headers.get('Origin') or 'none')} "
                                    f"fetch-site={self.headers.get('Sec-Fetch-Site') or '-'}")
                except Exception:  # noqa: BLE001
                    pass
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                if origin and origin != "-":
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Headers", "X-PM-Token, Content-Type")
                self.end_headers()
                self.wfile.write(data)

            def ok_request(self):
                host = (self.headers.get("Host") or "").lower()
                if host not in (f"127.0.0.1:{bridge.port}", f"localhost:{bridge.port}"):
                    return None                                            # DNS-rebinding guard
                # Web pages always send an Origin header (a page's address) when they call another address with a custom header, and a browser marks them
                # cross-site. Extensions in Firefox send their own moz-extension:// origin; in Brave and Chrome an extension sends no Origin and is marked "none".
                origin = self.headers.get("Origin") or ""
                if origin:
                    return origin if origin.startswith(("moz-extension://", "chrome-extension://")) else None
                if (self.headers.get("Sec-Fetch-Site") or "none").lower() != "none":
                    return None
                return "-"                                                 # no Origin to echo back

            def do_OPTIONS(self):
                o = self.ok_request()
                if not o:
                    return self.send(403, {"error": "forbidden"})
                self.send(204, {}, o)

            def do_GET(self):
                o = self.ok_request()
                if not o:
                    return self.send(403, {"error": "forbidden"})
                if not hmac.compare_digest(self.headers.get("X-PM-Token", ""), bridge.token):
                    return self.send(401, {"error": "bad pairing code"}, o)
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                locked, entries = bridge.state()
                if u.path == "/status":
                    return self.send(200, {"ok": True, "locked": locked, "count": len(entries)}, o)
                if u.path == "/fill":
                    if locked:
                        return self.send(423, {"error": "locked"}, o)
                    host = (q.get("host") or [""])[0].lower()
                    scheme = (q.get("scheme") or [""])[0]
                    if not (scheme in ("https:", "https") or host in ("localhost", "127.0.0.1")):
                        return self.send(400, {"error": "only https pages"}, o)
                    hits = match_entries(entries, host)
                    bridge.last_n = len(hits)
                    bridge.on_fill(host, len(hits))
                    return self.send(200, {"matches": [{"title": e["title"], "username": e["username"], "password": e["password"]} for e in hits]}, o)
                self.send(404, {"error": "not found"}, o)

            def do_POST(self):
                o = self.ok_request()
                if not o:
                    return self.send(403, {"error": "forbidden"})
                if not hmac.compare_digest(self.headers.get("X-PM-Token", ""), bridge.token):
                    return self.send(401, {"error": "bad pairing code"}, o)
                locked, _entries = bridge.state()
                if urllib.parse.urlparse(self.path).path != "/capture" or not bridge.on_capture:
                    return self.send(404, {"error": "not found"}, o)
                if locked:
                    return self.send(423, {"error": "locked"}, o)
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if not 0 < n <= 8192:
                        return self.send(400, {"error": "bad size"}, o)
                    body = json.loads(self.rfile.read(n).decode("utf-8", "replace"))
                    if not isinstance(body, dict):
                        raise ValueError
                except ValueError:
                    return self.send(400, {"error": "bad request"}, o)
                bridge.on_capture({k: (str(body.get(k) or "")[:300] if k != "password" else str(body.get(k) or "")[:500]) for k in ("host", "scheme", "username", "password", "kind")})
                self.send(200, {"ok": True}, o)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def log_line(self, text):
        """One line per request (time, result, site, how many logins matched). Never passwords. Kept small."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > 60000:
                self.log_path.write_text("".join(self.log_path.read_text().splitlines(True)[-100:]))
            with open(self.log_path, "a") as f:
                f.write(text + "\n")
        except OSError:
            pass


    def start(self):
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# ------------------------------------------------------------------ small widgets




















# ------------------------------------------------------------------ importing from a browser's exported CSV
def parse_csv(path):
    """Rows from a Brave / Chrome / Edge / Firefox / Bitwarden-style CSV export -> [{title, url, username, password, notes}]."""
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    rd = csv.DictReader(io.StringIO(raw))
    cols = {(c or "").strip().lower(): c for c in (rd.fieldnames or [])}

    def pick(row, *names):
        for n in names:
            if n in cols and row.get(cols[n]) is not None:
                return row[cols[n]]
        return ""
    rows = []
    for r in rd:
        pw = pick(r, "password", "login_password")
        if not pw:
            continue
        url = pick(r, "url", "login_uri", "website", "origin").strip()
        user = pick(r, "username", "login_username", "user", "email")
        title = (pick(r, "name", "title") or host_of(url) or user or "Imported").strip().replace("/", "-")
        otp = ""
        try:
            otp = normalize_otp(pick(r, "otpauth", "totp", "login_totp", "otp", "one-time password", "verification code"))
        except ValueError:
            pass                                                              # an unsupported code type: still import the password
        rows.append({"title": title, "url": url, "username": user, "password": pw, "notes": pick(r, "note", "notes"), "otp": otp})
    if not rows and not rd.fieldnames:
        raise VaultError("That file is empty.")
    return rows


def host_of(url):
    """The site's host name: no scheme, login name, port or path (https://me@www.example.com:8443/x -> example.com)."""
    u = (url or "").strip().lower()
    u = u.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    u = u.rsplit("@", 1)[-1]
    if u.startswith("["):                                     # an IPv6 address in brackets
        return u.split("]")[0] + "]"
    u = u.split(":", 1)[0]
    return u[4:] if u.startswith("www.") else u


def plan_import(rows, existing, group):
    """Skip what the vault already has (same site and username); make titles unique inside the group. -> (to_add, skipped)."""
    have = {(host_of(e["url"]), e["username"].lower()) for e in existing}
    taken = {e["title"].lower() for e in existing if e["group"] == group}
    out, skipped = [], 0
    for r in rows:
        key = (host_of(r["url"]), r["username"].lower())
        if key in have and key != ("", ""):
            skipped += 1
            continue
        have.add(key)
        t = r["title"]
        if t.lower() in taken:
            t = f"{t} ({r['username']})" if r["username"] else t
        n = 2
        base = t
        while t.lower() in taken:
            t = f"{base} {n}"
            n += 1
        taken.add(t.lower())
        out.append(dict(r, title=t, group=group))
    return out, skipped


class ImportWorker(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(int, list)

    def __init__(self, db, key, rows):
        super().__init__()
        self.db, self.key, self.rows = db, key, rows

    def run(self):
        errs, done = [], 0
        try:
            v = Vault(self.db, self.key)
        except VaultError as e:
            self.finished_ok.emit(0, [str(e)])
            return
        for n, r in enumerate(self.rows, 1):
            try:
                v.add(r)
                done += 1
            except VaultError as e:
                errs.append(f"{r['title']}: {e}")
            self.progress.emit(n, len(self.rows))
        self.finished_ok.emit(done, errs)


class SyncWorker(QThread):
    done = Signal(str, bool, str)             # message, changed locally, error

    def __init__(self, db, key, remote, rpath):
        super().__init__()
        self.a = (db, key, remote, rpath)

    def run(self):
        try:
            msg, changed = sync_vault(*self.a)
            self.done.emit(msg, changed, "")
        except VaultError as e:
            self.done.emit("", False, str(e))
        except Exception as e:  # noqa: BLE001
            self.done.emit("", False, f"Sync failed: {e}")


class PhoneWorker(QThread):
    done = Signal(str, str)                   # message, error

    def __init__(self, entries, code):
        super().__init__()
        self.entries, self.code = entries, code

    def run(self):
        try:
            blob = build_phone_blob(self.entries, self.code)
            phone_request("PUT", blob)
            self.done.emit(f"Sent {len(self.entries)} logins to the phone copy", "")
        except VaultError as e:
            self.done.emit("", str(e))
        except Exception as e:  # noqa: BLE001
            self.done.emit("", f"Phone copy failed: {e}")


class Signals(QObject):
    filled = Signal(str, int)                 # the browser asked for a site's logins
    captured = Signal(dict)                   # the browser saw a password typed into a form


EVENT_LOG = CONF_DIR / "events.log"


def log_event(text):
    """One line per event (never a password). Kept small."""
    try:
        EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        if EVENT_LOG.exists() and EVENT_LOG.stat().st_size > 60000:
            EVENT_LOG.write_text("".join(EVENT_LOG.read_text().splitlines(True)[-300:]))
        with open(EVENT_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")
    except OSError:
        pass


def wipe_file(path):
    """Overwrite then delete. On an SSD or a journaling filesystem this can't guarantee the old bits are gone, but it beats leaving a plain-text file."""
    p = Path(path)
    try:
        size = p.stat().st_size
        with open(p, "r+b") as f:
            f.write(secrets.token_bytes(size))
            f.flush()
            os.fsync(f.fileno())
        p.unlink()
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ desktop apps: recognise an app that just opened and fill its login
TYPER_SOCKET = "/run/pm-typer/socket"
SKIP_APPS = ("brave", "firefox", "chrome", "chromium", "vivaldi", "opera", "epiphany", "librewolf", "zen", "jblol", "recall", "terminal", "kgx", "ptyxis", "console",
             "nautilus", "org gnome", "gnome", "code oss", "settings", "software", "files")
GENERIC = {"desktop", "app", "client", "linux", "official", "login", "account", "password", "website", "the", "gtk", "qt", "web", "online", "my"}


def norm(text):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower().replace(".desktop", "")).split())


def app_keys(appid, appname, wmclass):
    keys = set()
    for t in (appid, appname, wmclass):
        n = norm(t)
        if n:
            keys.add(n)
            keys.update(w for w in n.split() if len(w) >= 3 and w not in GENERIC)
    return keys


def match_app_entries(entries, appid, appname, wmclass):
    """Entries whose NAME is the app's name (an entry called “Claude” for the Claude app)."""
    keys = app_keys(appid, appname, wmclass)
    out = []
    for e in entries:
        if not e.get("password") or (e.get("url") or "").startswith("ssh://"):
            continue
        name = " ".join(w for w in norm(re.sub(r"\(.*?\)", " ", e["title"])).split() if w not in GENERIC)
        if len(name) >= 3 and name in keys:
            out.append(e)
    out.sort(key=lambda e: (e["group"] != "Apps", e["title"].lower()))
    return out


# ------------------------------------------------------------------ one login, both a website and its computer app
# A website whose service also has an app (on the computer or the phone) that you sign into with the same account gets a
# copy of the login in the Apps group (named after the app, so app autofill finds it). The two stay linked: change the
# password on either one and the other follows.
APP_SITES = {"discord": "Discord", "discordapp": "Discord", "steamcommunity": "Steam", "steampowered": "Steam", "claude": "Claude",
             "github": "GitHub Desktop", "roblox": "Roblox", "live": "Microsoft", "microsoftonline": "Microsoft", "microsoft": "Microsoft",
             "netflix": "Netflix", "crunchyroll": "Crunchyroll", "collegeboard": "Bluebook", "spotify": "Spotify", "slack": "Slack",
             "zoom": "Zoom", "notion": "Notion", "epicgames": "Epic Games", "battle": "Battle.net", "telegram": "Telegram",
             "whatsapp": "WhatsApp", "dropbox": "Dropbox", "figma": "Figma", "twitch": "Twitch", "minecraft": "Minecraft",
             # services whose app is (mostly) on the phone
             "walmart": "Walmart", "paypal": "PayPal", "wendys": "Wendy's", "publix": "Publix", "kroger": "Kroger", "nike": "Nike",
             "aa": "American Airlines", "americanairlines": "American Airlines", "usps": "USPS Mobile", "bestbuy": "Best Buy",
             "myworkdayjobs": "Workday", "depop": "Depop", "lego": "LEGO", "pizzahut": "Pizza Hut", "littlecaesars": "Little Caesars",
             "samsung": "Samsung Account", "ford": "FordPass", "progressive": "Progressive", "wellsfargo": "Wells Fargo",
             "wellsfargoadvisors": "Wells Fargo", "hulu": "Hulu", "hbomax": "Max", "max": "Max", "paramountplus": "Paramount+",
             "pinterest": "Pinterest", "facebook": "Facebook", "instagram": "Instagram", "snapchat": "Snapchat", "google": "Google",
             "paychex": "Paychex Flex", "focusschoolsoftware": "Focus", "cloudflare": "Cloudflare", "expo": "Expo Go",
             "petsuppliesplus": "Pet Supplies Plus", "hottopic": "Hot Topic", "duolingo": "Duolingo", "tiktok": "TikTok",
             "twitter": "X", "x": "X", "reddit": "Reddit", "amazon": "Amazon", "uber": "Uber", "doordash": "DoorDash",
             "chase": "Chase", "bankofamerica": "Bank of America", "capitalone": "Capital One", "venmo": "Venmo", "cashapp": "Cash App"}
APP_HOSTS = {"recall.jblol.systems": "Recall"}           # your own apps, by exact address
TWIN_NOTE = "Same login as the website entry “{}”: change the password on either one and both are updated."
TWIN_RE = re.compile(r"^Same login as the website entry “(.+?)”")


def app_for_site(url):
    h = host_of(url or "")
    if not h or h.startswith("passwordreset."):
        return None
    return APP_HOSTS.get(h) or APP_SITES.get(reg_domain(h).split(".")[0])


def plan_app_twins(entries, only=None):
    """New Apps-group copies for website logins whose service has a computer app: one per app and account (the newest
    password wins where the same account was saved twice), skipping any the Apps group already has. only: just for these."""
    in_apps = [e for e in entries if e["group"].split("/")[0] == "Apps"]
    have = {(norm(re.sub(r"\(.*?\)", " ", e["title"])), e["username"].lower()) for e in in_apps}
    taken = {e["title"].lower() for e in in_apps}
    out = []
    for e in sorted(only if only is not None else entries, key=lambda e: e.get("modified") or "", reverse=True):
        if e["group"].split("/")[0] != "Websites" or not e.get("password") or not e.get("username"):
            continue
        app = app_for_site(e.get("url")) if (e.get("url") or "").strip() else \
            APP_SITES.get((norm(e["title"]).split() or [""])[0])          # no address saved: go by its name ("Duolingo")
        if not app or (norm(app), e["username"].lower()) in have:
            continue
        have.add((norm(app), e["username"].lower()))
        t, n = f"{app} ({e['username']})", 2
        while t.lower() in taken:
            t, n = f"{app} ({e['username']}) {n}", n + 1
        taken.add(t.lower())
        if (e.get("url") or "").strip():
            out.append({"group": "Apps", "title": t, "username": e["username"], "password": e["password"], "url": "",
                        "notes": TWIN_NOTE.format(e["title"]), "otp": e.get("otp", ""), "history": []})
        else:      # saved without a website (only ever used in the app): it moves to Apps instead of being kept twice
            out.append({**e, "group": "Apps", "title": t, "url": "", "move_from": e})
    return out


def twins_of(entries, e):
    """The linked copies of login e (as it was before an edit): its Apps copy, or for an Apps copy its website login."""
    mine = TWIN_RE.match(e.get("notes") or "")
    out = []
    for x in entries:
        if (x["group"], x["title"]) == (e["group"], e["title"]) or x["username"] != e["username"] or x["password"] != e["password"]:
            continue
        theirs = TWIN_RE.match(x.get("notes") or "")
        if (theirs and theirs.group(1) == e["title"]) or (mine and mine.group(1) == x["title"]):
            out.append(x)
    return out


def match_site_entries_for_app(entries, appid, appname, wmclass):
    """No entry is named after the app: the website logins for the same service instead (the Discord app -> a login saved
    for discord.com, Steam -> steampowered.com). Matched on the site's own name, the part before .com, against the app's
    names; never on a generic word, and short names must match exactly."""
    keys = {k for k in app_keys(appid, appname, wmclass) if " " not in k and len(k) >= 3}
    out = []
    for e in entries:
        u = (e.get("url") or "").strip()
        if not e.get("password") or not u or u.startswith(("ssh://", "steam")):
            continue
        h = host_of(u)
        label = reg_domain(h).split(".")[0] if h else ""
        if len(label) < 3 or label in GENERIC:
            continue
        if label in keys or any(min(len(k), len(label)) >= 5 and (k.startswith(label) or label.startswith(k)) for k in keys):
            out.append(e)
    out.sort(key=lambda e: e["title"].lower())
    return out


def type_login_text(username, password):
    """Types into whatever window has focus. The typing helper is a root service that only starts for this, only your user can reach it, and it stops itself."""
    env = {**os.environ, "YDOTOOL_SOCKET": TYPER_SOCKET}
    quiet = {"stdin": subprocess.DEVNULL, "capture_output": True, "timeout": 20}
    try:
        subprocess.run(["sudo", "systemctl", "start", "pm-typer"], **quiet)
        for _ in range(30):
            if Path(TYPER_SOCKET).exists():
                break
            time.sleep(0.1)
        else:
            return "The typing helper didn't start (is the stick plugged in?)."
        if username:
            subprocess.run(["ydotool", "type", "-d", "12", "-f", "-"], input=username.encode(), env=env, capture_output=True, timeout=20)
            subprocess.run(["ydotool", "key", "15:1", "15:0"], env=env, capture_output=True, timeout=10)       # Tab
        subprocess.run(["ydotool", "type", "-d", "12", "-f", "-"], input=password.encode(), env=env, capture_output=True, timeout=30)
        return ""
    except (OSError, subprocess.SubprocessError) as e:
        return f"Couldn't type: {e}"
    finally:
        try:
            subprocess.run(["sudo", "systemctl", "stop", "pm-typer"], stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass


def focused_app():
    """(app id, title) of the window with focus, from the window helper extension, or None."""
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.jblol.PmFocus", "--object-path", "/org/jblol/PmFocus", "--method", "org.jblol.PmFocus.GetFocus"],
                             capture_output=True, text=True, timeout=5)
        return ast.literal_eval(out.stdout.strip()) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, SyntaxError):
        return None


# ------------------------------------------------------------------ sharing through pm.jblol.systems
SHARE_TTLS = (("10 minutes", 600), ("1 hour", 3600), ("24 hours", 86400))       # never longer


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _share_call(s, path, body):
    req = urllib.request.Request(s["share_url"].rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + s["share_token"], "User-Agent": "PasswordManager/1.1 (jblol.systems)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise VaultError({401: "The share server didn't accept this computer's share key.", 429: "Too many active links. Wait for some to expire."}.get(e.code, f"The share server said {e.code}."))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise VaultError(f"Couldn't reach the share server: {e}")


def create_share(s, entries, ttl, once, note=""):
    """Encrypts here with a fresh random AES-256-GCM key, uploads only the ciphertext, and returns (link, id).
    The key goes in the link's #fragment, which browsers never send to any server, so the server (and Cloudflare) can't read it."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not s.get("share_token"):
        raise VaultError("This computer has no share key yet (Settings).")
    key, iv = AESGCM.generate_key(256), os.urandom(12)
    payload = json.dumps({"note": note, "entries": [{"title": e["title"], "username": e["username"], "password": e["password"], "url": e["url"]} for e in entries]}).encode()
    r = _share_call(s, "/api/create", {"ct": _b64u(AESGCM(key).encrypt(iv, payload, None)), "iv": _b64u(iv), "ttl": ttl, "once": bool(once)})
    return f"{s['share_url'].rstrip('/')}/s/{r['id']}#{_b64u(key)}", r["id"]




# ------------------------------------------------------------------ "a new password was found" confirm menu
CAT_SOCK = f"recall-cat-{os.environ.get('USER') or os.environ.get('USERNAME') or ''}"


def tell_cat(msg, wait=800):
    """Send one JSON line to the desktop cat's private socket. Returns its answer, or None when the cat isn't running."""
    sock = QLocalSocket()
    sock.connectToServer(CAT_SOCK)
    if not sock.waitForConnected(300):
        return None
    sock.write((json.dumps(msg) + "\n").encode())
    sock.flush()
    sock.waitForReadyRead(wait)
    sock.disconnectFromServer()
    return {}




# ------------------------------------------------------------------ list rows


# ------------------------------------------------------------------ the entry editor / viewer


# ------------------------------------------------------------------ main window


def main():
    from pmapp.window import main as run
    return run()


if __name__ == "__main__":
    sys.exit(main())
