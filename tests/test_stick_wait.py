"""Forgot the stick? sudo (and the pop-ups) wait a few seconds for it to be plugged in. The root PAM helper is run here against a scratch folder
standing in for /etc and /run/media, and the installer's PAM lines are checked on a copy of a PAM file.

    python3 -m unittest tests.test_stick_wait -v
"""
import hashlib
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

SUDO_STICK = Path(__file__).resolve().parent.parent / "sudo-stick"
U = "tester"


class Helper(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.etc, self.media = self.tmp / "etc", self.tmp / "media"
        self.etc.mkdir()
        (self.media / "stick" / "Vault").mkdir(parents=True)
        self.keyfile = self.media / "stick" / "Vault" / "recall-vault.keyx"
        self.key_text = "the key"
        (self.etc / "recall-stick.sha256").write_text(hashlib.sha256(self.key_text.encode()).hexdigest() + "\n")
        (self.etc / "recall-stick-services").write_text("sudo\npolkit-1\n")
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        shim = bin_ / "findmnt"
        shim.write_text("#!/bin/bash\necho /dev/sda1\n")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        src = (SUDO_STICK / "recall-stick-check.sh").read_text().replace("__USER__", U)
        src = src.replace("/etc/recall-stick", str(self.etc / "recall-stick")).replace("/run/media/" + U, str(self.media))
        src = src.replace("REMOTE=/usr/local/sbin/recall-stick-remote-check", f"REMOTE={self.tmp}/none")
        self.script = self.tmp / "check.sh"
        self.script.write_text(src)
        self.env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", PAM_USER=U, PAM_SERVICE="sudo", PAM_TTY="/dev/pts/9")

    def plug(self):
        self.keyfile.write_text(self.key_text)

    def run_check(self, *args, **env):
        t = time.time()
        r = subprocess.run(["bash", str(self.script), *args], env={**self.env, **env}, capture_output=True)
        return r.returncode, time.time() - t

    def wait_seconds(self, n):
        (self.etc / "recall-stick-wait").write_text(f"{n}\n")

    def test_stick_in_is_accepted_at_once(self):
        self.plug()
        self.assertEqual(self.run_check()[0], 0)
        self.assertEqual(self.run_check("--wait")[0], 0)

    def test_no_stick_and_no_wait_fails_straight_away(self):
        rc, took = self.run_check()
        self.assertEqual(rc, 1)
        self.assertLess(took, 1.5)
        self.wait_seconds(0)
        rc, took = self.run_check("--wait")
        self.assertEqual(rc, 1)
        self.assertLess(took, 1.5)

    def test_plugging_in_while_it_waits_approves(self):
        self.wait_seconds(8)
        threading.Timer(1.2, self.plug).start()
        rc, took = self.run_check("--wait")
        self.assertEqual(rc, 0)
        self.assertTrue(1.0 < took < 4.0, took)

    def test_it_gives_up_after_the_time_and_then_asks_for_the_password(self):
        self.wait_seconds(2)
        rc, took = self.run_check("--wait")
        self.assertEqual(rc, 1)
        self.assertTrue(1.8 < took < 4.0, took)

    def test_scripts_without_a_terminal_never_wait(self):
        self.wait_seconds(10)
        env = {k: v for k, v in self.env.items() if k != "PAM_TTY"}
        t = time.time()
        r = subprocess.run(["bash", str(self.script), "--wait"], env=env, capture_output=True)
        self.assertEqual(r.returncode, 1)
        self.assertLess(time.time() - t, 1.5)

    def test_popups_wait_even_without_a_terminal(self):
        self.wait_seconds(8)
        threading.Timer(1.0, self.plug).start()
        env = {k: v for k, v in self.env.items() if k != "PAM_TTY"}
        env["PAM_SERVICE"] = "polkit-1"
        r = subprocess.run(["bash", str(self.script), "--wait"], env=env, capture_output=True)
        self.assertEqual(r.returncode, 0)

    def test_only_the_listed_services_and_the_right_user_and_key(self):
        self.plug()
        self.assertEqual(self.run_check(PAM_SERVICE="su")[0], 1)
        self.assertEqual(self.run_check(PAM_USER="someone")[0], 1)
        self.keyfile.write_text("a different key")
        self.assertEqual(self.run_check()[0], 1, "a stick with another key file is not accepted")
        self.wait_seconds(2)
        self.assertEqual(self.run_check("--wait", PAM_SERVICE="su")[0], 1)

    def test_an_absurd_wait_is_ignored(self):
        self.wait_seconds(5000)
        rc, took = self.run_check("--wait")
        self.assertEqual(rc, 1)
        self.assertLess(took, 1.5)


class PamLines(unittest.TestCase):
    """The installer's pam_block function, on a copy of the sudo PAM file."""

    def block(self, text, wait):
        src = (SUDO_STICK / "install.sh").read_text()
        fn = re.search(r"pam_block\(\) \{.*?\n\}\n", src, re.S).group(0)
        d = Path(tempfile.mkdtemp())
        f = d / "sudo"
        f.write_text(text)
        subprocess.run(["bash", "-c", f'WAIT_MSG=/etc/recall-stick-wait.msg\n{fn}\npam_block "{f}" {wait}'], check=True)
        return f.read_text()

    BASE = "#%PAM-1.0\n@include common-auth\nsession required pam_limits.so\n"

    def test_without_waiting_there_is_one_line(self):
        out = self.block(self.BASE, 0)
        self.assertEqual(out.splitlines()[:3], ["#%PAM-1.0", "auth sufficient pam_exec.so quiet /usr/local/sbin/recall-stick-check", "@include common-auth"])

    def test_with_waiting_the_message_comes_between_the_two_checks(self):
        out = self.block(self.BASE, 1).splitlines()
        self.assertEqual(out[1:4], ["auth sufficient pam_exec.so quiet /usr/local/sbin/recall-stick-check",
                                    "auth optional pam_echo.so file=/etc/recall-stick-wait.msg",
                                    "auth sufficient pam_exec.so quiet /usr/local/sbin/recall-stick-check --wait"])
        self.assertEqual(out[4], "@include common-auth", "the normal password prompt still comes last")

    def test_running_it_again_does_not_pile_up_lines_and_can_switch_waiting_off(self):
        once = self.block(self.BASE, 1)
        twice = self.block(once, 1)
        self.assertEqual(once, twice)
        off = self.block(once, 0)
        self.assertNotIn("pam_echo", off)
        self.assertEqual(off.count("recall-stick-check"), 1)


if __name__ == "__main__":
    unittest.main()
