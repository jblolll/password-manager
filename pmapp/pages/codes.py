"""One-time codes: every login that has a 2-step code, in one list, with the live code and a countdown. Click a row to copy its code."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

import jblol_pm as core
from .. import icons, model
from ..theme import C, MONO
from ..widgets import EmptyState, Switch, TotpRing, VScroll, button, label
from .items import Dialog, drop_widget, form_row, mono_edit


class CodeRow(QPushButton):
    def __init__(self, win, e, logos):
        super().__init__()
        self.win, self.e = win, e
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(76)
        self.setToolTip("Click to copy the code")
        self.setStyleSheet(f"QPushButton {{ background:{C['panel']}; border:1px solid {C['line']}; border-radius:14px; text-align:left; }}"
                           f"QPushButton:hover {{ background:{C['raised']}; border-color:{C['line2']}; }}")
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 10, 16, 10)
        h.setSpacing(14)
        from ..widgets import Avatar
        self.avatar = Avatar(e["title"], "login", 44, logo=logos.pixmap_for(e) if logos else None)
        h.addWidget(self.avatar)
        col = QVBoxLayout()
        col.setSpacing(1)
        t = QLabel(e["title"])
        t.setStyleSheet("font-weight:650; font-size:14px; background:transparent;")
        col.addWidget(t)
        who = QLabel(e.get("username") or core.host_of(e.get("url", "")) or "")
        who.setStyleSheet(f"color:{C['muted']}; font-size:12px; background:transparent;")
        col.addWidget(who)
        h.addLayout(col, 1)
        self.code = QLabel("")
        self.code.setStyleSheet(f"font-family:{MONO}; font-size:26px; font-weight:700; letter-spacing:3px; background:transparent;")
        h.addWidget(self.code)
        self.ring = TotpRing(40)
        h.addWidget(self.ring)
        copy = QLabel()
        copy.setPixmap(icons.pix("copy", 18, C["muted"], 1.8))
        copy.setStyleSheet("background:transparent;")
        h.addWidget(copy)
        for w in self.findChildren(QLabel):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.ring.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.clicked.connect(self.copy_code)
        self.update_code(True)

    def now(self):
        try:
            code, left = core.totp_now(self.e["otp"])
            return code, left, core.otp_params(self.e["otp"])[3]
        except (ValueError, KeyError):
            return None, 0, 30

    def update_code(self, shown):
        code, left, period = self.now()
        if code is None:
            self.code.setText("Invalid key")
            self.code.setStyleSheet(f"color:{C['bad']}; font-size:14px; background:transparent;")
            return
        h = len(code) // 2
        self.code.setText(f"{code[:h]} {code[h:]}" if shown else "•••  •••")
        col = C["warn"] if left <= 5 else C["accent_hi"]
        self.code.setStyleSheet(f"font-family:{MONO}; font-size:26px; font-weight:700; letter-spacing:3px; color:{col}; background:transparent;")
        self.ring.set(left, period)

    def copy_code(self):
        code, _, _ = self.now()
        if code:
            self.win.copy_secret(code)


class AddCodeDialog(Dialog):
    """Give a login a 2-step code: pick the login, paste the setup key (or the otpauth:// link) the site shows next to its QR code."""

    def __init__(self, win, logins):
        super().__init__(win, "Add a code", 520)
        self.win, self.logins = win, logins
        self.v.addWidget(label("Add a one-time code", "h2"))
        self.v.addWidget(label("On the site's 2-step setup page choose “can't scan?” and copy the setup key. Paste it here.", "muted", True))
        self.who = QComboBox()
        for e in logins:
            self.who.addItem(f"{e['title']}" + (f"  ·  {e['username']}" if e.get("username") else ""), e)
        self.v.addWidget(form_row("Login", self.who))
        self.key = mono_edit("", False, "JBSWY3DPEHPK3PXP  or  otpauth://totp/…")
        self.v.addWidget(form_row("Setup key", self.key))
        self.err = QLabel("")
        self.err.setStyleSheet(f"color:{C['bad']};")
        self.err.setWordWrap(True)
        self.err.setMinimumWidth(1)
        self.v.addWidget(self.err)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Cancel", "", self.reject))
        row.addWidget(button("Add code", "primary", self.save, "check"))
        self.v.addLayout(row)

    def save(self):
        e = self.who.currentData()
        try:
            otp = core.normalize_otp(self.key.text())
        except ValueError as ex:
            self.err.setText(str(ex))
            return
        if not otp:
            self.err.setText("Paste the setup key first.")
            return
        self.win.save_item(e, dict(e, otp=otp), quiet=True)
        self.win.toast("Code added", "good")
        self.accept()


class CodesPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.rows = []
        self.sig = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 0)
        outer.setSpacing(6)
        top = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(label("One-time codes", "h1"))
        col.addWidget(label("The 2-step sign-in codes for all your logins, in one place. Click one to copy it.", "muted", True))
        top.addLayout(col, 1)
        top.addWidget(button("Add a code", "primary", self.add_code, "plus"), 0, Qt.AlignTop)
        outer.addLayout(top)
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search codes")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(QAction(icons.icon("search", C["muted"], 17), "", self.search), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.refresh)
        bar.addWidget(self.search, 1)
        bar.addWidget(label("Show codes", "muted"))
        self.show_sw = Switch(bool(win.s.get("codes_visible", True)))
        self.show_sw.toggled.connect(lambda on: (win.set_opt("codes_visible", on), self.tick()))
        bar.addWidget(self.show_sw)
        outer.addSpacing(10)
        outer.addLayout(bar)
        outer.addSpacing(6)
        sc = VScroll()
        host = QWidget()
        self.col = QVBoxLayout(host)
        self.col.setContentsMargins(0, 4, 8, 24)
        self.col.setSpacing(10)
        sc.setWidget(host)
        outer.addWidget(sc, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)

    def with_codes(self):
        q = self.search.text().strip().lower()
        es = [e for e in self.win.entries if e.get("otp") and model.kind_of(e) == "login"]
        if q:
            es = [e for e in es if q in f"{e['title']} {e['username']} {e['url']} {' '.join(model.item_tags(e))}".lower()]
        return sorted(es, key=lambda e: (e["title"].lower(), e["username"].lower()))

    def refresh(self, *_):
        es = self.with_codes()
        sig = tuple((e["group"], e["title"], e["otp"]) for e in es)
        if sig == self.sig:
            self.tick()
            return
        self.sig = sig
        while self.col.count():
            it = self.col.takeAt(0)
            if it.widget():
                drop_widget(it.widget())
        self.rows = []
        if not es:
            has_any = any(e.get("otp") for e in self.win.entries)
            self.col.addWidget(EmptyState("otp", "No codes match" if has_any else "No one-time codes yet",
                                          "Try another search." if has_any else "Add a code to any login: press Add a code, or edit a login and paste the setup key.",
                                          None if has_any else button("Add a code", "primary", self.add_code, "plus")))
            return
        for e in es:
            r = CodeRow(self.win, e, getattr(self.win, "logos", None))
            self.rows.append(r)
            self.col.addWidget(r)
        self.col.addStretch(1)
        self.tick()

    def tick(self):
        if not self.isVisible() and self.rows and self.sig is not None and not self.win.page == "codes":
            return
        shown = bool(self.win.s.get("codes_visible", True))
        for r in self.rows:
            r.update_code(shown)

    def add_code(self):
        logins = sorted([e for e in self.win.entries if model.kind_of(e) == "login" and not e.get("otp")], key=lambda e: e["title"].lower())
        if not logins:
            self.win.toast("Every login already has a code", "warn")
            return
        AddCodeDialog(self.win, logins).exec()
