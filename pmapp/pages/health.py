"""Security Check: a score for your vault, and the logins that need attention (weak, reused, old, found in a breach)."""
import hashlib
import time
import urllib.request

from PySide6.QtCore import QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import icons, model
from ..theme import C
from .items import drop_layout, drop_widget
from ..widgets import VScroll, Avatar, Card, EmptyState, button, label

YEAR = 365 * 86400


class BreachWorker(QThread):
    """Asks haveibeenpwned.com whether each password has appeared in a breach. Only the first 5 characters of a SHA-1 hash leave this computer."""
    done = Signal(dict, str)

    def __init__(self, entries):
        super().__init__()
        self.entries = entries

    def run(self):
        res, err = {}, ""
        by_prefix = {}
        for e in self.entries:
            if e.get("password"):
                h = hashlib.sha1(e["password"].encode()).hexdigest().upper()
                by_prefix.setdefault(h[:5], []).append((h[5:], e))
        try:
            for prefix, rows in by_prefix.items():
                req = urllib.request.Request(f"https://api.pwnedpasswords.com/range/{prefix}", headers={"User-Agent": "PasswordManager (jblol.systems)", "Add-Padding": "true"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    seen = {}
                    for line in r.read().decode().splitlines():
                        suf, _, n = line.partition(":")
                        if n.strip() != "0":
                            seen[suf.strip()] = int(n or 0)
                for suf, e in rows:
                    if suf in seen:
                        res[(e["group"], e["title"])] = seen[suf]
        except Exception as ex:
            err = f"Couldn't reach the breach list: {ex}"
        self.done.emit(res, err)


class ScoreRing(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(150, 150)
        self.score, self.color, self.text = 100, C["good"], "Excellent"

    def set(self, score):
        self.score = score
        self.color, self.text = (C["good"], "Excellent") if score >= 90 else (C["accent"], "Good") if score >= 75 else (C["warn"], "Needs work") if score >= 50 else (C["bad"], "At risk")
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(10, 10, 130, 130)
        p.setPen(QPen(QColor(C["line2"]), 10, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(r)
        p.setPen(QPen(QColor(self.color), 10, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 90 * 16, int(-360 * 16 * self.score / 100))
        p.setPen(QColor(C["ink"]))
        f = QFont("Inter")
        f.setPixelSize(36)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 40, 150, 46), Qt.AlignCenter, str(self.score))
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor(self.color))
        p.drawText(QRectF(0, 84, 150, 20), Qt.AlignCenter, self.text)


class HealthPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.breached = {}
        self.worker = None
        self.checked = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 0)
        outer.setSpacing(6)
        outer.addWidget(label("Security Check", "h1"))
        outer.addWidget(label("Finds logins that are easy to guess, shared between sites, very old, or already leaked.", "muted", True))
        sc = VScroll()
        host = QWidget()
        self.col = QVBoxLayout(host)
        self.col.setContentsMargins(0, 14, 8, 30)
        self.col.setSpacing(14)
        sc.setWidget(host)
        outer.addWidget(sc, 1)

    def refresh(self):
        w = self.win
        while self.col.count():
            it = self.col.takeAt(0)
            if it.widget():
                drop_widget(it.widget())
            elif it.layout():
                drop_layout(it.layout())
        if not w.s.get("warn_passwords", True):
            self.col.addWidget(EmptyState("shield", "Password warnings are off", "Weak, reused and old passwords aren't being flagged. Turn them back on any time.",
                                          button("Turn warnings on", "primary", lambda: (w.set_opt("warn_passwords", True), w.settings_page.rebuild_switch("warn_passwords", True)))))
            self.col.addStretch(1)
            return
        logins = [e for e in w.entries if model.kind_of(e) == "login" and e.get("password")]
        weak = [e for e in logins if any(k == "weak" for k, _ in w.health.get((e["group"], e["title"]), []))]
        reused = [e for e in logins if any(k == "reused" for k, _ in w.health.get((e["group"], e["title"]), []))]
        old = [e for e in logins if e.get("modified") and self._age(e) > YEAR]
        leaked = [e for e in logins if (e["group"], e["title"]) in self.breached]
        no_otp = [e for e in logins if not e.get("otp") and e["group"] in ("Websites",)]
        penalty = 7 * len(weak) + 4 * len(reused) + 12 * len(leaked) + min(15, len(old))
        score = max(0, 100 - penalty) if logins else 100
        top = QHBoxLayout()
        top.setSpacing(20)
        ring = ScoreRing()
        ring.set(score)
        top.addWidget(ring)
        side = QVBoxLayout()
        side.setSpacing(6)
        t = label("Your vault is in good shape" if score >= 90 else "A few things to fix" if score >= 60 else "Some passwords put you at risk", "h2")
        side.addWidget(t)
        side.addWidget(label(f"{len(logins)} logins checked on this computer. Nothing leaves it unless you press “Check for leaks”, and then only the first 5 characters of a scrambled copy of each password are sent.", "muted", True))
        row = QHBoxLayout()
        self.b_leak = button("Checking…" if self.worker else "Check for leaks", "primary", self.check_leaks, "shield_check")
        self.b_leak.setEnabled(not self.worker and bool(logins))
        row.addWidget(self.b_leak)
        if self.checked:
            row.addWidget(label(self.checked, "faint"))
        row.addStretch(1)
        side.addLayout(row)
        side.addStretch(1)
        top.addLayout(side, 1)
        c = Card(pad=22)
        c.body.addLayout(top)
        self.col.addWidget(c)
        stats = QHBoxLayout()
        stats.setSpacing(12)
        for name, n, icon, tone in (("Weak", len(weak), "alert", "bad"), ("Reused", len(reused), "copy", "warn"), ("Leaked", len(leaked), "shield_alert", "bad"),
                                    ("Over a year old", len(old), "clock", "warn")):
            s = Card(flat=True, pad=16)
            r = QHBoxLayout()
            ic = QLabel()
            col = C["good"] if n == 0 else C[{"bad": "bad", "warn": "warn"}[tone]]
            ic.setPixmap(icons.pix(icon if n else "check_circle", 24, col, 1.9))
            r.addWidget(ic)
            tx = QVBoxLayout()
            tx.setSpacing(0)
            num = QLabel(str(n))
            num.setStyleSheet(f"font-size:24px; font-weight:700; color:{col};")
            tx.addWidget(num)
            tx.addWidget(label(name, "muted"))
            r.addLayout(tx)
            r.addStretch(1)
            s.body.addLayout(r)
            stats.addWidget(s)
        sw = QWidget()
        sw.setLayout(stats)
        stats.setContentsMargins(0, 0, 0, 0)
        self.col.addWidget(sw)
        groups = (("Leaked in a breach", leaked, "Change these first: the password is known to attackers.", lambda e: f"seen {self.breached[(e['group'], e['title'])]:,} times"),
                  ("Weak passwords", weak, "Easy to guess. Open one and press the dice to make a strong one.", lambda e: next((t for k, t in w.health[(e['group'], e['title'])] if k == 'weak'), "")),
                  ("Reused passwords", reused, "The same password protects several sites, so one leak opens them all.", lambda e: next((t for k, t in w.health[(e['group'], e['title'])] if k == 'reused'), "")),
                  ("Not changed in over a year", old, "Worth refreshing, especially for email and banking.", lambda e: f"changed {model.ago(self._ts(e))}"))
        shown = False
        for title, items, hint, why in groups:
            if not items:
                continue
            shown = True
            c = Card(f"{title}  ·  {len(items)}", hint, pad=18)
            for e in items[:12]:
                c.body.addWidget(self._row(e, why(e)))
            if len(items) > 12:
                c.body.addWidget(label(f"…and {len(items) - 12} more", "faint"))
            self.col.addWidget(c)
        if not shown:
            self.col.addWidget(EmptyState("shield_check", "Nothing needs attention", "Every login looks strong and unique."))
        if no_otp:
            self.col.addWidget(label(f"{len(no_otp)} website logins have no one-time code set up. Turning on 2-step sign-in where a site offers it is the best extra protection.", "faint", True))
        self.col.addStretch(1)

    def _row(self, e, why):
        b = QPushButton()
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(f"QPushButton {{ background:transparent; border:none; border-radius:10px; text-align:left; padding:0; }} QPushButton:hover {{ background:{C['raised']}; }}")
        h = QHBoxLayout(b)
        h.setContentsMargins(8, 7, 12, 7)
        h.setSpacing(12)
        h.addWidget(Avatar(e["title"], "login", 34))
        col = QVBoxLayout()
        col.setSpacing(0)
        n = QLabel(e["title"])
        n.setStyleSheet("font-weight:600; background:transparent;")
        col.addWidget(n)
        r = QLabel(why)
        r.setStyleSheet(f"color:{C['muted']}; font-size:12px; background:transparent;")
        col.addWidget(r)
        h.addLayout(col, 1)
        ic = QLabel()
        ic.setPixmap(icons.pix("chevron_right", 16, C["faint"], 2.0))
        h.addWidget(ic)
        b.setFixedHeight(52)
        for child in b.findChildren(QLabel):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        b.clicked.connect(lambda: self.win.open_item(e))
        return b

    @staticmethod
    def _ts(e):
        try:
            return time.mktime(time.strptime(e["modified"][:19].replace("T", " "), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, KeyError):
            return time.time()

    def _age(self, e):
        return time.time() - self._ts(e)

    def check_leaks(self):
        logins = [e for e in self.win.entries if model.kind_of(e) == "login" and e.get("password")]
        self.worker = BreachWorker(logins)
        self.worker.done.connect(self._leaks_done)
        self.worker.start()
        self.refresh()

    def _leaks_done(self, res, err):
        self.worker.wait()
        self.worker = None
        self.breached = res
        self.checked = err or f"Checked {time.strftime('%H:%M')}: " + (f"{len(res)} leaked" if res else "none found")
        core_log = self.win.log
        core_log("Security Check: " + (err or f"breach check done, {len(res)} password(s) found in known leaks"))
        self.refresh()
        self.win.update_badges()
