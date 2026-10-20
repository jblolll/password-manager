import json, os, shutil, subprocess, sys, tempfile, unittest, urllib.request, urllib.error
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jblol_pm as pm

HAVE_CLI = bool(pm.cli_path())
# the KeePassXC snap can only read non-hidden folders under the home directory
BASE = Path.home() / "pm-test-tmp"


def e(title, user="u", pw="x", url="", group="Websites"):
    return {"group": group, "title": title, "username": user, "password": pw, "url": url, "notes": "", "modified": ""}


class Weakness(unittest.TestCase):
    def test_weak_cases(self):
        for pw in ("abc", "password1", "Summer2024", "12345678", "aaaaaaaaaaaa", "Qwerty!1234"):
            self.assertTrue(pm.weakness(pw) or pm.strength(pw)[1] == "Weak", pw)

    def test_strong_cases(self):
        for pw in ("v7#Qm2!pLx9$Rt4w", pm.generate(20)):
            self.assertEqual(pm.weakness(pw), "", pw)
            self.assertIn(pm.strength(pw)[1], ("Strong", "Excellent"))


class AppFill(unittest.TestCase):
    def test_app_named_entry_wins_then_website_fallback(self):
        es = [e("Discord", url="https://discord.com/login"), e("GitHub", url="https://github.com")]
        self.assertEqual([x["title"] for x in pm.match_app_entries(es, "com.discordapp.Discord.desktop", "Discord", "discord")], ["Discord"])
        site = [e("Chat login", url="https://discord.com/login"), e("Steam account", url="https://store.steampowered.com"),
                e("GitHub", url="https://github.com"), e("Mail", url="https://app.example.com")]
        got = lambda *a: [x["title"] for x in pm.match_site_entries_for_app(site, *a)]
        self.assertEqual(got("com.discordapp.Discord.desktop", "Discord", "discord"), ["Chat login"], "the Discord app finds discord.com")
        self.assertEqual(got("steam.desktop", "Steam", "steam"), ["Steam account"], "Steam finds steampowered.com")
        self.assertEqual(got("org.gnome.Calculator.desktop", "Calculator", "gnome-calculator"), [], "no unrelated site")
        self.assertEqual(got("app.desktop", "App", "app"), [], "never on a generic word")

    def test_sync_is_always_on(self):
        old = pm.SETTINGS
        try:
            pm.SETTINGS = BASE / "s.json"
            BASE.mkdir(exist_ok=True)
            pm.SETTINGS.write_text(json.dumps({"sync_enabled": False}))
            self.assertTrue(pm.load_settings()["sync_enabled"])
        finally:
            pm.SETTINGS = old


class AppTwins(unittest.TestCase):
    def test_plan(self):
        es = [e("discordapp.com (me@x.com)", user="me@x.com", pw="d1", url="https://discordapp.com/login"),
              e("claude account 1", user="a@x.com", pw="c1", url="https://claude.ai"),
              e("claude account 2", user="b@x.com", pw="c2", url="https://claude.ai"),
              e("login.live.com (me@x.com)", user="me@x.com", pw="m1", url="https://login.live.com"),
              e("login.microsoftonline.com (me@x.com)", user="me@x.com", pw="m1", url="https://login.microsoftonline.com"),
              e("passwordreset.microsoftonline.com (z)", user="z", pw="z", url="https://passwordreset.microsoftonline.com"),
              e("walmart.com (me)", user="me", pw="w", url="https://www.walmart.com"),
              e("recall.jblol.systems", user="", pw="r", url="https://recall.jblol.systems"),
              e("Claude (old@x.com)", user="old@x.com", pw="o", group="Apps"), e("instagram 2", user="ig", pw="i")]
        got = {t["title"]: t for t in pm.plan_app_twins(es)}
        self.assertEqual(sorted(got), ["Claude (a@x.com)", "Claude (b@x.com)", "Discord (me@x.com)", "Instagram (ig)", "Microsoft (me@x.com)", "Walmart (me)"])
        self.assertEqual(got["Discord (me@x.com)"]["password"], "d1")
        self.assertTrue(all(t["group"] == "Apps" and t["url"] == "" for t in got.values()))
        self.assertEqual(pm.app_for_site("https://example-shop.com"), None, "an unknown site gets no app copy")
        self.assertEqual(got["Instagram (ig)"]["move_from"]["title"], "instagram 2", "no website saved: moved, not copied")
        self.assertNotIn("move_from", got["Discord (me@x.com)"])
        self.assertEqual(pm.plan_app_twins(es + list(got.values())), [], "running it again adds nothing")
        # the copy is found by the Discord app, and stays linked to its website login
        self.assertEqual([x["title"] for x in pm.match_app_entries(list(got.values()), "com.discordapp.Discord", "Discord", "discord")], ["Discord (me@x.com)"])
        site = es[0]
        self.assertEqual([x["title"] for x in pm.twins_of(es + list(got.values()), site)], ["Discord (me@x.com)"])
        self.assertEqual([x["title"] for x in pm.twins_of(es + list(got.values()), got["Discord (me@x.com)"])], [site["title"]])

    @unittest.skipUnless(HAVE_CLI, "KeePassXC isn't installed")
    def test_attach_ssh_key_from_a_dot_folder(self):
        d = BASE / "attach"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        key, db = d / "k.keyx", d / "v.kdbx"
        key.write_bytes(os.urandom(64))
        subprocess.run([pm.cli_path(), "db-create", "-q", "--set-key-file", str(key), str(db)], check=True, capture_output=True)
        v = pm.Vault(db, key)
        v.ensure_group("Servers & SSH")
        v.add({"group": "Servers & SSH", "title": "desktop", "username": "u", "password": "", "url": "ssh://desktop", "notes": "", "otp": "", "history": []})
        hidden = Path.home() / ".pm-test-hidden"
        hidden.mkdir(exist_ok=True)
        (hidden / "id_ed25519").write_text("not a real key\n")
        try:
            v.attach("Servers & SSH", "desktop", hidden / "id_ed25519")
        finally:
            shutil.rmtree(hidden)
        out = v.run("show", ("-q", "--show-attachments"), args=("Servers & SSH/desktop",), stdin="\n")
        self.assertIn("id_ed25519", out)
        self.assertFalse(any((Path.home() / "PasswordManager-Stage").glob("*")), "the staged copy is gone")


