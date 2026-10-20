"""How one item looks (ItemView) and how it is edited (ItemEditor), for logins, cards, secure notes and Wi-Fi networks. Plus the dialogs that belong to an item."""
import re
import webbrowser

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout,
                               QWidget)

import jblol_pm as core
from .. import icons, model
from ..theme import C, MONO
from ..widgets import small_chip, VScroll, Avatar, Field, IconButton, Pill, QRTile, StrengthBar, TotpRing, button, label
from .generator import GeneratorDialog

BRAND_LOOK = {"Visa": ("#1a3fa8", "#2f6df6"), "Mastercard": ("#2b2f3d", "#494f66"), "Amex": ("#0f6f6b", "#1fb5ad"), "Discover": ("#b34a00", "#f08a24"),
              "Diners": ("#3a4a6b", "#5f7aa8"), "JCB": ("#0d5a3a", "#1fa86b"), "UnionPay": ("#7a1f3d", "#c0386a"), "": ("#2a2f55", "#5460c9")}


# ------------------------------------------------------------------ small helpers
def drop_widget(w):
    w.hide()
    w.setParent(None)
    w.deleteLater()


def drop_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        if it.widget():
            drop_widget(it.widget())
        elif it.layout():
            drop_layout(it.layout())



def tile(widget_or_layout=None, pad=(16, 12, 16, 12)):
    f = QFrame()
    f.setObjectName("field")
    f.setStyleSheet(f"QFrame#field {{ background:{C['raised']}; border:1px solid {C['line']}; border-radius:12px; }}")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(*pad)
    lay.setSpacing(4)
    return f, lay


def form_row(name, widget, *extra):
    box = QVBoxLayout()
    box.setSpacing(6)
    box.addWidget(label(name.upper(), "label"))
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(widget, 1)
    for x in extra:
        row.addWidget(x)
    box.addLayout(row)
    w = QWidget()
    w.setLayout(box)
    box.setContentsMargins(0, 0, 0, 0)
    return w


def mono_edit(text="", secret=False, placeholder=""):
    e = QLineEdit(text)
    e.setProperty("role", "mono")
    e.setPlaceholderText(placeholder)
    if secret:
        e.setEchoMode(QLineEdit.Password)
    return e


# ------------------------------------------------------------------ the picture of a card
class CardVisual(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(372, 222)
        self.brand, self.number, self.holder, self.expiry, self.hidden = "", "", "", "", True

    def set(self, number, holder, expiry, hidden=True):
        self.number, self.holder, self.expiry, self.hidden = number, holder, expiry, hidden
        self.brand = model.card_brand(number)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        a, b = BRAND_LOOK.get(self.brand, BRAND_LOOK[""])
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor(a))
        g.setColorAt(1, QColor(b))
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        clip = QPainterPath()
        clip.addRoundedRect(r, 20, 20)
        p.setClipPath(clip)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawRect(r)
        p.setBrush(QColor(255, 255, 255, 22))                                          # soft shapes for depth
        p.drawEllipse(QPointF(self.width() - 40, -10), 130, 130)
        p.drawEllipse(QPointF(30, self.height() + 40), 110, 110)
        p.setBrush(QColor("#e8c872"))                                                  # the chip
        p.drawRoundedRect(QRectF(28, 62, 46, 34), 7, 7)
        p.setPen(QPen(QColor(0, 0, 0, 70), 1))
        p.drawLine(28, 79, 74, 79)
        p.drawLine(51, 62, 51, 96)
        f = QFont("Inter")
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 235))
        p.drawText(QRectF(24, 20, 200, 26), Qt.AlignLeft | Qt.AlignVCenter, self.brand or "Card")
        m = QFont("DejaVu Sans Mono")
        m.setPixelSize(22)
        m.setBold(True)
        p.setFont(m)
        p.setPen(QColor("#ffffff"))
        txt = model.card_text(self.number, self.hidden) or "•••• •••• •••• ••••"
        p.drawText(QRectF(28, 110, self.width() - 56, 34), Qt.AlignLeft | Qt.AlignVCenter, txt)
        s = QFont("Inter")
        s.setPixelSize(10)
        s.setBold(True)
        p.setFont(s)
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(28, 158, 200, 14), Qt.AlignLeft, "CARDHOLDER")
        p.drawText(QRectF(self.width() - 108, 158, 80, 14), Qt.AlignLeft, "EXPIRES")
        n = QFont("Inter")
        n.setPixelSize(15)
        n.setBold(True)
        p.setFont(n)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(28, 174, 240, 24), Qt.AlignLeft | Qt.AlignVCenter, (self.holder or "NAME ON CARD").upper())
        p.drawText(QRectF(self.width() - 108, 174, 90, 24), Qt.AlignLeft | Qt.AlignVCenter, self.expiry or "MM/YY")


