"""USB & Security: the stick, what it protects, and your other computers (with a Lock button for each)."""
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

import jblol_pm as core
from .. import icons, model, sysinfo
from ..theme import C, MONO
from .items import drop_layout, drop_widget
from ..widgets import VScroll, Card, IconButton, Pill, Switch, button, label

try:
    import presence
except ImportError:
    presence = None

HERE = Path(core.__file__).resolve().parent
TONE_COL = {"good": C["good"], "warn": C["warn"], "bad": C["bad"], "": C["muted"], "accent": C["accent_hi"]}


def icon_tile(name, tone="", size=46, glyph=22):
    t = QLabel()
    t.setFixedSize(size, size)
    t.setAlignment(Qt.AlignCenter)
    col = TONE_COL.get(tone, C["muted"])
    bg = {"good": C["good_soft"], "warn": C["warn_soft"], "bad": C["bad_soft"], "accent": C["accent_soft"]}.get(tone, C["raised"])
    t.setStyleSheet(f"background:{bg}; border-radius:{size // 3}px;")
    t.setPixmap(icons.pix(name, glyph, col, 1.8))
    return t


class CommandBox(QFrame):
    """A shell command you copy and run yourself (the app never runs sudo)."""

    def __init__(self, win, cmd, hint=""):
        super().__init__()
        self.setObjectName("cmd")
        self.setStyleSheet(f"QFrame#cmd {{ background:{C['bg']}; border:1px solid {C['line']}; border-radius:10px; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 6, 8)
        v.setSpacing(2)
        if hint:
            v.addWidget(label(hint, "faint", True))
        r = QHBoxLayout()
        t = QLabel(cmd)
        t.setWordWrap(True)
        t.setMinimumWidth(1)
        t.setTextInteractionFlags(Qt.TextSelectableByMouse)
        t.setStyleSheet(f"font-family:{MONO}; font-size:12px; color:{C['soft']};")
        r.addWidget(t, 1)
        r.addWidget(IconButton("copy", "Copy command", 16, fn=lambda: win.copy_plain(cmd, "Command")))
        v.addLayout(r)


class ProtectCard(QFrame):
    def __init__(self, icon, title, text):
        super().__init__()
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(12)
        self.tile = icon_tile(icon)
        head.addWidget(self.tile, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        row = QHBoxLayout()
        row.addWidget(label(title, "h3"))
        row.addStretch(1)
        self.pill = Pill("")
        self.pill.setFixedHeight(22)
        row.addWidget(self.pill, 0, Qt.AlignVCenter)
        col.addLayout(row)
        self.text = label(text, "muted", True)
        col.addWidget(self.text)
        head.addLayout(col, 1)
        outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)

    def status(self, text, tone):
        self.pill.set(text, tone)
        TONE_COL.get(tone, C["muted"])
        bg = {"good": C["good_soft"], "warn": C["warn_soft"], "bad": C["bad_soft"], "accent": C["accent_soft"]}.get(tone, C["raised"])
        self.tile.setStyleSheet(f"background:{bg}; border-radius:15px;")

    def toggle_row(self, text, sw):
        r = QHBoxLayout()
        r.addWidget(label(text, "muted", True), 1)
        r.addWidget(sw)
        w = QWidget()
        w.setLayout(r)
        r.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(w)
        return w


def calm(win, tone):
    """With security warnings off, a problem is shown in a neutral colour instead of amber or red."""
    return tone if win.s.get("warn_security", True) or tone not in ("warn", "bad") else ""


class SecurityPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.info_key, self.info = None, {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 0)
        outer.setSpacing(6)
        outer.addWidget(label("USB & Security", "h1"))
        outer.addWidget(label("Your stick, what it protects on this computer, and your other computers.", "muted", True))
        sc = VScroll()
        host = QWidget()
        self.col = QVBoxLayout(host)
        self.col.setContentsMargins(0, 14, 8, 30)
        self.col.setSpacing(16)
        sc.setWidget(host)
        outer.addWidget(sc, 1)
        self.built = False

    # the page is rebuilt when something changes (cheap: a handful of cards)
    def refresh(self):
        if self.window() and getattr(self, "_busy", False):
            return
        self._busy = True
        try:
            self._build()
        finally:
            self._busy = False

    def _clear(self):
        while self.col.count():
            it = self.col.takeAt(0)
            if it.widget():
                drop_widget(it.widget())
            elif it.layout():
                drop_layout(it.layout())

    def _stick_info(self):
        kf = self.win.keyfile
        key = str(kf) if kf else None
        if key != self.info_key:
            self.info_key, self.info = key, ({"model": "SanDisk Ultra", "size": "115.5G", "mount": "/run/media/justin/0159-7E73", "device": "/dev/sda1", "fingerprint": "6ebde564d196"}
                                              if self.win.demo else core.stick_info(kf) if kf else {})
        return self.info

    def _build(self):
        w, s = self.win, self.win.s
        self._clear()
        kf = w.keyfile
        mine = w.demo or (core.stick_is_mine(kf) if kf else False)
        # ---- the stick
        hero = QFrame()
        hero.setObjectName("card")
        hl = QHBoxLayout(hero)
        hl.setContentsMargins(24, 22, 24, 22)
        hl.setSpacing(20)
        tone = calm(w, "good" if (kf and mine) else "warn" if kf else "good" if w.remote_from else "")
        hl.addWidget(icon_tile("usb", tone, 72, 34), 0, Qt.AlignTop)
        mid = QVBoxLayout()
        mid.setSpacing(6)
        title = QHBoxLayout()
        t = label("USB security key", "h2")
        title.addWidget(t)
        title.addSpacing(8)
        if kf and mine:
            title.addWidget(Pill("Connected · recognized", "good"))
        elif kf:
            title.addWidget(Pill("Connected · not your stick", calm(w, "bad")))
        elif w.remote_from:
            title.addWidget(Pill(f"In {w.remote_from}", "accent"))
        else:
            title.addWidget(Pill("Not plugged in"))
        title.addStretch(1)
        mid.addLayout(title)
        if kf:
            i = self._stick_info()
            grid = QGridLayout()
            grid.setHorizontalSpacing(34)
            grid.setVerticalSpacing(8)
            items = (("Model", i.get("model") or "USB drive"), ("Size", i.get("size") or "—"), ("Mounted at", i.get("mount") or "—"),
                     ("Device", i.get("device") or "—"), ("Key fingerprint", i.get("fingerprint") or "—"), ("Vault", "Unlocked" if w.vault else "Locked"))
            for n, (k, v) in enumerate(items):
                cell = QVBoxLayout()
                cell.setSpacing(1)
                cell.addWidget(label(k.upper(), "label"))
                val = QLabel(v)
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                val.setStyleSheet(f"font-size:13px; {'font-family:' + MONO + ';' if k in ('Key fingerprint', 'Device') else ''}")
                cell.addWidget(val)
                grid.addLayout(cell, n // 3, n % 3)
            gw = QWidget()
            gw.setLayout(grid)
            grid.setContentsMargins(0, 6, 0, 0)
            mid.addWidget(gw)
        else:
            mid.addWidget(label(f"Plug in the stick that holds {core.KEYFILE_NAME} in its Vault folder, and your vault opens." if not w.remote_from else
                                f"Your stick is in {w.remote_from}. This computer follows it for sudo, and for passwords if you allowed that below.", "muted", True))
        hl.addLayout(mid, 1)
        btns = QVBoxLayout()
        btns.setSpacing(8)
        lk = button("Lock vault", "", w.lock_now, "lock")
        lk.setEnabled(bool(w.vault))
        btns.addWidget(lk)
        btns.addWidget(button("Lock this computer", "", w.lock_this_computer, "laptop"))
        btns.addStretch(1)
        hl.addLayout(btns)
        self.col.addWidget(hero)

        # ---- protections
        self.col.addWidget(label("PROTECTION ON THIS COMPUTER", "section"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        cards = []

        code, text = w.sudo_code, w.sudo_msg
        pcode, ptext = sysinfo.polkit_state()
        c = ProtectCard("shield_check", "sudo and permission pop-ups", "While the stick is in, sudo and the “Authentication required” windows approve themselves.")
        both = code == "on" and pcode in ("on", "idle")
        c.status("On" if both else "Partly" if (code == "on" or pcode != "off") else "Off", calm(w, "good" if both else "warn" if (code == "on" or pcode != "off") else ""))
        c.body.addWidget(label(f"sudo: {text}", "faint", True))
        c.body.addWidget(label(f"Pop-ups: {ptext}", "faint", True))
        if not both and s.get("warn_security", True):
            c.body.addWidget(CommandBox(w, f"sudo bash {HERE}/sudo-stick/install.sh --login --cross-device --polkit", "Run this once in a terminal, with the stick plugged in:"))
        cards.append(c)

        c = ProtectCard("lock", "Lock when the stick is pulled", "Pull the stick and this computer's screen locks at once, and the vault locks. Your other computers always obey a lock order from your stick.")
        sw = Switch(bool(s.get("lock_screen_on_pull")))
        sw.toggled.connect(lambda on: w.set_opt("lock_screen_on_pull", on))
        c.status("On" if s.get("lock_screen_on_pull") else "Off", "good" if s.get("lock_screen_on_pull") else "")
        c.toggle_row("Lock this computer's screen", sw)
        sw2 = Switch(bool(s.get("lock_peers_on_pull")))
        sw2.setEnabled(bool(s.get("cross_device")))
        sw2.toggled.connect(lambda on: w.set_opt("lock_peers_on_pull", on))
        c.toggle_row("Also lock my other computers" + ("" if s.get("cross_device") else " (turn on cross-device first)"), sw2)
        cards.append(c)

        c = ProtectCard("laptop", "Your stick across computers", "While it is in one computer, your others can use sudo and the lock screen too.")
        sw = Switch(bool(s.get("cross_device")))
        sw.toggled.connect(w.toggle_cross)
        c.status("On" if s.get("cross_device") else "Off", "good" if s.get("cross_device") else "")
        c.toggle_row("Use my stick across my computers", sw)
        sw2 = Switch(bool(s.get("cross_device_vault")))
        sw2.setEnabled(bool(s.get("cross_device")))
        sw2.toggled.connect(w.toggle_cross_vault)
        c.toggle_row("Open my passwords here while the stick is elsewhere", sw2)
        if presence is not None and s.get("cross_device"):
            st = presence.state()
            if st.get("error"):
                c.body.addWidget(label(st["error"], "faint", True))
        cards.append(c)

        wait = sysinfo.wait_seconds()
        c = ProtectCard("clock", "Forgot to plug it in?", "Run sudo (or open a permission pop-up) without the stick, then plug it in: it approves as soon as it's in.")
        c.status(f"Waits {wait} s" if wait else "Off", "good" if wait else "")
        c.body.addWidget(label(f"sudo and the pop-ups wait {wait} seconds for the stick before asking for your password." if wait else
                               "Right now sudo asks for your password straight away when the stick is out.", "faint", True))
        if not wait or not sysinfo.polkit_wait_ready():
            c.body.addWidget(CommandBox(w, f"sudo bash {HERE}/sudo-stick/install.sh --login --cross-device --polkit --wait", "Run once in a terminal, with the stick plugged in (--wait=40 for 40 seconds):"))
        else:
            c.body.addWidget(CommandBox(w, f"sudo bash {HERE}/sudo-stick/install.sh --no-wait", "To turn it off:"))
        cards.append(c)

        c = ProtectCard("unlock", "Unlock the screen with the stick", "Plug the stick in while the screen is locked and it unlocks, instead of typing your password.")
        sw = Switch(bool(s.get("unlock_screen_with_stick")))
        sw.toggled.connect(lambda on: w.set_opt("unlock_screen_with_stick", on))
        c.status("On" if s.get("unlock_screen_with_stick") else "Off", "good" if s.get("unlock_screen_with_stick") else "")
        c.toggle_row("Unlock my screen when I plug the stick in", sw)
        cards.append(c)

        bcode, btext = sysinfo.boot_state()
        c = ProtectCard("key", "Disk unlock at start-up", "Boot with the stick in and the encrypted disk opens without typing its passphrase. Your passphrase always still works.")
        c.status({"on": "On", "off": "Off", "na": "Not available"}[bcode], {"on": "good", "off": "", "na": ""}[bcode])
        c.body.addWidget(label(btext, "faint", True))
        if bcode == "off" and s.get("warn_security", True):
            c.body.addWidget(CommandBox(w, f"sudo bash {HERE}/sudo-stick/boot-unlock.sh", "Run once in a terminal, with the stick plugged in:"))
        elif bcode == "on":
            c.body.addWidget(CommandBox(w, f"sudo bash {HERE}/sudo-stick/boot-unlock.sh --remove", "To turn it off again:"))
        cards.append(c)

        for n, c in enumerate(cards):
            grid.addWidget(c, n // 2, n % 2)
        gw = QWidget()
        gw.setLayout(grid)
        grid.setContentsMargins(0, 0, 0, 0)
        self.col.addWidget(gw)

        # ---- devices
        head = QHBoxLayout()
        head.addWidget(label("YOUR COMPUTERS", "section"))
        head.addStretch(1)
        self.col.addLayout(head)
        for d in w.devices():
            self.col.addWidget(self._device(d, kf is not None and mine))
        self.col.addWidget(self._add_device())
        head = QHBoxLayout()
        head.addWidget(label("YOUR PHONES", "section"))
        head.addStretch(1)
        self.col.addLayout(head)
        phones = w.link_phones()
        for p in phones:
            self.col.addWidget(self._phone(p))
        if not phones:
            self.col.addWidget(self._no_phone())
        self.col.addStretch(1)

    def _device(self, d, can_command):
        w, s = self.win, self.win.s
        f = QFrame()
        f.setObjectName("card")
        h = QHBoxLayout(f)
        h.setContentsMargins(20, 16, 20, 16)
        h.setSpacing(16)
        seen = d.get("seen")
        age = time.time() - seen["at"] if seen else None
        online = d["me"] or (age is not None and age < 100)
        h.addWidget(icon_tile(sysinfo.guess_kind(d["name"]), "good" if online else "", 48, 24))
        col = QVBoxLayout()
        col.setSpacing(4)
        r = QHBoxLayout()
        r.setSpacing(8)
        r.addWidget(label(d["name"], "h3"))
        if d["me"]:
            r.addWidget(Pill("This computer", "accent"))
        r.addWidget(Pill("Online", "good") if online else Pill("Seen " + model.ago(seen["at"]) if seen else "No report yet"))
        if d["me"] and w.keyfile:
            r.addWidget(Pill("Has the stick", "good"))
        elif seen and seen.get("stick") and not d["me"]:
            r.addWidget(Pill("Has the stick", "good"))
        if seen and seen.get("locked"):
            r.addWidget(Pill("Screen locked", "warn"))
        if d.get("vault"):
            r.addWidget(Pill("Can open passwords"))
        r.addStretch(1)
        col.addLayout(r)
        col.addWidget(label((sysinfo.os_name() if d["me"] else (seen or {}).get("os") or "Waiting for this computer to report in") + (" · vault " + ("unlocked" if w.vault else "locked") if d["me"] else ""), "muted"))
        h.addLayout(col, 1)
        if d["me"]:
            h.addWidget(button("Lock this computer", "", w.lock_this_computer, "lock"))
        else:
            b = button("Lock device", "", lambda n=d["name"]: w.lock_device(n), "lock")
            ok = bool(can_command and s.get("cross_device"))
            b.setEnabled(ok)
            if not ok:
                b.setToolTip("Plug your stick into this computer (and turn on cross-device) to lock another computer: only the stick can give the order.")
            h.addWidget(b)
            rm = IconButton("trash", "Remove from the stick's list", 18, fn=lambda n=d["name"]: w.remove_device(n))
            rm.setEnabled(bool(can_command))
            h.addWidget(rm)
        return f

    def _phone(self, p):
        w = self.win
        f = QFrame()
        f.setObjectName("card")
        h = QHBoxLayout(f)
        h.setContentsMargins(20, 16, 20, 16)
        h.setSpacing(16)
        h.addWidget(icon_tile("phone", "good" if p["online"] else "", 48, 24))
        col = QVBoxLayout()
        col.setSpacing(4)
        r = QHBoxLayout()
        r.setSpacing(8)
        r.addWidget(label(p["name"], "h3"))
        r.addWidget(Pill("Online", "good") if p["online"] else Pill("Seen " + model.ago(time.time() - p["age"])))
        r.addWidget(Pill("Passwords locked") if p.get("locked") else Pill("Passwords open", "warn"))
        r.addStretch(1)
        col.addLayout(r)
        col.addWidget(label(p.get("os") or "Recall app on your phone", "muted"))
        h.addLayout(col, 1)
        b = button("Lock passwords page", "", lambda i=p["id"], n=p["name"]: w.lock_phone(i, n), "lock")
        b.setEnabled(bool(p["online"] and not p.get("locked")))
        if not p["online"]:
            b.setToolTip("The phone isn't reachable right now. Its passwords lock themselves when you leave the app anyway.")
        h.addWidget(b)
        return f

    def _no_phone(self):
        w = self.win
        on = bool(w.s["phone_enabled"] and w.s["phone_key"])
        c = Card("No phone yet" if on else "Turn on the phone copy", "Open the Recall app on your phone, then More, Passwords, and it appears here within a minute." if on
                 else "Settings, “Keep an encrypted copy for the phone”. Your phone then shows up here and can lock, and be locked by, your computers.", flat=True, pad=18)
        if w.link_error and on:
            c.body.addWidget(label(w.link_error, "faint", True))
        return c

    def _add_device(self):
        c = Card("Add another computer", "Set up the same way, once per computer. It takes about two minutes.", flat=True, pad=18)
        steps = (("1", "Copy this folder to it and run:", f"bash {HERE.parent.name}/install.sh" if False else "bash install.sh"),
                 ("2", "Plug the stick in, open Password Manager, go to USB & Security and turn on “Use my stick across my computers”.", ""),
                 ("3", "Give it sudo, lock-screen and pop-up approval (run in a terminal, stick plugged in):", f"sudo bash ~/.local/share/jblol-password-manager/sudo-stick/install.sh --login --cross-device --polkit"),
                 ("4", "Optional: unlock its disk at start-up with the stick:", f"sudo bash ~/.local/share/jblol-password-manager/sudo-stick/boot-unlock.sh"))
        for n, text, cmd in steps:
            row = QHBoxLayout()
            row.setSpacing(12)
            b = QLabel(n)
            b.setFixedSize(24, 24)
            b.setAlignment(Qt.AlignCenter)
            b.setStyleSheet(f"background:{C['accent_soft']}; color:{C['accent_hi']}; border-radius:12px; font-weight:700; font-size:12px;")
            row.addWidget(b, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(6)
            col.addWidget(label(text, "muted", True))
            if cmd:
                col.addWidget(CommandBox(self.win, cmd))
            row.addLayout(col, 1)
            c.body.addLayout(row)
        return c