class SudoStatus(unittest.TestCase):
    def test_stick_in_other_computer_shows_active(self):
        BASE.mkdir(exist_ok=True)
        old = pm.HELPER, pm.PAM_FILE
        try:
            pm.PAM_FILE = BASE / "pam"
            pm.PAM_FILE.write_text("auth sufficient pam_exec.so quiet /usr/local/sbin/recall-stick-check\n")
            pm.HELPER = BASE / "helper"
            for rc, want in ((0, "on"), (1, "idle")):
                pm.HELPER.write_text(f"#!/bin/sh\nexit {rc}\n")
                pm.HELPER.chmod(0o755)
                self.assertEqual(pm.sudo_state(None)[0], want)
        finally:
            pm.HELPER, pm.PAM_FILE = old


class Matching(unittest.TestCase):
    def test_reg_domain(self):
        self.assertEqual(pm.reg_domain("login.example.com"), "example.com")
        self.assertEqual(pm.reg_domain("www.bbc.co.uk"), "bbc.co.uk")
        self.assertEqual(pm.reg_domain("evil.github.io"), "evil.github.io")
        self.assertNotEqual(pm.reg_domain("evil.github.io"), pm.reg_domain("good.github.io"))

    def test_match(self):
        ents = [e("GH", "a", "p1", "https://github.com"), e("GH2", "b", "p2", "https://github.com/login"), e("Amz", "c", "p3", "https://amazon.com"),
                e("srv", "d", "p4", "ssh://1.2.3.4"), e("nopw", "e", "", "https://github.com")]
        names = [x["title"] for x in pm.match_entries(ents, "github.com")]
        self.assertEqual(sorted(names), ["GH", "GH2"])                       # two accounts for one site, both offered
        self.assertEqual([x["title"] for x in pm.match_entries(ents, "gist.github.com")], ["GH", "GH2"])
        self.assertEqual(pm.match_entries(ents, "github.com.evil.com"), [])   # lookalike host
        self.assertEqual(pm.match_entries(ents, "notgithub.com"), [])


