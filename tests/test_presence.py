"""Cross-device stick (presence.py) and its root sudo check, with two simulated computers sharing a local stand-in server.

    python3 -m unittest tests.test_presence -v      (from the password-manager folder)
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="pm-presence-"))
os.environ["JBLOL_PM_SYNC_DIR"] = str(TMP / "server")
os.environ["JBLOL_PM_STICK_HASH"] = str(TMP / "stick.sha256")
sys.path.insert(0, str(ROOT))
import presence as P  # noqa: E402
P.SYNC_DIR, P.STICK_HASH = str(TMP / "server"), TMP / "stick.sha256"     # in case another test imported it first
P.SSH_KEY = TMP / "presence-ssh"


def _no_server(*a, **k):
    raise AssertionError("tests must never reach the real server")


P._ssh = _no_server                 # an earlier version of this test once registered a key on the real server: never again

KEY = os.urandom(64)
(TMP / "stick.sha256").write_text(hashlib.sha256(KEY).hexdigest() + "\n")


class Computer:
    """One simulated computer: its own config folder, removable-media folder and name."""
    def __init__(self, name):
        self.name, self.conf, self.media = name, TMP / name / "conf", TMP / name / "media"
        self.conf.mkdir(parents=True)
        self.media.mkdir(parents=True)
        (self.conf / "settings.json").write_text(json.dumps({"cross_device": True}))
        self.daemon = None

    def use(self):
        P.CONF_DIR, P.MEDIA = self.conf, self.media
        P.BOX_KEY, P.PUB_FILE, P.INCOMING, P.STATE = (self.conf / n for n in ("presence-box.key", "presence.pub", "presence-in.json", "presence-state.json"))
        P.LOCK_IN, P.LOCK_OUT, P.LOCKS_SEEN, P.SEEN, P.DEVICES, P.EVENT_LOG = (self.conf / n for n in (
            "presence-lock.json", "presence-lock-out.json", "presence-locks-seen.json", "presence-seen.json", "devices.json", "events.log"))
        P.machine_name = lambda: self.name
        if self.daemon is None:
            self.daemon = P.Daemon()
        return self

    def plug(self):
        (self.media / "STICK" / "Vault").mkdir(parents=True, exist_ok=True)
        f = self.media / "STICK" / "Vault" / P.KEYFILE_NAME
        f.write_bytes(KEY)
        return f


def move_stick(a, b):
    import shutil
    shutil.move(str(a.media / "STICK"), str(b.media / "STICK"))


class Crypto(unittest.TestCase):
    def setUp(self):
        self.sk = P.signing_key(KEY)
        self.pub = P.public_pem(KEY)

    def msg(self, **kw):
        now = int(time.time())
        body = {"v": 1, "from": "laptop", "iat": now, "exp": now + 60, "to": ["desktop"], **kw}
        return P.sign(self.sk, body)

    def test_genuine_heartbeat_is_accepted(self):
        self.assertIsNotNone(P.verify(self.pub, self.msg(), "desktop"))

    def test_rejections(self):
        other = P.public_pem(os.urandom(64))
        self.assertIsNone(P.verify(other, self.msg(), "desktop"), "signed by a different stick")
        m = self.msg()
        m["body"]["to"] = ["desktop", "intruder"]
        self.assertIsNone(P.verify(self.pub, m, "intruder"), "edited after signing")
        self.assertIsNone(P.verify(self.pub, self.msg(), "intruder"), "not addressed to this computer")
        self.assertIsNone(P.verify(self.pub, self.msg(revoked=True), "desktop"), "a 'stick removed' notice")
        self.assertIsNone(P.verify(self.pub, self.msg(), "desktop", now=time.time() + 120), "expired")
        self.assertIsNone(P.verify(self.pub, self.msg(exp=int(time.time()) + 3600), "desktop"), "valid for too long")
        self.assertIsNone(P.verify(self.pub, self.msg(), "laptop"), "a computer never accepts its own")
        self.assertIsNone(P.verify(self.pub, {"body": {}, "sig": "!!"}, "desktop"))
        self.assertIsNone(P.verify(self.pub, "junk", "desktop"))

    def test_seal_only_opens_for_its_computer(self):
        priv, pub = P.new_box()
        blob = P.seal(pub, KEY)
        self.assertEqual(P.unseal(priv, blob), KEY)
        other, _ = P.new_box()
        with self.assertRaises(Exception):
            P.unseal(other, blob)


class TwoComputers(unittest.TestCase):
    def setUp(self):
        import shutil
        shutil.rmtree(TMP / "server", ignore_errors=True)
        for n in ("laptop", "desktop"):
            shutil.rmtree(TMP / n, ignore_errors=True)
        self.laptop, self.desktop = Computer("laptop"), Computer("desktop")
        # pair both: the stick is plugged into each once
        self.desktop.use().plug()
        P.pair(self.desktop.media / "STICK" / "Vault" / P.KEYFILE_NAME, vault=True)
        move_stick(self.desktop, self.laptop)
        P.pair(self.laptop.use().media / "STICK" / "Vault" / P.KEYFILE_NAME, vault=False)

    def test_sudo_and_passwords_follow_the_stick_and_stop_when_it_leaves(self):
        now = time.time()
        self.laptop.use().daemon.step(now)
        self.assertEqual(json.loads(P.STATE.read_text())["to"], ["desktop"])
        self.desktop.use().daemon.step(now)
        self.assertEqual(P.remote_status(now)[0], "laptop")
        self.assertEqual(P.remote_keyfile(now), KEY, "the desktop (allowed passwords) gets the key file")
        # the laptop didn't allow passwords: nothing was sealed for it when the desktop held the stick
        move_stick(self.laptop, self.desktop)
        self.laptop.use().daemon.step(now + 1)                    # it notices the stick left: posts "gone"
        self.desktop.use().daemon.fetched_at = 0
        self.desktop.daemon.step(now + 1)
        self.assertIsNone(P.remote_status(now + 1), "stick moved to the desktop itself: nothing remote any more")
        self.assertFalse(P.INCOMING.exists())
        self.desktop.daemon.step(now + 25)
        self.laptop.use().daemon.step(now + 25)
        self.assertEqual(P.remote_status(now + 25)[0], "desktop")
        self.assertIsNone(P.remote_keyfile(now + 25), "the laptop wasn't allowed passwords")

    def test_heartbeat_runs_out_without_network(self):
        now = time.time()
        self.laptop.use().daemon.step(now)
        self.desktop.use().daemon.step(now)
        self.assertIsNotNone(P.remote_status(now + 30))
        self.assertIsNone(P.remote_status(now + P.TTL_S + 1))

    def test_turned_off_takes_nothing(self):
        now = time.time()
        self.laptop.use().daemon.step(now)
        (self.desktop.conf / "settings.json").write_text(json.dumps({"cross_device": False}))
        self.desktop.use().daemon.step(now)
        self.assertFalse(P.INCOMING.exists())

    def test_unpaired_computer_is_left_out(self):
        now = time.time()
        P.unpair(self.laptop.use().media / "STICK" / "Vault" / P.KEYFILE_NAME)      # the laptop leaves (stick is in it)
        self.laptop.daemon.step(now)
        self.assertEqual(json.loads(P.STATE.read_text())["to"], ["desktop"])
        P.pair(self.laptop.media / "STICK" / "Vault" / P.KEYFILE_NAME, vault=False)
        move_stick(self.laptop, self.desktop)
        P.unpair(None)                                               # turned off on the laptop without the stick
        self.desktop.use().daemon.sent_at = 0
        self.desktop.daemon.step(now + 2)
        self.laptop.use().daemon.step(now + 2)
        self.assertIsNotNone(P.remote_status(now + 2), "still listed on the stick")
        self.assertFalse(P.BOX_KEY.exists())


class Locking(TwoComputers):
    """Lock the screen when the stick is pulled (here and on the other computer), and "Lock device" from the app."""

    def setUp(self):
        super().setUp()
        self.locked = []
        P.lock_session = lambda: self.locked.append(P.machine_name()) or True
        P.beacon = lambda now=None: {"name": P.machine_name(), "at": int(now or time.time()), "os": "Test OS", "locked": False, "stick": False}
        self.conf_for(self.laptop, lock_screen_on_pull=True, lock_peers_on_pull=True)
        self.conf_for(self.desktop)

    def conf_for(self, comp, **kw):
        (comp.conf / "settings.json").write_text(json.dumps({"cross_device": True, **kw}))

    def pull_from_laptop(self, now):
        self.laptop.use().daemon.step(now)                         # the stick is here: heartbeat names the desktop
        import shutil
        shutil.rmtree(self.laptop.media / "STICK")                 # pulled
        self.laptop.use().daemon.step(now + 1)

    def desktop_looks(self, now):
        self.desktop.use().daemon.fetched_at = 0
        self.desktop.daemon.step(now)

    def test_pulling_the_stick_locks_this_computer(self):
        self.pull_from_laptop(time.time())
        self.assertEqual(self.locked, ["laptop"])
        self.assertIn("screen was locked", (self.laptop.conf / "events.log").read_text())

    def test_pulling_the_stick_locks_the_other_computer_too(self):
        now = time.time()
        self.pull_from_laptop(now)
        self.desktop_looks(now + 2)
        self.assertEqual(self.locked, ["laptop", "desktop"])
        self.assertEqual(json.loads((self.desktop.conf / "presence-lock.json").read_text())["from"], "laptop")

    def test_the_order_runs_once(self):
        now = time.time()
        self.pull_from_laptop(now)
        self.desktop_looks(now + 2)
        self.desktop_looks(now + 14)
        self.assertEqual(self.locked.count("desktop"), 1)

    def test_switches_off_means_nothing_is_locked(self):
        self.conf_for(self.laptop)
        self.pull_from_laptop(time.time())
        self.assertEqual(self.locked, [])

    def test_lock_device_button(self):
        now = time.time()
        self.laptop.use().daemon.step(now)                         # the laptop holds the stick
        P.request_lock(["desktop"])
        self.laptop.daemon.sent_at = now                           # a normal beat is not due, yet the order goes out at once
        self.laptop.daemon.step(now + 3)
        self.desktop_looks(now + 4)
        self.assertEqual(self.locked, ["desktop"])
        self.assertIn("Told desktop to lock", (self.laptop.conf / "events.log").read_text())

    def test_orders_that_are_forged_stale_or_for_someone_else_do_nothing(self):
        pub = P.public_pem(KEY)
        now = int(time.time())
        def blob(sk, **body):
            b = {"v": 1, "from": "laptop", "iat": now, "exp": now + 60, "to": ["desktop"], "lock": {"id": "x", "to": ["desktop"]}, **body}
            return json.dumps(P.sign(sk, b)).encode()
        good = blob(P.signing_key(KEY))
        self.assertEqual(P.find_lock([good], pub, "desktop", now), ("x", "laptop"))
        self.assertIsNone(P.find_lock([blob(P.signing_key(os.urandom(64)))], pub, "desktop", now), "signed by another stick")
        self.assertIsNone(P.find_lock([good], pub, "desktop", now + P.LOCK_TTL_S + 5), "too old")
        self.assertIsNone(P.find_lock([good], pub, "tablet", now), "addressed to another computer")
        self.assertIsNone(P.find_lock([good], pub, "laptop", now), "a computer never obeys itself")
        self.assertIsNone(P.find_lock([blob(P.signing_key(KEY), lock={"id": "y", "to": ["tablet"]})], pub, "desktop", now))
        self.assertIsNone(P.find_lock([b"junk", b"{}"], pub, "desktop", now))

    def test_a_lock_order_does_not_unlock_sudo(self):
        pub = P.public_pem(KEY)
        now = int(time.time())
        gone = P.sign(P.signing_key(KEY), {"v": 1, "from": "laptop", "iat": now, "exp": now, "to": ["desktop"], "revoked": True, "lock": {"id": "z", "to": ["desktop"]}})
        self.assertIsNone(P.verify(pub, gone, "desktop"), "a pull notice with a lock order is still a 'stick is gone' notice")

    def test_device_list_shows_other_computers_reports(self):
        now = time.time()
        self.desktop.use().daemon.step(now)                        # the desktop reports in
        self.laptop.use().plug()
        P.pair(self.laptop.media / "STICK" / "Vault" / P.KEYFILE_NAME, vault=False)
        self.laptop.daemon.seen_at = 0
        self.laptop.daemon.step(now + 1)
        devs = P.devices(self.laptop.media / "STICK" / "Vault" / P.KEYFILE_NAME)
        self.assertEqual([d["name"] for d in devs], ["laptop", "desktop"])
        self.assertTrue(devs[0]["me"])
        self.assertEqual(devs[1]["seen"]["os"], "Test OS")
        self.assertEqual([d["name"] for d in P.devices(None)], ["laptop", "desktop"], "remembered while the stick is away")


class RootCheck(unittest.TestCase):
    """The installed sudo helper, run against a heartbeat file, with its fixed paths pointed at test files."""
    def run_check(self, msg, me="desktop", user=None, owner_ok=True):
        user = user or os.environ.get("USER") or "jblol"
        home = TMP / "home"
        conf = home / ".config" / "jblol-password-manager"
        conf.mkdir(parents=True, exist_ok=True)
        (conf / "presence-in.json").write_text(json.dumps(msg))
        (TMP / "root.pub").write_bytes(P.public_pem(KEY))
        (TMP / "root.name").write_text(me + "\n")
        src = (ROOT / "sudo-stick" / "recall-stick-remote-check.py").read_text()
        src = src.replace("__USER__", user).replace('"/etc/recall-stick.presence.pub"', repr(str(TMP / "root.pub"))) \
                 .replace('"/etc/recall-stick.presence.name"', repr(str(TMP / "root.name"))).replace("pw.pw_dir", repr(str(home)))
        if not owner_ok:
            src = src.replace("st.st_uid != pw.pw_uid", "True")
        script = TMP / "check.py"
        script.write_text(src)
        env = {"PAM_USER": user, "PATH": "/usr/bin:/bin"}
        return subprocess.run([sys.executable, "-I", str(script)], env=env, capture_output=True).returncode

    def beat(self, **kw):
        now = int(time.time())
        return P.sign(P.signing_key(KEY), {"v": 1, "from": "laptop", "iat": now, "exp": now + 60, "to": ["desktop"], **kw})

    def test_accepts_only_a_current_signed_heartbeat_for_this_computer(self):
        self.assertEqual(self.run_check(self.beat()), 0)
        self.assertEqual(self.run_check(self.beat(), me="other"), 1)
        self.assertEqual(self.run_check(self.beat(revoked=True)), 1)
        self.assertEqual(self.run_check(self.beat(iat=int(time.time()) - 200, exp=int(time.time()) - 140)), 1)
        forged = P.sign(P.signing_key(os.urandom(64)), self.beat()["body"])
        self.assertEqual(self.run_check(forged), 1)
        self.assertEqual(self.run_check(self.beat(), user="someoneelse"), 1)
        self.assertEqual(self.run_check(self.beat(), owner_ok=False), 1, "a file someone else wrote is ignored")

    def test_matches_presence_verify(self):
        for kw in ({}, {"revoked": True}, {"to": ["x"]}):
            m = self.beat(**kw)
            self.assertEqual(self.run_check(m) == 0, P.verify(P.public_pem(KEY), m, "desktop") is not None, kw)


if __name__ == "__main__":
    unittest.main()


class OldServer(unittest.TestCase):
    """A server whose script predates the device list says "not allowed": the daemon must keep saying so until a call really works."""

    def test_server_old_flag_sticks_until_a_call_succeeds(self):
        c = Computer("oldsrv").use()
        d = c.daemon
        calls = []
        real_put, real_get = P.seen_put, P.seen_get
        P.seen_put = lambda s, i: (_ for _ in ()).throw(OSError("not allowed"))
        P.seen_get = lambda s: (_ for _ in ()).throw(OSError("not allowed"))
        P.beacon = lambda now=None: {"name": "oldsrv", "at": 0, "os": "x", "locked": False, "stick": False}
        try:
            now = time.time()
            d.beacons({}, now)
            self.assertTrue(d.server_old)
            d.beacons({}, now + 1)                      # nothing is due this second: the flag must not flip back
            self.assertTrue(d.server_old)
            P.seen_put, P.seen_get = real_put, real_get
            d.beacons({}, now + 60)
            self.assertFalse(d.server_old)
        finally:
            P.seen_put, P.seen_get = real_put, real_get