# ------------------------------------------------------------------ viewing
class ItemView(QWidget):
    edit = Signal()
    delete = Signal()
    share = Signal()
    favorite = Signal()

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.e = None
        self.ticker = QTimer(self)
        self.ticker.timeout.connect(self._tick)
        self.ticker.start(1000)
        self.ring = self.code_lbl = None
        self.reveal_timers = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sc = VScroll()
        self.host = QWidget()
        self.col = QVBoxLayout(self.host)
        self.col.setContentsMargins(34, 30, 34, 30)
        self.col.setSpacing(10)
        sc.setWidget(self.host)
        outer.addWidget(sc)

    def clear(self):
        while self.col.count():
            it = self.col.takeAt(0)
            w = it.widget()
            if w:
                drop_widget(w)
            elif it.layout():
                drop_layout(it.layout())
        self.ring = self.code_lbl = None

    def show_item(self, e):
        self.e = e
        self.clear()
        kind = model.kind_of(e)
        d = e.get("data") or {}
        win = self.win
        head = QHBoxLayout()
        head.setSpacing(16)
        head.addWidget(Avatar(e["title"], kind, 64, logo=win.logos.pixmap_for(e) if kind in ("login", "passkey") else None), 0, Qt.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        t = label(re.sub(r"([.@/_-])", "\\1\u200b", e["title"] or "Untitled"), "h1")     # (a long name with no spaces may wrap after a dot or an @)
        t.setWordWrap(True)
        t.setMinimumWidth(1)
        titles.addWidget(t)
        pills = QHBoxLayout()
        pills.setSpacing(6)
        pills.addWidget(Pill(model.KIND_LABEL[kind], "accent"))
        if kind == "login" and e.get("group"):
            pills.addWidget(Pill(e["group"].split("/")[-1]))
        if kind == "card" and model.card_expired(d.get("expiry", "")):
            pills.addWidget(Pill("Expired", "bad"))
        pills.addStretch(1)
        titles.addLayout(pills)
        tags = model.item_tags(e)
        if tags:
            trow = QHBoxLayout()
            trow.setSpacing(6)
            for t in tags[:8]:
                trow.addWidget(small_chip("#" + t, lambda t=t: win.go("vault", tag=t)))
            trow.addStretch(1)
            titles.addLayout(trow)
        head.addLayout(titles, 1)
        acts = QHBoxLayout()
        acts.setSpacing(0)
        fav = bool(d.get("fav"))
        acts.addWidget(IconButton("star_fill" if fav else "star", "Remove from favorites" if fav else "Add to favorites", 19, C["amber"] if fav else C["muted"], self.favorite.emit))
        if kind in ("login", "wifi"):
            acts.addWidget(IconButton("share", "Share a private link", 19, fn=self.share.emit))
        acts.addWidget(IconButton("edit", "Edit", 19, fn=self.edit.emit))
        acts.addWidget(IconButton("trash", "Delete", 19, fn=self.delete.emit))
        head.addLayout(acts)
        self.col.addLayout(head)
        self.col.addSpacing(10)
        getattr(self, "_" + kind)(e, d)
        self.col.addStretch(1)
        meta = []
        if e.get("modified"):
            meta.append("Last changed " + e["modified"].replace("T", " ").replace("Z", " UTC"))
        if meta:
            self.col.addWidget(label("  ·  ".join(meta), "faint"))

    # -- logins
    def _login(self, e, d):
        win = self.win
        why = win.health.get((e["group"], e["title"]))
        if why:
            ban = QFrame()
            ban.setObjectName("warnbanner")
            ban.setStyleSheet(f"QFrame#warnbanner {{ background:{C['warn_soft']}; border:1px solid #5c4a1c; border-radius:12px; }}")
            bl = QHBoxLayout(ban)
            bl.setContentsMargins(14, 11, 12, 11)
            bl.setSpacing(12)
            ic = QLabel()
            ic.setPixmap(icons.pix("alert", 20, C["warn"], 2.0))
            ic.setStyleSheet("background:transparent; border:none;")
            bl.addWidget(ic, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(3)
            for _, text in why:
                tx = QLabel(text)
                tx.setWordWrap(True)
                tx.setMinimumWidth(1)
                tx.setStyleSheet(f"color:{C['warn']}; background:transparent; border:none; font-size:12.5px;")
                col.addWidget(tx)
            col.addSpacing(4)
            col.addWidget(button("Change it", "", self.edit.emit), 0, Qt.AlignLeft)
            bl.addLayout(col, 1)
            self.col.addWidget(ban)
        host = core.host_of(e.get("url", ""))
        if e.get("username"):
            self.col.addWidget(Field("Username", e["username"], copy=lambda v: win.copy_plain(v, "Username")))
        if e.get("password"):
            f = Field("Password", e["password"], secret=True, mono=True, copy=win.copy_secret)
            self.col.addWidget(f)
            bar = StrengthBar()
            bar.set_password(e["password"])
            self.col.addWidget(bar)
            win.arm_reveal(f)
        if e.get("url"):
            self.col.addWidget(Field("Website", e["url"], copy=lambda v: win.copy_plain(v, "Website"), open_=lambda: self._open(e["url"])))
        if e.get("otp"):
            f, lay = tile()
            lay.addWidget(label("ONE-TIME CODE", "label"))
            row = QHBoxLayout()
            self.code_lbl = QLabel("")
            self.code_lbl.setStyleSheet(f"font-family:{MONO}; font-size:28px; font-weight:700; letter-spacing:3px; color:{C['accent_hi']};")
            row.addWidget(self.code_lbl)
            row.addStretch(1)
            self.ring = TotpRing(40)
            row.addWidget(self.ring)
            row.addWidget(IconButton("copy", "Copy code", fn=lambda: win.copy_secret(core.totp_now(e["otp"])[0])))
            lay.addLayout(row)
            self.col.addWidget(f)
            self._tick()
        if e.get("notes"):
            f, lay = tile()
            lay.addWidget(label("NOTES", "label"))
            n = QLabel(e["notes"])
            n.setWordWrap(True)
            n.setMinimumWidth(1)
            n.setTextInteractionFlags(Qt.TextSelectableByMouse)
            n.setStyleSheet(f"color:{C['soft']};")
            lay.addWidget(n)
            self.col.addWidget(f)
        row = QHBoxLayout()
        row.setSpacing(8)
        if e.get("url") and not e["url"].startswith(("ssh", "steam")):
            row.addWidget(button("Open site & copy password", "primary", lambda: self._quick(e), "external"))
        if e.get("history"):
            row.addWidget(button("Earlier passwords", "", lambda: HistoryDialog(self.win, e).exec(), "clock"))
        row.addStretch(1)
        self.col.addSpacing(6)
        self.col.addLayout(row)
        extra = []
        att = win.vault.attachments(e) if win.vault and win.vault_real else []
        if att:
            extra.append("Attached: " + ", ".join(att) + ". Attached files, such as SSH keys, are used by the USB helper and never shown here.")
        others = [x["username"] or x["title"] for x in win.entries if host and core.host_of(x["url"]) == host and (x["group"], x["title"]) != (e["group"], e["title"])]
        if others:
            extra.append("Other accounts saved for this site: " + ", ".join(others[:6]) + ".")
        for t in extra:
            self.col.addWidget(label(t, "faint", True))

    def _open(self, u):
        u = (u or "").strip()
        if u:
            webbrowser.open(u if "://" in u else "https://" + u)

    def _quick(self, e):
        self.win.copy_secret(e["password"])
        self._open(e["url"])
        self.win.toast("Password copied. Paste it into the page.", "good")

    def _tick(self):
        if not (self.e and self.e.get("otp") and self.code_lbl):
            return
        try:
            code, left = core.totp_now(self.e["otp"])
            period = core.otp_params(self.e["otp"])[3]
        except (ValueError, KeyError):
            self.code_lbl.setText("Invalid key")
            return
        h = len(code) // 2
        self.code_lbl.setText(f"{code[:h]} {code[h:]}")
        self.code_lbl.setStyleSheet(f"font-family:{MONO}; font-size:28px; font-weight:700; letter-spacing:3px; color:{C['warn'] if left <= 5 else C['accent_hi']};")
        self.ring.set(left, period)

    # -- cards
    def _card(self, e, d):
        win = self.win
        vis = CardVisual()
        vis.set(d.get("number", ""), d.get("holder", ""), d.get("expiry", ""), True)
        row = QHBoxLayout()
        row.addWidget(vis)
        row.addStretch(1)
        self.col.addLayout(row)
        eye = button("Show number", "ghost", None, "eye")
        eye.clicked.connect(lambda: (vis.set(d.get("number", ""), d.get("holder", ""), d.get("expiry", ""), eye.text() == "Hide number"),
                                     eye.setText("Show number" if eye.text() == "Hide number" else "Hide number")))
        r2 = QHBoxLayout()
        r2.addWidget(eye)
        r2.addStretch(1)
        self.col.addLayout(r2)
        if d.get("holder"):
            self.col.addWidget(Field("Cardholder", d["holder"], copy=lambda v: win.copy_plain(v, "Name")))
        if d.get("number"):
            f = Field("Card number", model.card_text(d["number"], False), secret=True, mono=True, copy=lambda v: win.copy_secret(model.card_digits(v)))
            self.col.addWidget(f)
            win.arm_reveal(f)
        r = QHBoxLayout()
        r.setSpacing(10)
        if d.get("expiry"):
            r.addWidget(Field("Expires", d["expiry"], copy=lambda v: win.copy_plain(v, "Expiry")))
        if d.get("cvv"):
            f = Field("Security code", d["cvv"], secret=True, mono=True, copy=win.copy_secret)
            r.addWidget(f)
            win.arm_reveal(f)
        if d.get("pin"):
            f = Field("PIN", d["pin"], secret=True, mono=True, copy=win.copy_secret)
            r.addWidget(f)
            win.arm_reveal(f)
        if r.count():
            w = QWidget()
            w.setLayout(r)
            r.setContentsMargins(0, 0, 0, 0)
            self.col.addWidget(w)
        if d.get("zip"):
            self.col.addWidget(Field("Billing ZIP / postal code", d["zip"], copy=lambda v: win.copy_plain(v, "ZIP")))
        self._notes(e)

    def _notes(self, e):
        if e.get("notes"):
            f, lay = tile()
            lay.addWidget(label("NOTES", "label"))
            n = QLabel(e["notes"])
            n.setWordWrap(True)
            n.setMinimumWidth(1)
            n.setTextInteractionFlags(Qt.TextSelectableByMouse)
            n.setStyleSheet(f"color:{C['soft']};")
            lay.addWidget(n)
            self.col.addWidget(f)

    # -- secure notes
    def _note(self, e, d):
        f, lay = tile(pad=(20, 16, 20, 18))
        row = QHBoxLayout()
        row.addWidget(label("NOTE", "label"))
        row.addStretch(1)
        row.addWidget(IconButton("copy", "Copy the note", fn=lambda: self.win.copy_secret(e.get("notes", ""))))
        lay.addLayout(row)
        n = QLabel(e.get("notes") or "This note is empty.")
        n.setWordWrap(True)
        n.setMinimumWidth(1)
        n.setTextInteractionFlags(Qt.TextSelectableByMouse)
        n.setStyleSheet(f"font-size:14px; line-height:150%; color:{C['ink'] if e.get('notes') else C['faint']};")
        lay.addWidget(n)
        self.col.addWidget(f)

    # -- passkeys
    def _passkey(self, e, d):
        win = self.win
        host = d.get("rp_id", "")
        self.col.addWidget(Field("Website", host, copy=lambda v: win.copy_plain(v, "Website"), open_=lambda: self._open(host)))
        if d.get("user_name") or d.get("display_name"):
            self.col.addWidget(Field("Account", d.get("user_name") or d.get("display_name"), copy=lambda v: win.copy_plain(v, "Account")))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(Field("Made on", d.get("created", "?")))
        cid = d.get("cred_id", "")
        row.addWidget(Field("Passkey ID", (cid[:10] + "…" + cid[-6:]) if len(cid) > 18 else cid, mono=True, copy=lambda v: win.copy_plain(cid, "Passkey ID")))
        w = QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.col.addWidget(w)
        f, lay = tile()
        lay.addWidget(label("HOW IT WORKS", "label"))
        lay.addWidget(label("When this site asks you to sign in with a passkey, the browser extension asks this app. You approve it here, and the app signs the request. "
                            "The private key stays in your vault: it is never shown and never leaves this computer except in the encrypted vault that syncs between your computers.", "muted", True))
        self.col.addWidget(f)
        self._notes(e)

    # -- Wi-Fi
    def _wifi(self, e, d):
        win = self.win
        ssid = d.get("ssid") or e["title"]
        uri = model.wifi_uri(ssid, e.get("password", ""), d.get("security", "WPA"), bool(d.get("hidden")))
        top = QHBoxLayout()
        top.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(Field("Network name", ssid, copy=lambda v: win.copy_plain(v, "Network name")))
        if d.get("security", "WPA") != "nopass":
            f = Field("Password", e.get("password", ""), secret=True, mono=True, copy=win.copy_secret)
            left.addWidget(f)
            win.arm_reveal(f)
        left.addWidget(Field("Security", dict(model.WIFI_SECURITY).get(d.get("security", "WPA"), "WPA2 / WPA3") + (" · hidden network" if d.get("hidden") else "")))
        lw = QWidget()
        lw.setLayout(left)
        left.setContentsMargins(0, 0, 0, 0)
        top.addWidget(lw, 1, Qt.AlignTop)
        qrcard = QFrame()
        qrcard.setObjectName("field")
        qrcard.setStyleSheet(f"QFrame#field {{ background:{C['raised']}; border:1px solid {C['line']}; border-radius:14px; }}")
        ql = QVBoxLayout(qrcard)
        ql.setContentsMargins(16, 16, 16, 14)
        ql.setSpacing(8)
        t = QRTile(210)
        t.set_text(uri)
        ql.addWidget(t, 0, Qt.AlignCenter)
        cap = label("Point a phone camera here to join", "muted", True)
        cap.setAlignment(Qt.AlignCenter)
        ql.addWidget(cap)
        ql.addWidget(button("Full screen", "ghost", lambda: QRDialog(win, ssid, uri).exec(), "qr"))
        top.addWidget(qrcard, 0, Qt.AlignTop)
        w = QWidget()
        w.setLayout(top)
        top.setContentsMargins(0, 0, 0, 0)
        self.col.addWidget(w)
        self._notes(e)


# ------------------------------------------------------------------ editing
class ItemEditor(QWidget):
    saved = Signal(object, object)
    cancelled = Signal()

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.old = None
        self.kind = "login"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sc = VScroll()
        self.host = QWidget()
        self.col = QVBoxLayout(self.host)
        self.col.setContentsMargins(34, 28, 34, 24)
        self.col.setSpacing(14)
        sc.setWidget(self.host)
        outer.addWidget(sc, 1)
        bar = QFrame()
        bar.setStyleSheet(f"QFrame {{ border-top:1px solid {C['line']}; background:transparent; }}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(34, 12, 34, 14)
        self.err = QLabel("")
        self.err.setStyleSheet(f"color:{C['bad']}; border:none;")
        bl.addWidget(self.err, 1)
        bl.addWidget(button("Cancel", "", self.cancelled.emit))
        self.b_save = button("Save", "primary", self.save, "check")
        bl.addWidget(self.b_save)
        outer.addWidget(bar)
        self.w = {}

    def clear(self):
        while self.col.count():
            it = self.col.takeAt(0)
            if it.widget():
                drop_widget(it.widget())
            elif it.layout():
                drop_layout(it.layout())
        self.w = {}

    def load(self, e, new=False):
        self.old = None if new else e
        self.kind = model.kind_of(e)
        d = e.get("data") or {}
        self.d0 = dict(d)
        self.clear()
        self.err.setText("")
        title = {"login": "Login", "card": "Card", "note": "Secure note", "wifi": "Wi-Fi network", "passkey": "Passkey"}[self.kind]
        head = QHBoxLayout()
        self.badge = Avatar(e["title"] or "?", self.kind, 48)
        head.addWidget(self.badge)
        head.addSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(("New " if new else "Edit ") + title.lower(), "h2"))
        col.addWidget(label("Everything is encrypted with the key file on your USB stick.", "faint"))
        head.addLayout(col, 1)
        self.col.addLayout(head)
        self.col.addSpacing(4)

        def add(key, name, widget, *extra):
            self.w[key] = widget
            self.col.addWidget(form_row(name, widget, *extra))
        t = QLineEdit(e["title"])
        t.setPlaceholderText({"login": "For example GitHub", "card": "For example Chase Visa", "note": "For example Locker combination", "wifi": "For example Home Wi-Fi", "passkey": "Name"}[self.kind])
        t.textChanged.connect(lambda s: self.badge.set(s or "?", self.kind))
        add("title", "Name", t)
        if self.kind == "login":
            g = QComboBox()
            g.setEditable(True)
            g.addItems([""] + [x for x in self.win.groups if x not in model.KIND_GROUP.values()])
            g.setCurrentText(e["group"])
            add("group", "Folder", g)
            add("username", "Username or email", QLineEdit(e["username"]))
            pw = mono_edit(e["password"], True)
            show = IconButton("eye", "Show", fn=lambda: (pw.setEchoMode(QLineEdit.Normal if pw.echoMode() == QLineEdit.Password else QLineEdit.Password)))
            gen = IconButton("dice", "Generate a password", color=C["accent_hi"], fn=lambda: self._generate(pw))
            add("password", "Password", pw, show, gen)
            self.meter = StrengthBar()
            pw.textChanged.connect(self.meter.set_password)
            self.meter.set_password(pw.text())
            self.col.addWidget(self.meter)
            add("url", "Website or app", QLineEdit(e["url"]))
            otp = mono_edit(e.get("otp", ""), False, "Paste the setup key or otpauth:// link from the site's 2-step setup")
            add("otp", "One-time code setup key", otp)
            self._notes_field(e)
        elif self.kind == "card":
            add("holder", "Cardholder name", QLineEdit(d.get("holder", "")))
            num = mono_edit(model.card_text(d.get("number", ""), False), False, "1234 5678 9012 3456")
            self.brand = Pill("", "accent")
            self.brand.setVisible(False)
            num.textChanged.connect(lambda s: self._fmt_number(num))
            add("number", "Card number", num, self.brand)
            self._fmt_number(num)
            r = QHBoxLayout()
            r.setSpacing(12)
            exp = mono_edit(d.get("expiry", ""), False, "MM/YY")
            exp.textChanged.connect(lambda s: self._fmt_expiry(exp))
            self.w["expiry"] = exp
            cvv = mono_edit(d.get("cvv", ""), True, "3 or 4 digits")
            self.w["cvv"] = cvv
            pin = mono_edit(d.get("pin", ""), True, "Optional")
            self.w["pin"] = pin
            for name, wid in (("Expires", exp), ("Security code", cvv), ("PIN", pin)):
                r.addWidget(form_row(name, wid), 1)
            rw = QWidget()
            rw.setLayout(r)
            r.setContentsMargins(0, 0, 0, 0)
            self.col.addWidget(rw)
            add("zip", "Billing ZIP / postal code", QLineEdit(d.get("zip", "")))
            self._notes_field(e)
        elif self.kind == "passkey":
            self.col.addWidget(label("The key itself can't be edited. You can rename this passkey and add notes or tags.", "faint", True))
            self._notes_field(e)
        elif self.kind == "note":
            body = QPlainTextEdit(e.get("notes", ""))
            body.setMinimumHeight(280)
            body.setPlaceholderText("Write anything private: door codes, recovery phrases, account numbers…")
            self.w["notes"] = body
            self.col.addWidget(form_row("Note", body))
        else:
            add("ssid", "Network name (SSID)", QLineEdit(d.get("ssid", "")))
            pw = mono_edit(e.get("password", ""), True)
            show = IconButton("eye", "Show", fn=lambda: pw.setEchoMode(QLineEdit.Normal if pw.echoMode() == QLineEdit.Password else QLineEdit.Password))
            gen = IconButton("dice", "Generate a password", color=C["accent_hi"], fn=lambda: self._generate(pw))
            add("password", "Wi-Fi password", pw, show, gen)
            sec = QComboBox()
            for k, text in model.WIFI_SECURITY:
                sec.addItem(text, k)
            sec.setCurrentIndex(max(0, [k for k, _ in model.WIFI_SECURITY].index(d.get("security", "WPA"))))
            add("security", "Security", sec)
            hid = QCheckBox("This is a hidden network")
            hid.setChecked(bool(d.get("hidden")))
            self.w["hidden"] = hid
            self.col.addWidget(hid)
            self._notes_field(e)
        tags = QLineEdit(", ".join(model.item_tags(e)))
        tags.setPlaceholderText("work, school, bank")
        self.w["tags"] = tags
        self.col.addWidget(form_row("Tags", tags))
        self._tag_suggestions(tags)
        self.col.addStretch(1)
        self.w["title"].setFocus()

    def _tag_suggestions(self, edit):
        """The tags you already use, as small buttons that add or remove the tag in the box."""
        known = [t for t, _ in model.tag_counts(self.win.entries)][:8]
        if not known:
            return
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(label("Yours:", "faint"))

        def flip(t):
            cur = model.norm_tags(edit.text())
            edit.setText(", ".join([x for x in cur if x != t] if t in cur else cur + [t]))
        for t in known:
            row.addWidget(small_chip("#" + t, lambda t=t: flip(t)))
        row.addStretch(1)
        w = QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.col.addWidget(w)

    def _notes_field(self, e):
        n = QPlainTextEdit(e.get("notes", ""))
        n.setFixedHeight(96)
        self.w["notes"] = n
        self.col.addWidget(form_row("Notes", n))

    def _fmt_number(self, edit):
        digits = model.card_digits(edit.text())[:19]
        text = " ".join(model.card_groups(digits))
        if text != edit.text():
            edit.blockSignals(True)
            edit.setText(text)
            edit.blockSignals(False)
        b = model.card_brand(digits)
        self.brand.setVisible(bool(b))
        self.brand.set(b + ("" if len(digits) < 12 or model.luhn_ok(digits) else " · check the number"), "accent" if (len(digits) < 12 or model.luhn_ok(digits)) else "warn")

    def _fmt_expiry(self, edit):
        t = re.sub(r"[^\d]", "", edit.text())[:4]
        if len(t) >= 3:
            t = t[:2] + "/" + t[2:]
        if t != edit.text():
            edit.blockSignals(True)
            edit.setText(t)
            edit.blockSignals(False)

    def _generate(self, pw):
        d = GeneratorDialog(self.win)
        if d.exec() and d.result:
            pw.setText(d.result)
            pw.setEchoMode(QLineEdit.Normal)

    def read(self):
        k, w = self.kind, self.w
        text = lambda key: (w[key].text() if hasattr(w[key], "text") else w[key].toPlainText()).strip()
        base = dict(self.old) if self.old else model.new_item(k, self.win.current_group_for(k))
        e = dict(base, kind=k)
        e["title"] = text("title")
        data = dict(self.d0)
        if k == "login":
            e.update(group=w["group"].currentText().strip().strip("/") or "Websites", username=w["username"].text().strip(), password=w["password"].text(),
                     url=text("url"), otp=text("otp"), notes=w["notes"].toPlainText().rstrip())
        elif k == "card":
            num = model.card_digits(w["number"].text())
            data.update(holder=text("holder"), number=num, expiry=text("expiry"), cvv=w["cvv"].text().strip(), pin=w["pin"].text().strip(), zip=text("zip"))
            e.update(group="Cards", username=data["holder"], password="", notes=w["notes"].toPlainText().rstrip())
            if not e["title"]:
                e["title"] = f"{model.card_brand(num) or 'Card'}" + (f" •••• {num[-4:]}" if num else "")
        elif k == "passkey":
            e.update(group="Passkeys", notes=w["notes"].toPlainText().rstrip(), password="")
        elif k == "note":
            e.update(group="Secure notes", notes=w["notes"].toPlainText().rstrip(), username="", password="")
        else:
            data.update(ssid=text("ssid") or e["title"], security=w["security"].currentData(), hidden=w["hidden"].isChecked())
            e.update(group="Wi-Fi", username="", password=w["password"].text(), notes=w["notes"].toPlainText().rstrip())
            if not e["title"]:
                e["title"] = data["ssid"]
        data["tags"] = model.norm_tags(w["tags"].text())
        e["data"] = {k_: v for k_, v in data.items() if v not in ("", None, False, [], {})}
        return e

    def save(self):
        e = self.read()
        if not e["title"].strip():
            self.err.setText("Give it a name first.")
            self.w["title"].setFocus()
            return
        try:
            e["otp"] = core.normalize_otp(e.get("otp", ""))
        except ValueError as ex:
            self.err.setText(str(ex))
            return
        self.err.setText("")
        self.saved.emit(self.old, e)


# ------------------------------------------------------------------ dialogs
class Dialog(QDialog):
    def __init__(self, win, title, width=520):
        super().__init__(win)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(26, 24, 26, 22)
        self.v.setSpacing(12)


class PasskeyPrompt(Dialog):
    """A website asked for a passkey: say who is asking and what for, and let you allow it, hand it back to the browser, or say no.
    run() -> ("allow", index of the chosen account) | ("browser", None) | ("cancel", None)."""

    def __init__(self, win, mode, site, rp_id, accounts, replaces=False):
        super().__init__(win, "Passkey", 480)
        self.choice, self.result_ = ("cancel", None), None
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        make = mode == "create"
        t = QHBoxLayout()
        t.setSpacing(14)
        t.addWidget(Avatar(site or rp_id, "passkey", 52), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        col.addWidget(label("Save a passkey?" if make else "Sign in with a passkey?", "h2"))
        col.addWidget(label(f"{site or rp_id}  ·  {rp_id}", "muted"))
        t.addLayout(col, 1)
        self.v.addLayout(t)
        self.list = None
        if make:
            self.v.addWidget(label(f"This makes a new passkey for {accounts[0] or 'your account'} on {rp_id} and keeps it in your vault. "
                                   "You'll sign in with it instead of a password." + (" It replaces the passkey you already have for this account." if replaces else ""), "muted", True))
        elif len(accounts) > 1:
            self.v.addWidget(label(f"Which account do you want to use on {rp_id}?", "muted", True))
            from PySide6.QtWidgets import QListWidget
            self.list = QListWidget()
            self.list.setStyleSheet(f"QListWidget {{ background:{C['raised']}; border:1px solid {C['line']}; border-radius:10px; padding:4px; }}"
                                    f"QListWidget::item {{ padding:10px 12px; border-radius:8px; }}"
                                    f"QListWidget::item:selected {{ background:{C['accent_soft']}; color:{C['accent_hi']}; }}"
                                    f"QListWidget::item:hover {{ background:{C['hover']}; }}")
            self.list.setFixedHeight(min(220, 44 * len(accounts) + 14))
            for a in accounts:
                self.list.addItem(a or "(no name)")
            self.list.setCurrentRow(0)
            self.v.addWidget(self.list)
        else:
            self.v.addWidget(label(f"Sign in to {rp_id} as {accounts[0] or 'your account'}.", "muted", True))
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(button("Use my browser instead", "ghost", lambda: self._done("browser")))
        row.addStretch(1)
        row.addWidget(button("Not now", "", lambda: self._done("cancel")))
        row.addWidget(button("Save passkey" if make else "Sign in", "primary", lambda: self._done("allow"), "check"))
        self.v.addLayout(row)

    def _done(self, what):
        self.choice = (what, self.list.currentRow() if (what == "allow" and self.list is not None) else 0 if what == "allow" else None)
        self.accept()

    def run(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.exec()
        return self.choice


class FolderDialog(Dialog):
    """Make a folder, at the top or inside another one."""

    def __init__(self, win, folders, inside=""):
        super().__init__(win, "New folder", 440)
        self.v.addWidget(label("New folder", "h2"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("For example School")
        self.v.addWidget(form_row("Name", self.name_edit))
        self.where = QComboBox()
        self.where.addItem("Top level", "")
        for f in folders:
            self.where.addItem("  " * f.count("/") + f.split("/")[-1] + ("   (" + f + ")" if "/" in f else ""), f)
        i = self.where.findData(inside)
        self.where.setCurrentIndex(max(0, i))
        self.v.addWidget(form_row("Inside", self.where))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Cancel", "", self.reject))
        row.addWidget(button("Make folder", "primary", self.accept, "plus"))
        self.v.addLayout(row)
        self.name_edit.setFocus()

    def name(self):
        return self.name_edit.text().strip().replace("/", "-")

    def parent(self):
        return self.where.currentData() or ""


class HistoryDialog(Dialog):
    def __init__(self, win, e):
        super().__init__(win, "Earlier passwords", 520)
        self.v.addWidget(label("Earlier passwords", "h2"))
        self.v.addWidget(label("Kept in case a site didn't take the new password. Newest first.", "muted", True))
        for when, pw in e.get("history", []):
            row = QHBoxLayout()
            row.addWidget(label(when, "muted"))
            val = mono_edit(pw, True)
            val.setReadOnly(True)
            row.addWidget(val, 1)
            row.addWidget(IconButton("eye", "Show", fn=lambda v=val: v.setEchoMode(QLineEdit.Normal if v.echoMode() == QLineEdit.Password else QLineEdit.Password)))
            row.addWidget(IconButton("copy", "Copy", fn=lambda p=pw: win.copy_secret(p)))
            self.v.addLayout(row)
        self.v.addWidget(button("Close", "", self.accept), 0, Qt.AlignRight)


class QRDialog(Dialog):
    """The Wi-Fi QR code, big, for pointing a phone at (or saving as a picture)."""

    def __init__(self, win, ssid, uri):
        super().__init__(win, f"Join {ssid}", 460)
        self.uri, self.ssid = uri, ssid
        self.v.setAlignment(Qt.AlignHCenter)
        t = label(f"Join “{ssid}”", "h2")
        t.setAlignment(Qt.AlignCenter)
        self.v.addWidget(t)
        c = label("Open the camera on a phone and point it at this code. It asks to join the network.", "muted", True)
        c.setAlignment(Qt.AlignCenter)
        self.v.addWidget(c)
        self.tile = QRTile(340)
        self.tile.set_text(uri)
        self.v.addWidget(self.tile, 0, Qt.AlignCenter)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Save as picture…", "", self.save, "download"))
        row.addWidget(button("Done", "primary", self.accept))
        self.v.addLayout(row)

    def save(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save the QR code", f"{self.ssid}-wifi.png", "PNG image (*.png)")
        if f:
            self.tile.grab().save(f)


class ShareDialog(Dialog):
    def __init__(self, win, entries, what):
        super().__init__(win, f"Share {what}", 540)
        self.win, self.entries, self.share_id = win, entries, None
        self.v.addWidget(label(f"Share {what}", "h2"))
        self.v.addWidget(label("Makes a private link. The password is encrypted on this computer first and the key is inside the link only, so the server can't read it. "
                               "The link stops working after the time you pick.", "muted", True))
        self.ttl = QComboBox()
        for text, _ in core.SHARE_TTLS:
            self.ttl.addItem(text)
        self.v.addWidget(form_row("Link works for", self.ttl))
        self.repeat = QCheckBox("Let the link be opened more than once until it expires (otherwise it works once)")
        self.v.addWidget(self.repeat)
        self.link = mono_edit("")
        self.link.setReadOnly(True)
        self.link.hide()
        self.v.addWidget(self.link)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setMinimumWidth(1)
        self.v.addWidget(self.msg)
        row = QHBoxLayout()
        self.b_stop = button("Stop sharing now", "danger", self.stop)
        self.b_stop.hide()
        self.b_copy = button("Copy link", "", self.copy, "copy")
        self.b_copy.hide()
        self.b_make = button("Create link", "primary", self.make)
        row.addWidget(self.b_stop)
        row.addStretch(1)
        row.addWidget(self.b_copy)
        row.addWidget(self.b_make)
        row.addWidget(button("Close", "", self.accept))
        self.v.addLayout(row)

    def make(self):
        ttl = core.SHARE_TTLS[self.ttl.currentIndex()][1]
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            link, self.share_id = core.create_share(self.win.s, self.entries, ttl, not self.repeat.isChecked())
        except core.VaultError as e:
            self.msg.setText(f"<span style='color:{C['bad']}'>{e}</span>")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.link.setText(link)
        for w in (self.link, self.b_copy, self.b_stop):
            w.show()
        self.b_make.hide()
        self.ttl.setEnabled(False)
        self.repeat.setEnabled(False)
        self.msg.setText(f"<span style='color:{C['good']}'>Link ready. Anyone who has the whole link can see it until it expires"
                         f"{' or is opened once' if not self.repeat.isChecked() else ''}.</span>")
        core.log_event(f"Created a share link for {len(self.entries)} item(s), {core.SHARE_TTLS[self.ttl.currentIndex()][0]}")
        self.copy()

    def copy(self):
        self.win.copy_plain(self.link.text(), "Link")

    def stop(self):
        try:
            core._share_call(self.win.s, f"/api/revoke/{self.share_id}", {})
        except core.VaultError as e:
            self.msg.setText(f"<span style='color:{C['bad']}'>{e}</span>")
            return
        core.log_event("Stopped sharing a link")
        self.link.clear()
        self.link.hide()
        self.b_copy.hide()
        self.b_stop.hide()
        self.msg.setText("The link no longer works.")
