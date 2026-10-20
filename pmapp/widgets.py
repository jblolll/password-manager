"""The small pieces every page is built from: switches, icon buttons, avatars, cards, the strength bar, the code ring, the QR tile, toasts."""
import hashlib

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Property
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QFrame, QLayout, QScrollArea, QHBoxLayout, QLabel, QPushButton, QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout,
                               QWidget)

from . import icons, model, qr
from .theme import C, MONO

KIND_ICON = {"login": "key", "card": "card", "note": "note", "wifi": "wifi", "passkey": "passkey"}
KIND_TINT = {"card": C["violet"], "note": C["amber"], "wifi": C["teal"], "passkey": C["pink"]}


class VScroll(QScrollArea):
    """A scroll area that only ever scrolls up and down: whatever is inside is kept exactly as wide as the window shows (a long line wraps instead of
    stretching the whole page sideways)."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def setWidget(self, w):
        super().setWidget(w)
        lay = w.layout()
        if lay is not None:
            lay.setSizeConstraint(QLayout.SetNoConstraint)
        self._fit()

    def _fit(self):
        w = self.widget()
        if w is not None:
            w.setMaximumWidth(max(1, self.viewport().width()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()


def label(text="", role="", wrap=False, selectable=False):
    l = QLabel(text)
    if role:
        l.setProperty("role", role)
    l.setWordWrap(wrap)
    if wrap:
        l.setMinimumWidth(1)                       # (a wrapped label otherwise insists on the width of its longest line)
    if selectable:
        l.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return l


def hline():
    f = QFrame()
    f.setObjectName("sep")
    return f


def button(text, kind="", fn=None, icon=None, icon_color=None):
    b = QPushButton(text.replace("&", "&&"))
    if kind:
        b.setObjectName(kind)
    if icon:
        b.setIcon(icons.icon(icon, icon_color or ("#ffffff" if kind == "primary" else C["bad"] if kind == "danger" else C["soft"]), 16))
        b.setIconSize(QSize(16, 16))
    b.setCursor(Qt.PointingHandCursor)
    if fn:
        b.clicked.connect(lambda _=False: fn())
    return b


def small_chip(text, fn=None, checkable=False):
    """A small rounded tag button (the full look is written out, because a style sheet on one button replaces the shared one)."""
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setCheckable(checkable)
    b.setStyleSheet(f"QPushButton {{ background:transparent; border:1px solid {C['line2']}; border-radius:11px; padding:2px 10px; color:{C['muted']}; font-size:12px; font-weight:550; }}"
                    f"QPushButton:hover {{ color:{C['ink']}; border-color:#3a4560; }}"
                    f"QPushButton:checked {{ background:{C['accent_soft']}; border-color:#2e458f; color:{C['accent_hi']}; }}")
    if fn:
        b.clicked.connect(lambda _=False: fn())
    return b


class IconButton(QToolButton):
    def __init__(self, name, tip="", size=18, color=None, fn=None):
        super().__init__()
        self.setObjectName("iconbtn")
        self.name, self.color, self.size_ = name, color or C["muted"], size
        self.setIconSize(QSize(size, size))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)
        self.set_icon(name)
        if fn:
            self.clicked.connect(lambda _=False: fn())

    def set_icon(self, name, color=None):
        self.name = name
        if color:
            self.color = color
        self.setIcon(icons.icon(name, self.color, self.size_))

    def enterEvent(self, e):
        self.setIcon(icons.icon(self.name, C["ink"], self.size_))
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setIcon(icons.icon(self.name, self.color, self.size_))
        super().leaveEvent(e)


class Switch(QAbstractButton):
    """An on/off switch. `toggled(bool)` fires when the user flips it."""

    def __init__(self, on=False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(on)
        self.setFixedSize(42, 24)
        self.setCursor(Qt.PointingHandCursor)
        self._x = 1.0 if on else 0.0
        self.anim = QPropertyAnimation(self, b"pos_", self)
        self.anim.setDuration(130)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._slide)

    def _slide(self, on):
        self.anim.stop()
        self.anim.setStartValue(self._x)
        self.anim.setEndValue(1.0 if on else 0.0)
        self.anim.start()

    def _get(self):
        return self._x

    def _set(self, v):
        self._x = v
        self.update()
    pos_ = Property(float, _get, _set)

    def setChecked(self, on):
        super().setChecked(on)
        self._x = 1.0 if on else 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = QColor(C["accent"])
        off = QColor(C["line2"])
        t = self._x
        col = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t), int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            col.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 11, 11)
        p.setBrush(QColor("#ffffff") if self.isEnabled() else QColor("#9aa3b8"))
        x = 4 + t * (self.width() - 24)
        p.drawEllipse(QRectF(x, 4, 16, 16))


def avatar_pixmap(title, kind="login", size=40, dpr=2.0, logo=None):
    """The little tile for an item: the site's logo on a light tile if there is one, else a coloured tile with the first letter (or the kind's icon)."""
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    if logo is not None and not logo.isNull():
        p.setPen(QPen(QColor(0, 0, 0, 40), 1))
        p.setBrush(QColor("#f4f6fb"))
        p.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), size * 0.28, size * 0.28)
        pad = size * 0.17
        inner = size - 2 * pad
        scaled = logo.scaled(int(inner * dpr), int(inner * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        w, h = scaled.width() / dpr, scaled.height() / dpr
        p.drawPixmap(int(pad + (inner - w) / 2), int(pad + (inner - h) / 2), int(w), int(h), scaled)
        p.end()
        return px
    if kind in KIND_TINT:
        tint = QColor(KIND_TINT[kind])
        top, bot = QColor(tint), QColor(tint)
        top.setHsl(top.hslHue(), 125, 78)
        bot.setHsl(bot.hslHue(), 130, 52)
    else:
        hue = int(hashlib.md5((title or "?").lower().encode()).hexdigest()[:4], 16) % 360
        top, bot = QColor.fromHsl(hue, 150, 105), QColor.fromHsl(hue, 150, 70)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, top)
    g.setColorAt(1, bot)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    if kind in KIND_TINT:
        s = int(size * 0.5)
        p.drawPixmap(int((size - s) / 2), int((size - s) / 2), icons.pix(KIND_ICON[kind], s, "#ffffff", 1.9))
    else:
        p.setPen(QColor("#ffffff"))
        f = QFont("Inter")
        f.setBold(True)
        f.setPixelSize(int(size * 0.44))
        p.setFont(f)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, ((title or "?").strip()[:1] or "?").upper())
    p.end()
    return px


class Avatar(QLabel):
    def __init__(self, title="", kind="login", size=40, logo=None):
        super().__init__()
        self.size_ = size
        self.setFixedSize(size, size)
        self.set(title, kind, logo)

    def set(self, title, kind="login", logo=None):
        self.setPixmap(avatar_pixmap(title, kind, self.size_, logo=logo))


class Pill(QLabel):
    def __init__(self, text="", tone=""):
        super().__init__(text)
        self.setObjectName("pill")
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignCenter)

    def set(self, text, tone=""):
        self.setText(text)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)


class Card(QFrame):
    """A rounded panel with an optional heading. Add content with `.body` (a QVBoxLayout)."""

    def __init__(self, title="", subtitle="", flat=False, pad=20, right=None, size="h3"):
        super().__init__()
        self.setObjectName("cardflat" if flat else "card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(pad, pad - 2, pad, pad)
        outer.setSpacing(12)
        if title:
            head = QHBoxLayout()
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(label(title, size))
            if subtitle:
                col.addWidget(label(subtitle, "muted", True))
            head.addLayout(col, 1)
            if right is not None:
                head.addWidget(right, 0, Qt.AlignTop)
            outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)


class SettingRow(QWidget):
    """Title + explanation on the left, a control (switch, button, spin box) on the right."""

    def __init__(self, title, text="", control=None, tag=""):
        super().__init__()
        self.keywords = f"{title} {text} {tag}".lower()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 6, 0, 6)
        h.setSpacing(16)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label(title, "h3")
        self.title.setStyleSheet("font-weight:600; font-size:13.5px;")
        col.addWidget(self.title)
        if text:
            self.text = label(text, "muted", True)
            self.text.setStyleSheet(f"color:{C['muted']}; font-size:12.5px;")
            col.addWidget(self.text)
        h.addLayout(col, 1)
        if control is not None:
            h.addWidget(control, 0, Qt.AlignVCenter)
        self.control = control


class StrengthBar(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(22)
        self.level, self.text, self.color = 0, "", C["muted"]

    def set_password(self, pw):
        from jblol_pm import strength
        bits, text, color = strength(pw)
        self.level = 0 if not pw else 1 if bits < 40 else 2 if bits < 60 else 3 if bits < 90 else 4
        self.text, self.color = (f"{text} · {int(bits)} bits" if pw else ""), color
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = 46
        for i in range(4):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self.color) if i < self.level else QColor(C["line2"]))
            p.drawRoundedRect(QRectF(i * (w + 5), 8, w, 5), 2.5, 2.5)
        p.setPen(QColor(self.color if self.level else C["muted"]))
        f = QFont("Inter")
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRect(4 * (w + 5) + 6, 0, 200, 22), Qt.AlignVCenter | Qt.AlignLeft, self.text)


class TotpRing(QWidget):
    def __init__(self, size=40):
        super().__init__()
        self.setFixedSize(size, size)
        self.left, self.period = 30, 30

    def set(self, left, period=30):
        self.left, self.period = left, period
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(4, 4, self.width() - 8, self.height() - 8)
        p.setPen(QPen(QColor(C["line2"]), 3))
        p.drawEllipse(r)
        warn = self.left <= 5
        p.setPen(QPen(QColor(C["warn"] if warn else C["accent"]), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 90 * 16, int(-360 * 16 * self.left / max(1, self.period)))
        p.setPen(QColor(C["warn"] if warn else C["soft"]))
        f = QFont("Inter")
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, str(self.left))


class QRTile(QWidget):
    """A QR code on a white rounded tile (white so phone cameras read it, whatever the app's theme)."""

    def __init__(self, size=220):
        super().__init__()
        self.setFixedSize(size, size)
        self.grid = None

    def set_text(self, text):
        try:
            self.grid = qr.make(text) if text else None
        except ValueError:
            self.grid = None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(self.rect(), 14, 14)
        if not self.grid:
            return
        n = len(self.grid)
        cell = (self.width() - 2 * 14) / (n + 0.0)
        cell = min(cell, (self.width() - 24) / (n + 2 * 0))
        side = cell * n
        x0, y0 = (self.width() - side) / 2, (self.height() - side) / 2
        p.setBrush(QColor("#0b0d12"))
        for r in range(n):
            for c in range(n):
                if self.grid[r][c]:
                    p.drawRect(QRectF(x0 + c * cell, y0 + r * cell, cell + 0.4, cell + 0.4))


class Toast(QLabel):
    """A short message that floats at the bottom of the window and fades."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("toast")
        self.hide()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.setAlignment(Qt.AlignCenter)

    def show_message(self, text, tone="", ms=3200):
        col = {"good": C["good"], "bad": C["bad"], "warn": C["warn"]}.get(tone, C["ink"])
        self.setStyleSheet(f"QLabel#toast {{ background:{C['raised']}; border:1px solid {C['line2']}; border-radius:12px; padding:11px 18px; color:{col}; font-weight:600; }}")
        self.setText(text)
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()
        self.timer.start(ms)

    def place(self):
        p = self.parentWidget()
        if p:
            self.move((p.width() - self.width()) // 2, p.height() - self.height() - 28)


class EmptyState(QWidget):
    def __init__(self, icon_name, title, text="", action=None):
        super().__init__()
        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(icons.pix(icon_name, 44, C["faint"], 1.5))
        ic.setAlignment(Qt.AlignCenter)
        v.addWidget(ic)
        t = label(title, "h2")
        t.setAlignment(Qt.AlignCenter)
        v.addWidget(t)
        if text:
            m = label(text, "muted", True)
            m.setAlignment(Qt.AlignCenter)
            m.setFixedWidth(300)                                     # (a centred wrapped label has no width of its own: without this it collapses to a sliver)
            v.addWidget(m, 0, Qt.AlignCenter)
        if action:
            v.addSpacing(6)
            v.addWidget(action, 0, Qt.AlignCenter)


class Field(QWidget):
    """One labelled value in an item: label above, value below, little icon buttons at the right (copy, show, open)."""

    def __init__(self, name, value="", secret=False, mono=False, copy=None, open_=None, big=False):
        super().__init__()
        self.value, self.secret, self.shown = value or "", secret, False
        self.copy_fn = copy
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 11, 10, 11)
        v.setSpacing(3)
        v.addWidget(label(name.upper(), "label"))
        row = QHBoxLayout()
        row.setSpacing(2)
        self.val = QLabel()
        self.val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.val.setWordWrap(True)
        self.val.setMinimumWidth(1)
        self.val.setStyleSheet(f"font-size:{16 if big else 14}px; {'font-family:' + MONO + ';' if mono else ''}")
        row.addWidget(self.val, 1)
        self.b_show = IconButton("eye", "Show") if secret else None
        if self.b_show:
            self.b_show.clicked.connect(self.toggle)
            row.addWidget(self.b_show)
        if open_:
            row.addWidget(IconButton("external", "Open", fn=open_))
        if copy:
            row.addWidget(IconButton("copy", "Copy", fn=lambda: copy(self.value)))
        v.addLayout(row)
        self.setObjectName("field")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#field {{ background:{C['raised']}; border:1px solid {C['line']}; border-radius:12px; }} QWidget#field:hover {{ border-color:{C['line2']}; }}")
        self.refresh()

    def set_value(self, value):
        self.value = value or ""
        self.refresh()

    def toggle(self):
        self.shown = not self.shown
        self.b_show.set_icon("eye_off" if self.shown else "eye")
        self.b_show.setToolTip("Hide" if self.shown else "Show")
        self.refresh()

    def refresh(self):
        if self.secret and not self.shown:
            self.val.setText("•" * min(max(len(self.value), 8), 18) if self.value else "")
        else:
            self.val.setText(self.value)


class ItemDelegate(QStyledItemDelegate):
    """One row of the item list: a tile, the name, a line of detail, and small flags at the right."""
    H = 64

    def __init__(self, parent=None, health=None, logos=None):
        super().__init__(parent)
        self.health = health or (lambda e: False)
        self.logos = logos
        self.cache = {}

    def sizeHint(self, opt, idx):
        return QSize(0, self.H)

    @staticmethod
    def subtitle(e):
        k = model.kind_of(e)
        d = e.get("data") or {}
        if k == "card":
            n = model.card_digits(d.get("number", ""))
            return " · ".join(x for x in (model.card_brand(n) or "Card", ("•••• " + n[-4:]) if n else "", ("exp " + d["expiry"]) if d.get("expiry") else "") if x)
        if k == "note":
            return (e.get("notes") or "").strip().split("\n")[0][:80] or "Empty note"
        if k == "passkey":
            return f"{d.get('rp_id', '')}  ·  {e.get('username') or 'passkey'}"
        if k == "wifi":
            return dict(model.WIFI_SECURITY).get(d.get("security", "WPA"), "Wi-Fi") + (" · hidden" if d.get("hidden") else "")
        from jblol_pm import host_of
        host = host_of(e.get("url", ""))
        return f"{e['username']}  ·  {host}" if e.get("username") and host else (e.get("username") or host or e.get("group") or "")

    def paint(self, p, opt, idx):
        e = idx.data(Qt.UserRole)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = opt.rect.adjusted(2, 2, -2, -2)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        hov = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        if sel or hov:
            p.setPen(QPen(QColor("#2e458f"), 1) if sel else Qt.NoPen)
            p.setBrush(QColor(C["accent_soft"] if sel else C["raised"]))
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        kind = model.kind_of(e)
        logo = self.logos.pixmap_for(e) if (self.logos is not None and kind in ("login", "passkey")) else None
        key = (e["title"][:1].lower(), kind, e["title"], logo.cacheKey() if logo is not None else 0)
        if key not in self.cache:
            self.cache[key] = avatar_pixmap(e["title"], kind, 40, logo=logo)
        p.drawPixmap(r.left() + 12, r.top() + (r.height() - 40) // 2, self.cache[key])
        x = r.left() + 62
        right = 12 + (22 if (e.get("data") or {}).get("fav") else 0) + (22 if self.health(e) else 0)
        w = r.width() - 62 - right
        f = QFont(opt.font)
        f.setPixelSize(14)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(C["ink"]))
        p.drawText(x, r.top() + 27, QFontMetrics(f).elidedText(e["title"] or "Untitled", Qt.ElideRight, w))
        f2 = QFont(opt.font)
        f2.setPixelSize(12)
        p.setFont(f2)
        p.setPen(QColor(C["muted"]))
        p.drawText(x, r.top() + 45, QFontMetrics(f2).elidedText(self.subtitle(e), Qt.ElideRight, w))
        rx = r.right() - 12
        if self.health(e):
            rx -= 18
            p.drawPixmap(rx, r.top() + (r.height() - 16) // 2, icons.pix("alert", 16, C["warn"], 2.0))
            rx -= 4
        if (e.get("data") or {}).get("fav"):
            rx -= 18
            p.drawPixmap(rx, r.top() + (r.height() - 16) // 2, icons.pix("star_fill", 16, C["amber"], 1.4))
        p.restore()
