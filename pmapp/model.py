"""What the app knows about your items, with no windows in it (so it can be tested without a screen).

Every item is a KeePass entry. A login is an ordinary entry. A card, secure note or Wi-Fi network is an entry in its own group whose
extra fields live in the notes as two small lines (`pm-kind|card`, `pm-data|{...}`), so the vault stays a plain KeePass file that opens
in KeePassXC and syncs the way it always did.
"""
import json
import re
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

KINDS = ("login", "card", "note", "wifi")
KIND_LABEL = {"login": "Login", "card": "Card", "note": "Secure note", "wifi": "Wi-Fi"}
KIND_PLURAL = {"login": "Logins", "card": "Cards", "note": "Secure notes", "wifi": "Wi-Fi networks"}
KIND_GROUP = {"card": "Cards", "note": "Secure notes", "wifi": "Wi-Fi"}
GROUP_KIND = {v: k for k, v in KIND_GROUP.items()}

KIND_LINE = re.compile(r"^pm-kind\|(\w+)$", re.M)
DATA_LINE = re.compile(r"^pm-data\|(\{.*\})$", re.M)


def unpack(notes):
    """(plain notes, kind, data dict) from an entry's notes."""
    notes = notes or ""
    k = KIND_LINE.search(notes)
    d = DATA_LINE.search(notes)
    data = {}
    if d:
        try:
            data = json.loads(d.group(1))
        except ValueError:
            data = {}
    kind = k.group(1) if k and k.group(1) in KINDS else "login"
    return DATA_LINE.sub("", KIND_LINE.sub("", notes)).strip("\n"), kind, (data if isinstance(data, dict) else {})


def pack(notes, kind, data):
    """The notes to store for this item (the kind and fields ride along as two lines; a plain login with nothing extra stores nothing extra)."""
    parts = [(notes or "").rstrip("\n")]
    data = {k: v for k, v in (data or {}).items() if v not in ("", None, False)}
    if kind and kind != "login":
        parts.append(f"pm-kind|{kind}")
    if data:
        parts.append("pm-data|" + json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    return "\n".join(p for p in parts if p)


def kind_of(e):
    if e.get("kind") in KINDS:
        return e["kind"]
    return GROUP_KIND.get((e.get("group") or "").split("/")[0], "login")


def new_item(kind, group=""):
    e = {"group": group or KIND_GROUP.get(kind, "Websites"), "title": "", "username": "", "password": "", "url": "", "notes": "", "otp": "",
         "history": [], "pending_for": None, "modified": "", "kind": kind, "data": {}}
    return e


# ------------------------------------------------------------------ cards
BRANDS = (("Amex", r"^3[47]"), ("Visa", r"^4"), ("Mastercard", r"^(5[1-5]|2(2[2-9]|[3-6]\d|7[01]|720))"), ("Discover", r"^(6011|65|64[4-9])"),
          ("Diners", r"^(36|38|30[0-5])"), ("JCB", r"^35(2[89]|[3-8]\d)"), ("UnionPay", r"^62"))


def card_digits(number):
    return re.sub(r"\D", "", number or "")


def card_brand(number):
    n = card_digits(number)
    for name, rx in BRANDS:
        if re.match(rx, n):
            return name
    return ""


def luhn_ok(number):
    n = card_digits(number)
    if len(n) < 12:
        return False
    total = 0
    for i, ch in enumerate(reversed(n)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def card_groups(number):
    n = card_digits(number)
    if card_brand(n) == "Amex":
        return [n[:4], n[4:10], n[10:]]
    return [n[i:i + 4] for i in range(0, len(n), 4)]


def card_text(number, hidden=True):
    g = card_groups(number)
    if not hidden:
        return " ".join(x for x in g if x)
    n = card_digits(number)
    return " ".join(("•" * len(x)) if i < len(g) - 1 else x for i, x in enumerate(g)) if n else ""


def card_expired(expiry, now=None):
    m = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(\d{2}|\d{4})\s*", expiry or "")
    if not m:
        return False
    mon, yr = int(m.group(1)), int(m.group(2))
    yr = yr + 2000 if yr < 100 else yr
    t = time.localtime(now or time.time())
    return (yr, mon) < (t.tm_year, t.tm_mon)


# ------------------------------------------------------------------ Wi-Fi
WIFI_SECURITY = (("WPA", "WPA2 / WPA3"), ("WEP", "WEP"), ("nopass", "None (open)"))


def _wifi_esc(s):
    return re.sub(r'([\\;,:"])', r"\\\1", s or "")


def wifi_uri(ssid, password, security="WPA", hidden=False):
    """The text a phone camera reads to join the network: WIFI:T:WPA;S:name;P:password;;"""
    t = security if security in ("WPA", "WEP") else "nopass"
    out = f"WIFI:T:{t};S:{_wifi_esc(ssid)};"
    if t != "nopass":
        out += f"P:{_wifi_esc(password)};"
    if hidden:
        out += "H:true;"
    return out + ";"


def nm_wifi_profiles():
    """Wi-Fi networks this computer has saved, with their passwords: [(ssid, password, security)]. Empty if NetworkManager isn't there."""
    out = []
    try:
        names = subprocess.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], capture_output=True, text=True, timeout=10).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return out
    for line in names:
        name, _, typ = line.rpartition(":")
        if typ != "802-11-wireless":
            continue
        name = name.replace("\\:", ":")
        try:
            r = subprocess.run(["nmcli", "-s", "-g", "802-11-wireless.ssid,802-11-wireless-security.key-mgmt,802-11-wireless-security.psk", "connection", "show", name],
                               capture_output=True, text=True, timeout=10).stdout.split("\n")
        except (OSError, subprocess.SubprocessError):
            continue
        ssid = (r[0] if r else "") or name
        mgmt = r[1] if len(r) > 1 else ""
        psk = r[2] if len(r) > 2 else ""
        out.append((ssid, psk, "nopass" if not mgmt else "WEP" if mgmt == "none" else "WPA"))
    return out


