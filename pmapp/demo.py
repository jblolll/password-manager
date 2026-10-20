"""Sample data for `jblol-pm --demo`: lets you look around (and screenshot) the app with no stick and none of your real passwords."""
import time



def _e(kind, group, title, username="", password="", url="", notes="", otp="", data=None, modified="2026-09-02T10:00:00Z"):
    return {"group": group, "title": title, "username": username, "password": password, "url": url, "notes": notes, "otp": otp, "history": [],
            "pending_for": None, "modified": modified, "kind": kind, "data": data or {}}


def entries():
    L = lambda *a, **k: _e("login", *a, **k)
    return [
        L("Websites", "GitHub", "jblol", "Xk9#vTq2!mPz7Lr@4wB", "https://github.com", otp="otpauth://totp/GitHub?secret=JBSWY3DPEHPK3PXP", data={"fav": True}, modified="2026-10-10T09:00:00Z"),
        L("Websites", "Google", "justin.brook@gmail.com", "correct-horse-battery-staple-92", "https://accounts.google.com", otp="otpauth://totp/Google?secret=KRSXG5CTMVRXEZLU", data={"fav": True}, modified="2026-10-12T09:00:00Z"),
        L("Websites", "Amazon", "justin.brook@gmail.com", "amazon123", "https://www.amazon.com", modified="2024-11-01T09:00:00Z"),
        L("Websites", "Netflix", "justin.brook@gmail.com", "amazon123", "https://www.netflix.com", modified="2024-11-01T09:00:00Z"),
        L("Websites", "Discord", "jblol#2210", "Qm7$nVe8&Rt3Zc!1xKd", "https://discord.com", otp="otpauth://totp/Discord?secret=MFRGGZDFMZTWQ2LK", modified="2026-09-20T09:00:00Z"),
        L("Websites/School", "Brightspace", "jbrook@patriots.cf.edu", "Patriots#2026-fall-Sem!", "https://brightspace.ucf.edu", otp="otpauth://totp/Brightspace?secret=NBSWY3DPEB3W64TM", data={"tags": ["school"]}, modified="2026-08-26T09:00:00Z"),
        L("Websites/School/Canvas", "Canvas", "jbrook@patriots.cf.edu", "Cv9!pLm2#Qr7Xz4$Wt", "https://canvas.instructure.com", data={"tags": ["school", "classes"]}, modified="2026-09-02T09:00:00Z"),
        L("Websites/Work", "Paychex Flex", "justinb04", "Hk3&nVe8!Rt5Zc2#Lm", "https://login.flex.paychex.com", otp="otpauth://totp/Paychex?secret=MFRGGZDFMZTWQ2LK", data={"tags": ["work", "money"]}, modified="2026-09-12T09:00:00Z"),
        L("Websites", "Chase", "jblol", "ZpL4!uYw9@Hq2Ns7&Vc", "https://www.chase.com", otp="otpauth://totp/Chase?secret=ONSWG4TFOQYTEMZU", data={"tags": ["money"]}, modified="2026-10-01T09:00:00Z"),
        L("Websites", "Spotify", "justin.brook", "password1", "https://open.spotify.com", modified="2025-01-14T09:00:00Z"),
        L("Apps", "Steam", "jblol_gaming", "Tr8&kLm2!Qz5Wv9#Bn", "steam://", modified="2026-07-03T09:00:00Z"),
        L("Servers & SSH", "recall server", "ubuntu", "", "ssh://129.213.162.117", notes="Public key: ssh-ed25519 AAAAC3Nz…\nFingerprint: SHA256:abc123", modified="2026-05-02T09:00:00Z"),
        _e("passkey", "Passkeys", "GitHub (jblol)", "jblol", "", "https://github.com", data={"rp_id": "github.com", "rp_name": "GitHub", "cred_id": "Kx3vQ9aZpL2mW8sT1yHn5bRcUe7dFgJ0", "user_name": "jblol",
                                                                                          "user_handle": "dXNlci0x", "created": "2026-10-12", "key": "demo", "alg": -7, "tags": ["dev"]}),
        _e("passkey", "Passkeys", "Google (justin.brook@gmail.com)", "justin.brook@gmail.com", "", "https://accounts.google.com", data={"rp_id": "google.com", "rp_name": "Google", "cred_id": "Pq7wE2rT9yUi4oAs6dFg1hJk3lZx5cVb", "user_name": "justin.brook@gmail.com",
                                                                                                                                    "user_handle": "dXNlci0y", "created": "2026-10-15", "key": "demo", "alg": -7}),
        _e("card", "Cards", "Chase Sapphire", "Justin D Brook", "", "", "Travel card. Call 1-800-432-3117 if lost.",
           data={"holder": "Justin D Brook", "number": "4242424242424242", "expiry": "08/28", "cvv": "123", "pin": "4821", "zip": "34461", "fav": True}),
        _e("card", "Cards", "Amex Gold", "Justin D Brook", "", "", "", data={"holder": "Justin D Brook", "number": "378282246310005", "expiry": "03/27", "cvv": "4411", "zip": "34461"}),
        _e("card", "Cards", "Old debit card", "Justin Brook", "", "", "", data={"holder": "Justin Brook", "number": "5555555555554444", "expiry": "01/24", "cvv": "992"}),
        _e("note", "Secure notes", "Locker combination", notes="Gym locker 214: 12 - 34 - 08\nRight, left, right."),
        _e("note", "Secure notes", "Recovery codes — Google", notes="3829 1047\n5521 9930\n7716 4402\n8830 1195\n2204 6671\n\nEach code works once."),
        _e("wifi", "Wi-Fi", "Home Wi-Fi", password="sunny-meadow-lantern-42", data={"ssid": "MySpectrumWiFi88-5G", "security": "WPA", "fav": True}),
        _e("wifi", "Wi-Fi", "Grandma's house", password="roses4ever", data={"ssid": "Brook Family", "security": "WPA"}),
    ]


