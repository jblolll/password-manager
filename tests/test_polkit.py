"""The Authentication required pop-ups (polkit) approve themselves while the stick is in: the rule that install.sh --polkit writes is run here with
a fake polkit, for every case that matters. Needs node (the shipped rule is JavaScript); skipped without it.

    python3 -m unittest tests.test_polkit -v
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

INSTALL = Path(__file__).resolve().parent.parent / "sudo-stick" / "install.sh"
NODE = shutil.which("node") or str(Path.home() / ".local/node/bin/node")


def rule_text(user="jblol", helper="/usr/local/sbin/recall-stick-polkit"):
    src = INSTALL.read_text()
    body = re.search(r"<<RULES\n(.*?)\nRULES\n", src, re.S).group(1)
    return body.replace("$U", user).replace("$PK_HELPER", helper)


def run_rule(subject, spawn_ok):
    """Load the shipped rule into node with a fake polkit and ask it about one action for this subject."""
    full = ("let rule = null; const asked = [];\n"
            "global.polkit = { Result: { YES: 'yes', NOT_HANDLED: null }, addRule: (f) => { rule = f; },\n"
            "  spawn: (a) => { asked.push(a.join(' ')); if (!%s) throw new Error('exit 1'); } };\n" % ("true" if spawn_ok else "false")
            + rule_text()
            + "\nconsole.log(JSON.stringify({ r: rule({ id: 'org.freedesktop.packagekit.package-install' }, %s), asked }));" % json.dumps(subject))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(full)
    try:
        return json.loads(subprocess.run([NODE, f.name], capture_output=True, text=True, check=True).stdout)
    finally:
        os.unlink(f.name)


@unittest.skipUnless(Path(NODE).exists(), "node isn't installed")
class Rule(unittest.TestCase):
    ME = {"user": "jblol", "local": True, "active": True}

    def test_with_the_stick_in_the_popup_is_approved(self):
        out = run_rule(self.ME, True)
        self.assertEqual(out["r"], "yes")
        self.assertEqual(out["asked"], ["/usr/local/sbin/recall-stick-polkit"])

    def test_without_the_stick_the_normal_password_prompt_stays(self):
        self.assertIsNone(run_rule(self.ME, False)["r"])

    def test_nobody_else_and_no_remote_or_background_session_is_ever_approved(self):
        for subject in ({**self.ME, "user": "someone"}, {**self.ME, "local": False}, {**self.ME, "active": False}):
            out = run_rule(subject, True)
            self.assertIsNone(out["r"], subject)
            self.assertEqual(out["asked"], [], "the stick isn't even checked for them")


def heredoc(name, **env):
    """The text of one <<NAME heredoc in install.sh, expanded the way bash would when the installer runs (so the generated script is what's tested)."""
    src = INSTALL.read_text()
    body = re.search(r"<<'?%s'?\n(.*?)\n%s\n" % (name, name), src, re.S).group(1)
    quoted = re.search(r"<<'%s'" % name, src) is not None
    if quoted:
        return body + "\n"
    out = subprocess.run(["bash", "-c", "cat <<%s\n%s\n%s" % (name, body, name)], capture_output=True, text=True, env={"PATH": os.environ["PATH"], **env}, check=True)
    return out.stdout


class Installer(unittest.TestCase):
    def test_script_is_valid_and_has_the_switch_and_undo(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(INSTALL)]).returncode, 0)
        s = INSTALL.read_text()
        self.assertIn("--polkit", s)
        self.assertIn('"$RULE" "$PK_HELPER"', s, "--remove takes the rule and helper away again")
        self.assertIn("systemctl disable --now recall-stick-watch.service", s, "and stops the watcher")
        self.assertIn("echo polkit-1", s)

    def test_the_helper_polkit_runs_only_reads_a_root_owned_fresh_plain_flag(self):
        text = heredoc("HELPER")
        d = Path(tempfile.mkdtemp())
        flag = d / "ok"
        me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
        script = d / "helper.sh"
        script.write_text(text.replace("/run/recall-stick/ok", str(flag)).replace("= root ]", "= %s ]" % me))   # the test can't make root-owned files
        run = lambda: subprocess.run(["sh", str(script)]).returncode
        self.assertEqual(run(), 1, "no flag: no stick")
        flag.write_text("")
        self.assertEqual(run(), 0, "a fresh flag: the stick is in")
        os.utime(flag, (flag.stat().st_atime, flag.stat().st_mtime - 30))
        self.assertEqual(run(), 1, "a stale flag (the watcher stopped, or the stick came out): refused")
        flag.unlink()
        link = d / "real"
        link.write_text("")
        flag.symlink_to(link)
        self.assertEqual(run(), 1, "a symlink is never accepted")
        self.assertNotIn("$HOME", text)

    def test_the_watcher_only_raises_the_flag_for_the_right_key_file(self):
        d = Path(tempfile.mkdtemp())
        media = d / "media" / "stick" / "Vault"
        media.mkdir(parents=True)
        key = media / "recall-vault.keyx"
        key.write_bytes(b"secret key bytes")
        import hashlib
        (d / "hash").write_text(hashlib.sha256(b"secret key bytes").hexdigest() + "\n")
        text = heredoc("WATCH", U="jblol")
        text = (text.replace("/run/media/$U/", str(d / "media") + "/").replace("/etc/recall-stick.sha256", str(d / "hash")).replace("DIR=/run/recall-stick", "DIR=" + str(d / "run"))
                .replace('src=$(findmnt -T "$f" -no SOURCE 2>/dev/null)', "src=/dev/sdz1"))
        script = d / "watch.sh"
        script.write_text(text)
        flag = d / "run" / "ok"

        def watch(seconds=3):
            p = subprocess.Popen(["bash", str(script)])
            try:
                import time
                time.sleep(seconds)
                return flag.exists()
            finally:
                p.terminate()
                p.wait()
        self.assertTrue(watch(), "right key file: flag raised")
        self.assertFalse(flag.exists(), "stopping the watcher takes the flag down with it")
        key.write_bytes(b"a different file")
        self.assertFalse(watch(), "a stick with another key file never raises it")
        key.unlink()
        self.assertFalse(watch(), "no stick: no flag")

    def test_the_stick_in_the_other_computer_also_raises_the_flag_but_only_when_its_heartbeat_checks_out(self):
        d = Path(tempfile.mkdtemp())
        (d / "hash").write_text("0" * 64 + "\n")                                 # nothing local matches
        (d / "pub").write_text("pem")
        remote = d / "remote-check"
        text = heredoc("WATCH", U="jblol")
        text = (text.replace("/run/media/$U/", str(d / "nomedia") + "/").replace("/etc/recall-stick.sha256", str(d / "hash"))
                .replace("/etc/recall-stick.presence.pub", str(d / "pub")).replace("/usr/local/sbin/recall-stick-remote-check", str(remote))
                .replace("DIR=/run/recall-stick", "DIR=" + str(d / "run")).replace('runuser -u "$U" -- ', ""))
        script = d / "watch.sh"
        script.write_text(text)
        flag = d / "run" / "ok"
        import time

        def watch(seconds=3):
            p = subprocess.Popen(["bash", str(script)])
            try:
                time.sleep(seconds)
                return flag.exists()
            finally:
                p.terminate()
                p.wait()
        remote.write_text("#!/bin/sh\n[ \"$PAM_SERVICE\" = polkit-1 ] && [ \"$PAM_USER\" = jblol ] && exit 0\nexit 1\n")
        remote.chmod(0o755)
        self.assertTrue(watch(), "the other computer's stick is in: flag raised (and the helper is asked as the polkit service for this user)")
        remote.write_text("#!/bin/sh\nexit 1\n")
        self.assertFalse(watch(), "no valid heartbeat: no flag")
        remote.unlink()
        self.assertFalse(watch(), "this computer isn't paired for cross-device: only the local stick counts")


if __name__ == "__main__":
    unittest.main()