# ------------------------------------------------------------------ passphrases
def words():
    try:
        return [w for w in (HERE / "words.txt").read_text().split() if w]
    except OSError:
        return []


# ------------------------------------------------------------------ the activity log, for the Audit log page
CATEGORIES = (("stick", "USB stick"), ("vault", "Vault"), ("device", "Devices"), ("approve", "Approvals"), ("sync", "Sync"), ("browser", "Browser"), ("share", "Sharing"))
_RULES = (("approve", r"sudo|polkit|authoriz|gdm-password|stick accepted|not accepted|login accepted"),
          ("device", r"cross-device|other computer|stick is in|stick is no longer|told .* to lock|asked .* to lock|lock order|you locked this computer|removed .* from the stick|paired"),
          ("vault", r"^vault (locked|unlocked)|locked after|went to sleep|screen locked"),
          ("stick", r"usb stick|stick was pulled|screen unlocked by"),
          ("sync", r"^sync|sync:|synced"),
          ("browser", r"browser|new password found|saved password|fill"),
          ("share", r"share link|sharing"),
          ("vault", r"vault|locked|unlocked|saved|deleted|imported|copied|moved|added|updated"))


def category_of(text):
    low = text.lower()
    for cat, rx in _RULES:
        if re.search(rx, low):
            return cat
    return "vault"


def parse_event_line(line):
    m = re.match(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\s+(.*)$", line.rstrip("\n"))
    if not m:
        return None
    ts = time.mktime(time.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S"))
    return {"ts": ts, "text": m.group(3), "cat": category_of(m.group(3)), "src": "app"}


def read_events(path, limit=1500):
    try:
        lines = Path(path).read_text().splitlines()[-limit:]
    except OSError:
        return []
    return [e for e in (parse_event_line(l) for l in lines) if e]


def read_journal(limit=200):
    """The sudo / lock-screen approvals the stick made (written by the root helper into the system journal), newest last."""
    try:
        out = subprocess.run(["journalctl", "-t", "recall-stick", "-n", str(limit), "-o", "short-unix", "--no-pager"], capture_output=True, text=True, timeout=8).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    res = []
    for line in out.splitlines():
        m = re.match(r"^(\d+)\.\d+ \S+ recall-stick\[\d+\]: (.*)$", line)
        if m:
            res.append({"ts": float(m.group(1)), "text": m.group(2), "cat": "approve", "src": "system"})
    return res


def audit_events(path, with_journal=True):
    ev = read_events(path) + (read_journal() if with_journal else [])
    seen, out = set(), []
    for e in sorted(ev, key=lambda x: x["ts"]):
        k = (int(e["ts"]), e["text"])
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def ago(ts, now=None):
    s = int((now or time.time()) - ts)
    if s < 45:
        return "just now"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    if s < 86400 * 7:
        return f"{s // 86400} d ago"
    return time.strftime("%b %d", time.localtime(ts))


def passphrase(n=5, sep="-", cap=False, number=False):
    import secrets
    ws = words()
    if not ws:
        raise ValueError("no word list")
    picks = [secrets.choice(ws) for _ in range(n)]
    if cap:
        picks = [w.capitalize() for w in picks]
    if number:
        picks[secrets.randbelow(len(picks))] += str(secrets.randbelow(100))
    return sep.join(picks)


def pin(n=6):
    import secrets
    return "".join(secrets.choice("0123456789") for _ in range(n))
