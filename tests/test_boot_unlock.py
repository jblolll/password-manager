"""sudo-stick/boot-unlock.sh against a scratch folder and pretend disk tools (no root, no real disk is touched)."""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "sudo-stick" / "boot-unlock.sh"
LUKS, STICK = "2a8c4882-f18a-482d-a6ee-14a23347a437", "0159-7E73"

SHIMS = {
    "blkid": '''#!/bin/bash
case "$*" in
  "-U $LUKS") echo /dev/nvme0n1p3 ;;
  *"-s UUID"*) echo "$STICK" ;;
  *"-s TYPE"*) echo "${STICK_FS:-vfat}" ;;
  *) exit 2 ;;
esac''',
    "findmnt": '#!/bin/bash\necho /dev/sda1',
    "cryptsetup": '''#!/bin/bash
echo "$@" >> "$SCRATCH/cryptsetup.log"
case "$1" in
  luksAddKey) cp "$3" "$SCRATCH/added.key" ;;
  luksRemoveKey) rm -f "$SCRATCH/added.key" ;;
  open) [ -f "$SCRATCH/added.key" ] && [ "$(cat "$SCRATCH/added.key")" = "$(cat "${@: -2:1}")" ] ;;
esac''',
}


class BootUnlock(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        (self.root / "etc").mkdir(parents=True)
        (self.root / "etc" / "crypttab").write_text(f"dm_crypt-0 UUID={LUKS} none luks\n")
        self.media = self.tmp / "media"
        (self.media / "stick" / "Vault").mkdir(parents=True)
        (self.media / "stick" / "Vault" / "recall-vault.keyx").write_text("key material")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in SHIMS.items():
            f = self.bin / name
            f.write_text(body)
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", RECALL_ROOT=str(self.root), RECALL_USER="tester", RECALL_MEDIA=str(self.media),
                        LUKS=LUKS, STICK=STICK, SCRATCH=str(self.tmp))
        self.conf = self.root / "etc" / "dracut.conf.d" / "90-recall-stick.conf"
        self.key = self.media / "stick" / "Vault" / "boot" / "recall-boot.key"

    def run_script(self, *args, **env):
        return subprocess.run(["bash", str(SCRIPT), *args], env={**self.env, **env}, capture_output=True, text=True)

    def test_install_writes_key_settings_and_state(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        k = self.key.read_text()
        self.assertEqual(len(k), 64)                                    # 64 hex characters, no newline
        self.assertTrue(all(c in "0123456789abcdef" for c in k))
        self.assertEqual((self.tmp / "added.key").read_text(), k)       # the same key was added to the disk
        conf = self.conf.read_text()
        self.assertIn(f"rd.luks.key={LUKS}=/Vault/boot/recall-boot.key:UUID={STICK}", conf)
        self.assertIn("keyfile-timeout=20s", conf)
        self.assertIn("usb_storage", conf)
        self.assertIn("vfat", conf)
        self.assertIn("On:", self.run_script("--status").stdout)
        self.assertIn("passphrase", r.stdout)

    def test_second_run_keeps_the_same_key(self):
        self.run_script()
        first = self.key.read_text()
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.key.read_text(), first)
        self.assertEqual((self.tmp / "cryptsetup.log").read_text().count("luksAddKey"), 1)

    def test_remove_undoes_it(self):
        self.run_script()
        r = self.run_script("--remove")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.conf.exists())
        self.assertFalse(self.key.exists())
        self.assertFalse((self.tmp / "added.key").exists())             # the disk no longer accepts it
        self.assertEqual(self.run_script("--status").stdout.strip(), "Off.")

    def test_refuses_a_stick_file_system_it_cannot_read_at_start_up(self):
        r = self.run_script(STICK_FS="ntfs")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("file system", r.stderr)
        self.assertFalse(self.conf.exists())

    def test_refuses_two_encrypted_disks(self):
        (self.root / "etc" / "crypttab").write_text(f"a UUID={LUKS} none luks\nb UUID=1111 none luks\n")
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("more than one", r.stderr)

    def test_needs_the_right_stick(self):
        (self.root / "etc" / "recall-stick.sha256").write_text("0" * 64 + "\n")
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("isn't your stick", r.stderr)
        self.assertFalse(self.key.exists())

    def test_without_the_stick_nothing_happens(self):
        (self.media / "stick" / "Vault" / "recall-vault.keyx").unlink()
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("plug the stick in", r.stderr)


if __name__ == "__main__":
    unittest.main()
