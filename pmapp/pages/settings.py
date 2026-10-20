"""Settings: searchable, grouped. Every row is a title, a sentence about it, and one control."""
import subprocess
import shutil
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QSpinBox, QVBoxLayout, QWidget)
from PySide6.QtGui import QAction

import jblol_pm as core
from .. import icons
from ..theme import C, MONO
from ..widgets import VScroll, Card, IconButton, SettingRow, Switch, button, hline, label


class SettingsPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.rows = []                                   # (SettingRow, card, separator before it)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 0)
        outer.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(label("Settings", "h1"), 1)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search settings")
        self.search.setFixedWidth(280)
        self.search.setClearButtonEnabled(True)
        self.search.addAction(QAction(icons.icon("search", C["muted"], 17), "", self.search), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.filter)
        top.addWidget(self.search)
        outer.addLayout(top)
        sc = VScroll()
        host = QWidget()
        self.col = QVBoxLayout(host)
        self.col.setContentsMargins(0, 14, 8, 36)
        self.col.setSpacing(16)
        sc.setWidget(host)
        outer.addWidget(sc, 1)
        self.cards = []
        self.build()

    # -- building blocks
    def card(self, title, subtitle=""):
        c = Card(title, subtitle, pad=22, size="h2")
        c.body.setSpacing(0)
        c.rows = []
        self.col.addWidget(c)
        self.cards.append(c)
        return c

    def add(self, card, title, text="", control=None, tag=""):
        r = SettingRow(title, text, control, tag)
        sep = hline() if card.rows else None
        if sep:
            card.body.addWidget(sep)
        card.body.addWidget(r)
        card.rows.append((r, sep))
        return r

    def sw(self, key, extra=None):
        s = Switch(bool(self.win.s.get(key)))
        s.key = key
        s.toggled.connect(lambda on: (self.win.set_opt(key, on), extra(on) if extra else None))
        return s

    def rebuild_switch(self, key, on):
        for c in self.cards:
            for r, _ in c.rows:
                if isinstance(r.control, Switch) and getattr(r.control, "key", None) == key:
                    r.control.setChecked(on)

    def spin(self, key, lo, hi, suffix, special="Never"):
        b = QSpinBox()
        b.setRange(lo, hi)
        b.setSuffix(suffix)
        b.setSpecialValueText(special)
        b.setValue(int(self.win.s.get(key, 0)))
        b.valueChanged.connect(lambda v: self.win.set_opt(key, v))
        return b

    def btns(self, *specs):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for text, kind, fn, icon in specs:
            h.addWidget(button(text, kind, fn, icon))
        return w

    def build(self):
        w = self.win
        s = w.s
        c = self.card("General")
        self.add(c, "Open when the stick is plugged in", "Starts this app by itself as soon as your stick goes in.", self.sw("launch_on_plug"), "start launch usb")
        self.add(c, "Clear a copied password after", "How long a copied password stays on the clipboard before it is wiped.", self.spin("clip_clear_s", 0, 300, "  seconds"), "clipboard copy")
        self.add(c, "Hide a shown password after", "A password you reveal covers itself again after this long.", self.spin("reveal_timeout_s", 0, 300, "  seconds"), "reveal show hide")
        self.add(c, "Lock after being idle for", "Locks the vault when you haven't touched this computer.", self.spin("idle_lock_min", 0, 240, "  minutes"), "idle timeout")

        c = self.card("Warnings", "Turn these off if you'd rather not be nagged.")
        self.add(c, "Password warnings", "Warn about weak, reused and old passwords: the warning marks in the list, the banner on a login, and the Security Check count.", self.sw("warn_passwords"), "security check weak reused old warnings health")
        self.add(c, "Security warnings", "Warn about protections that aren't set up (sudo pop-ups, disk unlock, lock orders) on the USB & Security page.", self.sw("warn_security"), "security usb warnings protection")

        c = self.card("Locking", "When the vault and this computer close themselves.")
        self.add(c, "Lock the vault when the stick is removed", "Wipes your passwords from memory the moment the stick comes out.", self.sw("lock_on_unplug"), "unplug remove")
        self.add(c, "Also lock the screen when the stick is pulled", "Locks this computer too. Can lock your other computers as well (USB & Security).", self.sw("lock_screen_on_pull"), "pull screen lock")
        self.add(c, "Lock with the screen or sleep", "The vault locks when the screen locks or the computer sleeps, and opens again when the screen unlocks with the stick in.", self.sw("lock_on_screen"), "sleep screen")
        self.add(c, "Unlock the screen with the stick", "Plug the stick in at the lock screen instead of typing your password.", self.sw("unlock_screen_with_stick"), "lock screen unlock")

        c = self.card("Vault file")
        self.vault_path = label(s["vault"], "mono", True, True)
        self.vault_path.setStyleSheet(f"font-family:{MONO}; font-size:12.5px; color:{C['soft']};")
        self.add(c, "Your vault", "A standard KeePass file, locked with the key file on your stick. It also opens in KeePassXC.", None, "file path location")
        c.body.addWidget(self.vault_path)
        self.add(c, "Manage", "", self.btns(("Choose another…", "", w.choose_vault, "folder"), ("Back up…", "", w.backup, "download"),
                                           ("Show in folder", "ghost", lambda: webbrowser.open(Path(s["vault"]).parent.as_uri()), "external")), "backup choose folder")

        c = self.card("Add passwords", "Bring in what you already have.")
        self.add(c, "Import a password file", "A .csv export from Brave, Chrome, Firefox, Apple Passwords, Bitwarden or another manager. Duplicates are skipped.", button("Choose file…", "", w.choose_import, "upload"), "import csv chrome bitwarden")
        self.imp_group = QComboBox()
        self.imp_group.setEditable(True)
        self.imp_group.setMinimumWidth(180)
        self.add(c, "Put imported logins in", "", self.imp_group, "import folder group")
        self.imp_msg = label("", "muted", True)
        self.imp_bar = QProgressBar()
        self.imp_bar.setVisible(False)
        c.body.addWidget(self.imp_msg)
        c.body.addWidget(self.imp_bar)
        self.add(c, "Wi-Fi networks on this computer", "Copies the networks this computer has joined, with their passwords, into your vault so you can share them by QR code.", button("Add them…", "", w.import_wifi, "wifi"), "wifi network")
        self.add(c, "This computer's SSH key", "Saves ~/.ssh/id_ed25519 under Servers & SSH.", button("Add key", "", w.add_this_computer_ssh, "key"), "ssh server")
        self.add(c, "Apps", "Website logins for services that also have an app are also kept under Apps, linked, so changing one changes both.", button("Copy logins into Apps", "", w.add_app_twins, "copy"), "apps twins")

        c = self.card("Sync", "Every computer keeps the same vault.")
        self.sync_label = label("", "muted", True)
        self.add(c, "Sync now", "The server keeps one encrypted copy it cannot read. Changes merge a few seconds after you save, when you plug in, and every 5 minutes.", button("Sync now", "", lambda: w.start_sync(manual=True), "refresh"), "server merge")
        c.body.addWidget(self.sync_label)

        c = self.card("Browser extension", "Fill logins in your browser.")
        self.add(c, "Let the browser ask this app for logins", "Works only while the stick is in. Only your browser extension can use it, only on this computer.", self.sw("bridge_enabled", w.toggle_bridge), "chrome firefox brave fill")
        self.bridge_label = label("", "faint", True)
        c.body.addWidget(self.bridge_label)
        self.pair_code = QLineEdit(s["bridge_token"])
        self.pair_code.setReadOnly(True)
        self.pair_code.setProperty("role", "mono")
        self.pair_code.setEchoMode(QLineEdit.Password)
        pr = QWidget()
        ph = QHBoxLayout(pr)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.addWidget(self.pair_code, 1)
        ph.addWidget(IconButton("eye", "Show", fn=lambda: self.pair_code.setEchoMode(QLineEdit.Normal if self.pair_code.echoMode() == QLineEdit.Password else QLineEdit.Password)))
        ph.addWidget(IconButton("copy", "Copy", fn=lambda: w.copy_plain(s["bridge_token"], "Pairing code")))
        self.add(c, "Pairing code", "The extension pairs itself from the “PasswordManager-Extension” folder. Paste this by hand only in Firefox.", pr, "token pairing code")
        self.add(c, "Extension", "", self.btns(("Show the folder", "", lambda: webbrowser.open((core.HOME / "PasswordManager-Extension").as_uri()), "folder"), ("New pairing code", "ghost", w.new_pairing, "refresh")), "folder new")

        c = self.card("Phone", "A read-only copy for your iPhone.")
        self.add(c, "Keep an encrypted copy for the phone", "View your logins and one-time codes in the Recall app. The key never leaves your computers and phone.", self.sw("phone_enabled", w.toggle_phone), "iphone ios")
        self.phone_label = label("", "faint", True)
        c.body.addWidget(self.phone_label)
        self.phone_key_box = QLineEdit(s["phone_key"])
        self.phone_key_box.setReadOnly(True)
        self.phone_key_box.setProperty("role", "mono")
        self.phone_key_box.setEchoMode(QLineEdit.Password)
        kr = QWidget()
        kh = QHBoxLayout(kr)
        kh.setContentsMargins(0, 0, 0, 0)
        kh.addWidget(self.phone_key_box, 1)
        kh.addWidget(IconButton("eye", "Show", fn=lambda: self.phone_key_box.setEchoMode(QLineEdit.Normal if self.phone_key_box.echoMode() == QLineEdit.Password else QLineEdit.Password)))
        kh.addWidget(IconButton("copy", "Copy", fn=lambda: w.copy_plain(s["phone_key"], "Phone key")))
        self.add(c, "Phone key", "In the Recall app open More, then Passwords, and type this once.", kr, "key iphone")
        self.add(c, "Phone copy", "", self.btns(("Send now", "", lambda: w.send_phone(manual=True), "upload"), ("New key", "ghost", w.new_phone_key_ui, "refresh"), ("Remove copy", "danger", w.remove_phone, "trash")), "send remove")

        c = self.card("Desktop apps")
        self.add(c, "Fill logins into desktop apps", "When an app opens and you have a login named after it, Password Manager offers to type it in. Needs the window helper GNOME extension.", self.sw("app_fill"), "fill apps discord steam")
        self.apps_label = label("", "faint", True)
        c.body.addWidget(self.apps_label)
        self.add(c, "Remembered choices", "", button("Forget my app choices", "", w.forget_apps), "forget")

        c = self.card("About")
        engine = (subprocess.run([shutil.which("keepassxc.cli") or shutil.which("keepassxc-cli") or "true", "--version"], capture_output=True, text=True).stdout.strip() or "not found")
        self.add(c, f"Password Manager {core.VERSION}", f"{core.BRAND}. Storage and encryption by KeePassXC {engine}, the standard KeePass format.", None, "version about keepassxc")
        self.nomatch = label("No settings match your search.", "muted")
        self.nomatch.setAlignment(Qt.AlignCenter)
        self.nomatch.hide()
        self.col.addWidget(self.nomatch)
        self.col.addStretch(1)

    def filter(self, text):
        q = text.strip().lower()
        any_ = False
        for c in self.cards:
            vis = 0
            first = True
            for r, sep in c.rows:
                show = not q or q in r.keywords or q in (c.findChildren(QLabel)[0].text().lower() if c.findChildren(QLabel) else "")
                r.setVisible(show)
                if sep:
                    sep.setVisible(show and not first)
                if show:
                    first = False
                    vis += 1
            c.setVisible(vis > 0 or not q)
            any_ = any_ or vis > 0
        self.nomatch.setVisible(bool(q) and not any_)

    def refresh(self):
        w = self.win
        helper = core.focused_app() is not None
        saved = ", ".join(f"{k} ({v.get('mode')})" for k, v in w.s["apps"].items()) or "none yet"
        self.apps_label.setText(f"Window helper: {'running' if helper else 'not running yet (log out and back in)'}.  Remembered apps: {saved}.")
        bool(w.vault)
        if self.imp_group.count() != len(w.groups or core.STARTER_GROUPS):
            self.imp_group.clear()
            self.imp_group.addItems([g for g in (w.groups or core.STARTER_GROUPS)])
            self.imp_group.setCurrentText("Websites")
        self.bridge_label.setText(w.bridge_text())
        self.phone_label.setText(w.phone_status or ("On." if w.s["phone_enabled"] else "Off."))
        self.sync_label.setText(w.last_sync or "Not synced yet in this session.")
        self.vault_path.setText(w.s["vault"])