class Importing(unittest.TestCase):
    def test_same_site_different_accounts_and_skips(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.csv"
            f.write_text("name,url,username,password,note\nGitHub,https://github.com,a@x.com,pw1,\nGitHub,https://github.com,b@x.com,pw2,\nGitHub,https://github.com,a@x.com,pw1,dupe in file\nNo,https://n.com,u,,\n")
            rows = pm.parse_csv(f)
            self.assertEqual(len(rows), 3)
            add, skipped = pm.plan_import(rows, [e("Old", "a@x.com", "pw1", "https://www.github.com")], "Websites")
            self.assertEqual([r["username"] for r in add], ["b@x.com"])       # a@x.com is already saved (even as www.), the in-file duplicate too
            self.assertEqual(skipped, 2)
            add2, _ = pm.plan_import(rows, [], "Websites")
            self.assertEqual(sorted(r["title"] for r in add2), ["GitHub", "GitHub (b@x.com)"])   # unique names for two accounts


@unittest.skipUnless(HAVE_CLI, "KeePassXC isn't installed")
class VaultAndSync(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(exist_ok=True)
        self.d = Path(tempfile.mkdtemp(dir=BASE))
        self.key = self.d / "k.key"
        self.key.write_text(pm.generate(64, symbols=False))
        self.srv = self.d / "server"

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def mk(self, name):
        p = self.d / name
        subprocess.run([pm.cli_path(), "db-create", "--set-key-file", str(self.key), str(p)], capture_output=True, check=True)
        return pm.Vault(p, self.key)

    def test_sync_two_computers(self):
        pm.SYNC_DIR = str(self.srv)
        try:
            a = self.mk("a.kdbx")
            a.add(e("FromA", "ua", "pa", "https://a.com"))
            self.assertEqual(pm.sync_vault(a.db, self.key, "x", "vault-sync/recall.kdbx")[0], "Uploaded to the server")
            b_path = self.d / "b.kdbx"
            msg, changed = pm.sync_vault(b_path, self.key, "x", "vault-sync/recall.kdbx")       # a computer with no vault yet
            self.assertEqual((msg, changed), ("Downloaded from the server", True))
            b = pm.Vault(b_path, self.key)
            self.assertEqual([x["title"] for x in b.load()], ["FromA"])
            b.add(e("FromB", "ub", "pb", "https://b.com"))
            a.add(e("FromA2", "ua2", "pa2", "https://a2.com"))
            pm.sync_vault(b.db, self.key, "x", "vault-sync/recall.kdbx")
            msg, changed = pm.sync_vault(a.db, self.key, "x", "vault-sync/recall.kdbx")
            self.assertTrue(changed)
            self.assertEqual(sorted(x["title"] for x in a.load()), ["FromA", "FromA2", "FromB"])   # nothing lost on either side
            pm.sync_vault(b.db, self.key, "x", "vault-sync/recall.kdbx")
            self.assertEqual(sorted(x["title"] for x in b.load()), ["FromA", "FromA2", "FromB"])
            self.assertEqual(pm.sync_vault(a.db, self.key, "x", "vault-sync/recall.kdbx")[0], "Up to date")
        finally:
            pm.SYNC_DIR = None

    def test_edit_and_add_roundtrip(self):
        v = self.mk("v.kdbx")
        v.add(e("Site", "me", "pw1", "https://s.com"))
        old = v.load()[0]
        v.edit(old, dict(old, password="pw2", group="Apps"))
        got = v.load()
        self.assertEqual((got[0]["group"], got[0]["password"]), ("Apps", "pw2"))

class BridgeSecurity(unittest.TestCase):
    PORT = 47633

    def setUp(self):
        self.state = {"locked": False, "entries": [e("GH", "a", "p1", "https://github.com"), e("GH2", "b", "p2", "https://github.com")]}
        self.seen = []
        self.b = pm.Bridge(self.PORT, "TOKEN123", lambda: (self.state["locked"], self.state["entries"]), lambda h, n: self.seen.append((h, n)))
        self.b.start()

    def tearDown(self):
        self.b.stop()

    def get(self, path, token="TOKEN123", origin="moz-extension://abc", host=None, extra=None):
        h = {"Host": host or f"127.0.0.1:{self.PORT}", **(extra or {})}
        if token is not None:
            h["X-PM-Token"] = token
        if origin:
            h["Origin"] = origin
        try:
            with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.PORT}{path}", headers=h), timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as x:
            return x.code, json.loads(x.read() or b"{}")

    def test_ok_two_accounts(self):
        code, body = self.get("/fill?host=github.com&scheme=https:")
        self.assertEqual(code, 200)
        self.assertEqual(sorted(m["username"] for m in body["matches"]), ["a", "b"])
        self.assertEqual(self.seen, [("github.com", 2)])

    def test_brave_and_chrome_extensions_send_no_origin(self):
        code, body = self.get("/fill?host=github.com&scheme=https:", origin=None, extra={"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "cors"})
        self.assertEqual(code, 200)                                                              # what the real extension does in Brave (seen in a real browser)
        self.assertEqual(len(body["matches"]), 2)
        self.assertEqual(self.get("/fill?host=github.com&scheme=https:", origin="chrome-extension://abcdef")[0], 200)

    def test_no_match_other_site(self):
        self.assertEqual(self.get("/fill?host=example.org&scheme=https:")[1]["matches"], [])

    def test_refusals(self):
        self.assertEqual(self.get("/fill?host=github.com&scheme=https:", token="nope")[0], 401)
        self.assertEqual(self.get("/fill?host=github.com&scheme=https:", token=None)[0], 401)
        self.assertEqual(self.get("/fill?host=github.com&scheme=https:", origin="https://evil.com")[0], 403)   # a web page, even with the token
        self.assertEqual(self.get("/fill?host=github.com&scheme=https:", origin=None, extra={"Sec-Fetch-Site": "cross-site"})[0], 403)   # no Origin, but marked as a web request
        self.assertEqual(self.get("/fill?host=github.com&scheme=https:", host="evil.com")[0], 403)             # DNS rebinding
        self.assertEqual(self.get("/fill?host=github.com&scheme=http:")[0], 400)                               # plain http page
        self.assertEqual(self.seen, [])

    def test_locked(self):
        self.state["locked"] = True
        self.assertEqual(self.get("/fill?host=github.com&scheme=https:")[0], 423)
        self.assertEqual(self.get("/status")[1]["locked"], True)


class VerificationCodes(unittest.TestCase):
    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"      # RFC 6238 test key "12345678901234567890"

    def test_rfc6238_vectors(self):
        uri = f"otpauth://totp/x?secret={self.SECRET}&digits=8"
        self.assertEqual(pm.totp_now(uri, now=59)[0], "94287082")
        self.assertEqual(pm.totp_now(uri, now=1111111109)[0], "07081804")
        self.assertEqual(pm.totp_now(f"otpauth://totp/x?secret={self.SECRET}", now=59), ("287082", 1))   # 6 digits, 1 second left in the window
        sha256 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZA"                                  # 32-byte key for SHA-256
        self.assertEqual(pm.totp_now(f"otpauth://totp/x?secret={sha256}&digits=8&algorithm=SHA256", now=59)[0], "46119246")

    def test_normalize(self):
        self.assertEqual(pm.normalize_otp("gezd gnbv gy3t qojq gezd gnbv gy3t qojq"), f"otpauth://totp/Account?secret={self.SECRET}")
        self.assertEqual(pm.normalize_otp(""), "")
        for bad in ("not a key!", "otpauth://hotp/x?secret=ABC", "otpauth://totp/x?secret=", "otpauth://totp/x?secret=ABCDEFGH&digits=12"):
            with self.assertRaises(ValueError):
                pm.normalize_otp(bad)

    def test_notes_roundtrip(self):
        uri = f"otpauth://totp/GitHub:me?secret={self.SECRET}&issuer=GitHub"
        for notes in ("", "recovery codes in the safe", "line one\nline two"):
            joined = pm.join_otp(notes, uri)
            self.assertEqual(pm.split_otp(joined), (notes, uri))
        self.assertEqual(pm.split_otp("just notes"), ("just notes", ""))

    def test_apple_csv(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "apple.csv"
            f.write_text(f'Title,URL,Username,Password,Notes,OTPAuth\n"GitHub (me)",https://github.com,me,pw1,,otpauth://totp/GitHub:me?secret={self.SECRET}&issuer=GitHub\n'
                         'Netflix,https://netflix.com,fam,pw2,,\nOdd,https://odd.com,u,pw3,,otpauth://hotp/odd?secret=ABCDEFGH\n')
            rows = pm.parse_csv(f)
            self.assertEqual([r["title"] for r in rows], ["GitHub (me)", "Netflix", "Odd"])
            self.assertTrue(rows[0]["otp"].startswith("otpauth://totp/"))
            self.assertEqual(rows[1]["otp"], "")
            self.assertEqual(rows[2]["otp"], "")                # unsupported code type: password still imported


@unittest.skipUnless(HAVE_CLI, "KeePassXC isn't installed")
class VaultOtp(unittest.TestCase):
    def test_otp_survives_add_edit_load(self):
        BASE.mkdir(exist_ok=True)
        d = Path(tempfile.mkdtemp(dir=BASE))
        try:
            key = d / "k.key"
            key.write_text(pm.generate(64, symbols=False))
            subprocess.run([pm.cli_path(), "db-create", "--set-key-file", str(key), str(d / "v.kdbx")], capture_output=True, check=True)
            v = pm.Vault(d / "v.kdbx", key)
            uri = "otpauth://totp/Site:me?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&issuer=Site"
            v.add(dict(e("Site", "me", "pw1", "https://site.com"), notes="my notes", otp=uri))
            got = v.load()[0]
            self.assertEqual((got["notes"], got["otp"]), ("my notes", uri))
            v.edit(got, dict(got, notes="changed notes", username="me2"))
            got = v.load()[0]
            self.assertEqual((got["notes"], got["otp"], got["username"]), ("changed notes", uri, "me2"))   # the code setup is kept across edits
            v.edit(got, dict(got, otp=""))
            self.assertEqual(v.load()[0]["otp"], "")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_permission_error_is_explained(self):
        v = pm.Vault("/nonexistent/x.kdbx", "/nonexistent/k")
        fake = subprocess.CompletedProcess([], 1, "", "Failed to load key file /run/media/x/recall-vault.keyx : permission denied")
        orig = subprocess.run
        subprocess.run = lambda *a, **k: fake
        try:
            with self.assertRaises(pm.VaultError) as cm:
                v.run("ls")
        finally:
            subprocess.run = orig
        self.assertIn("snap connect keepassxc:removable-media", str(cm.exception))


class PhoneVault(unittest.TestCase):
    def test_roundtrip_and_tamper(self):
        code = pm.new_phone_key()
        self.assertEqual(len(pm.phone_key_bytes(code)), 32)
        ents = [e("GitHub", "me", "pw1", "https://github.com"), e("NoPW", "x", "", "https://x.com"), e("SSH", "u", "", "ssh://h", "Servers & SSH")]
        ents[0]["otp"] = "otpauth://totp/x?secret=GEZDGNBVGY3TQOJQ"
        blob = pm.build_phone_blob(ents, code)
        self.assertTrue(blob.startswith(b"PMV1"))
        self.assertNotIn(b"pw1", blob)
        self.assertNotIn(b"GitHub", blob)
        got = pm.open_phone_blob(blob, code)
        self.assertEqual([x["title"] for x in got["entries"]], ["GitHub"])      # entries with no password or code stay off the phone
        self.assertEqual(got["entries"][0]["otp"], ents[0]["otp"])
        bad = bytearray(blob)
        bad[-3] ^= 1
        with self.assertRaises(Exception):
            pm.open_phone_blob(bytes(bad), code)                              # a changed byte is detected
        with self.assertRaises(Exception):
            pm.open_phone_blob(blob, pm.new_phone_key())                      # the wrong key can't read it
        self.assertNotEqual(blob, pm.build_phone_blob(ents, code))             # a fresh nonce every time

    def test_history_markers(self):
        notes = pm.join_meta("my notes", "otpauth://totp/x?secret=ABCDEFGH", [("2026-10-17", "oldpw|with|bars"), ("2026-09-01", "older")], ("Websites", "Site"))
        self.assertEqual(pm.split_meta(notes), ("my notes", "otpauth://totp/x?secret=ABCDEFGH", [("2026-10-17", "oldpw|with|bars"), ("2026-09-01", "older")], ("Websites", "Site")))
        self.assertEqual(pm.split_meta("plain"), ("plain", "", [], None))


@unittest.skipUnless(HAVE_CLI, "KeePassXC isn't installed")
class HistoryInVault(unittest.TestCase):
    def test_password_change_keeps_old_in_history(self):
        BASE.mkdir(exist_ok=True)
        d = Path(tempfile.mkdtemp(dir=BASE))
        try:
            key = d / "k.key"
            key.write_text(pm.generate(64, symbols=False))
            subprocess.run([pm.cli_path(), "db-create", "--set-key-file", str(key), str(d / "v.kdbx")], capture_output=True, check=True)
            v = pm.Vault(d / "v.kdbx", key)
            v.add(dict(e("Site", "me", "first", "https://s.com"), history=[], pending_for=None))
            for pw in ("second", "third"):
                cur = v.load()[0]
                v.edit(cur, dict(cur, password=pw))
            got = v.load()[0]
            self.assertEqual(got["password"], "third")
            self.assertEqual([p for _, p in got["history"]], ["second", "first"])   # newest first
            self.assertNotIn("pm-history", got["notes"])                              # the markers never show up as notes
        finally:
            shutil.rmtree(d, ignore_errors=True)


class HostOf(unittest.TestCase):
    def test_hosts(self):
        for url, want in (("https://www.Example.com/login?x=1", "example.com"), ("http://192.168.1.1:8080/admin", "192.168.1.1"), ("https://me@site.org:8443/x", "site.org"),
                          ("github.com", "github.com"), ("http://localhost:3000", "localhost"), ("ssh://1.2.3.4", "1.2.3.4"), ("", "")):
            self.assertEqual(pm.host_of(url), want, url)
        self.assertEqual([x["title"] for x in pm.match_entries([e("Router", "a", "p", "http://localhost:8080/login")], "localhost")], ["Router"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