class DemoVault:
    """Behaves like core.Vault, in memory."""
    db = "(demo)"
    key = "(demo)"

    def __init__(self):
        self.items = entries()

    def load(self):
        return [dict(e) for e in sorted(self.items, key=lambda e: (e["title"].lower(), e["group"]))]

    extra = set()

    def groups(self):
        out = {e["group"] for e in self.items} | {"Websites", "Apps", "Servers & SSH", "Other"} | self.extra
        for g in list(out):
            parts = g.split("/")
            out |= {"/".join(parts[:i]) for i in range(1, len(parts) + 1)}
        return sorted(out)

    def ensure_group(self, g):
        parts = g.split("/") if g else []
        self.extra = self.extra | {"/".join(parts[:i]) for i in range(1, len(parts) + 1)}

    def delete_group(self, g):
        if any(e["group"] == g or e["group"].startswith(g + "/") for e in self.items) or any(x.startswith(g + "/") for x in self.groups()):
            from jblol_pm import VaultError
            raise VaultError("The folder isn't empty.")
        self.extra = {x for x in self.extra if x != g}

    @staticmethod
    def check_name(name, what):
        from jblol_pm import Vault
        Vault.check_name(name, what)

    def attachments(self, e):
        return []

    def add(self, e):
        self.items.append(dict(e, modified=time.strftime("%Y-%m-%dT%H:%M:%SZ")))

    def edit(self, old, new):
        for i, x in enumerate(self.items):
            if (x["group"], x["title"]) == (old["group"], old["title"]):
                self.items[i] = dict(new, modified=time.strftime("%Y-%m-%dT%H:%M:%SZ"))
                return

    def delete(self, e):
        self.items = [x for x in self.items if (x["group"], x["title"]) != (e["group"], e["title"])]


def devices():
    now = time.time()
    return [{"name": "justin-ubuntu", "me": True, "vault": True, "seen": None},
            {"name": "jblol-desktop-ubuntu", "me": False, "vault": False, "seen": {"at": now - 40, "os": "Ubuntu 26.04 LTS", "locked": False, "stick": False}}]


def phones():
    return [{"id": "ph-demo", "name": "Justin's iPhone", "kind": "phone", "locked": False, "age": 12.0, "online": True, "os": "iOS 26"}]


def events():
    now = time.time()
    rows = [(-3, "stick", "USB stick plugged in"), (-3, "vault", "Vault unlocked (17 passwords)"), (-2, "sync", "Sync: Up to date"), (-600, "approve", "sudo: stick accepted"),
            (-1900, "browser", "Browser fill: github.com, 2 login(s) offered"), (-3600, "device", "Your stick is in jblol-desktop-ubuntu"), (-3700, "stick", "USB stick removed"),
            (-3701, "vault", "Vault locked: The stick was removed, so your passwords were locked."), (-3702, "device", "The stick was pulled: told jblol-desktop-ubuntu to lock"),
            (-86400, "vault", "Saved password for netflix.com"), (-86500, "share", "Created a share link for 1 item(s), 1 hour"), (-90000, "approve", "gdm-password: stick accepted from your other computer")]
    return [{"ts": now + dt, "text": t, "cat": c, "src": "system" if c == "approve" else "app"} for dt, c, t in rows]
