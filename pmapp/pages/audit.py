"""Audit log: everything the vault and your USB stick did, newest first, with filters and CSV export."""
import csv
import time

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from .. import icons, model
from ..theme import C
from ..widgets import EmptyState, Switch, button, label

CAT = {"stick": ("usb", C["accent_hi"]), "vault": ("lock", C["violet"]), "device": ("laptop", C["teal"]), "approve": ("shield_check", C["good"]),
       "sync": ("refresh", C["muted"]), "browser": ("globe", C["pink"]), "share": ("share", C["amber"])}


class Delegate(QStyledItemDelegate):
    def sizeHint(self, opt, idx):
        return QSize(0, 34 if idx.data(Qt.UserRole + 1) == "head" else 54)

    def paint(self, p, opt, idx):
        e = idx.data(Qt.UserRole)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = opt.rect
        if idx.data(Qt.UserRole + 1) == "head":
            f = QFont(opt.font)
            f.setPixelSize(11)
            f.setBold(True)
            f.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
            p.setFont(f)
            p.setPen(QColor(C["faint"]))
            p.drawText(r.adjusted(6, 10, 0, 0), Qt.AlignLeft | Qt.AlignTop, e["text"].upper())
            p.restore()
            return
        if opt.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C["raised"]))
            p.drawRoundedRect(QRectF(r).adjusted(0, 2, 0, -2), 10, 10)
        name, col = CAT.get(e["cat"], CAT["vault"])
        tint = QColor(col)
        bg = QColor(tint.red(), tint.green(), tint.blue(), 34)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(r.left() + 8, r.top() + 10, 34, 34), 10, 10)
        p.drawPixmap(r.left() + 17, r.top() + 19, icons.pix(name, 16, col, 2.0))
        f = QFont(opt.font)
        f.setPixelSize(13)
        p.setFont(f)
        p.setPen(QColor(C["ink"]))
        right = 150
        p.drawText(r.left() + 54, r.top() + 24, QFontMetrics(f).elidedText(e["text"], Qt.ElideRight, r.width() - 54 - right))
        f2 = QFont(opt.font)
        f2.setPixelSize(11.5)
        p.setFont(f2)
        p.setPen(QColor(C["muted"]))
        src = "This app" if e["src"] == "app" else "System log"
        p.drawText(r.left() + 54, r.top() + 41, f"{dict(model.CATEGORIES).get(e['cat'], 'Vault')}  ·  {src}")
        p.setFont(f2)
        p.drawText(QRectF(r.right() - right, r.top() + 10, right - 12, 34), Qt.AlignRight | Qt.AlignVCenter, time.strftime("%H:%M:%S", time.localtime(e["ts"])))
        p.restore()


class AuditPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.cat = None
        self.events = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 20)
        outer.setSpacing(6)
        top = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(label("Audit log", "h1"))
        col.addWidget(label("Every plug-in, lock, unlock, approval and sync. Passwords are never written to the log.", "muted", True))
        top.addLayout(col, 1)
        top.addWidget(button("Export CSV", "", self.export, "download"), 0, Qt.AlignTop)
        top.addWidget(button("Clear", "ghost", self.clear, "trash"), 0, Qt.AlignTop)
        outer.addLayout(top)
        outer.addSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.chips = {}
        for key, text in [(None, "All")] + list(model.CATEGORIES):
            b = button(text, "chip", lambda k=key: self.set_cat(k))
            b.setCheckable(True)
            b.setChecked(key is None)
            self.chips[key] = b
            bar.addWidget(b)
        bar.addStretch(1)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search the log")
        self.search.setFixedWidth(240)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.paint)
        bar.addWidget(self.search)
        outer.addLayout(bar)
        outer.addSpacing(4)
        self.list = QListWidget()
        self.list.setItemDelegate(Delegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setSelectionMode(QListWidget.NoSelection)
        outer.addWidget(self.list, 1)
        self.empty = EmptyState("list", "Nothing logged yet", "Plug in the stick, lock the vault or sync, and it shows up here.")
        outer.addWidget(self.empty, 1)
        foot = QHBoxLayout()
        self.foot = label("", "faint")
        foot.addWidget(self.foot, 1)
        foot.addWidget(label("Include system approvals (sudo, lock screen)", "faint"))
        self.sw = Switch(win.s.get("audit_journal", True))
        self.sw.toggled.connect(self._journal)
        foot.addWidget(self.sw)
        outer.addLayout(foot)

    def _journal(self, on):
        self.win.s["audit_journal"] = on
        self.win.save()
        self.refresh()

    def set_cat(self, cat):
        self.cat = cat
        for k, b in self.chips.items():
            b.setChecked(k == cat)
        self.paint()

    def refresh(self):
        import jblol_pm as core
        self.events = model.audit_events(core.EVENT_LOG, with_journal=bool(self.win.s.get("audit_journal", True)))
        self.paint()

    def paint(self, *_):
        q = self.search.text().strip().lower()
        evs = [e for e in reversed(self.events) if (self.cat is None or e["cat"] == self.cat) and (not q or q in e["text"].lower())]
        self.list.clear()
        last = None
        today = time.strftime("%Y-%m-%d")
        yday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        for e in evs[:600]:
            day = time.strftime("%Y-%m-%d", time.localtime(e["ts"]))
            if day != last:
                last = day
                name = "Today" if day == today else "Yesterday" if day == yday else time.strftime("%A, %B %d", time.localtime(e["ts"]))
                it = QListWidgetItem()
                it.setData(Qt.UserRole, {"text": name})
                it.setData(Qt.UserRole + 1, "head")
                it.setFlags(Qt.NoItemFlags)
                self.list.addItem(it)
            it = QListWidgetItem()
            it.setData(Qt.UserRole, e)
            it.setFlags(Qt.ItemIsEnabled)
            self.list.addItem(it)
        self.list.setVisible(bool(evs))
        self.empty.setVisible(not evs)
        n_today = sum(1 for e in self.events if time.strftime("%Y-%m-%d", time.localtime(e["ts"])) == today)
        self.foot.setText(f"{len(evs)} shown  ·  {n_today} today" + ("  ·  showing the newest 600" if len(evs) > 600 else ""))

    def export(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export the audit log", f"audit-log-{time.strftime('%Y%m%d')}.csv", "CSV (*.csv)")
        if not f:
            return
        with open(f, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "category", "source", "event"])
            for e in self.events:
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["ts"])), e["cat"], e["src"], e["text"]])
        self.win.toast(f"Exported {len(self.events)} events", "good")

    def clear(self):
        import jblol_pm as core
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(self, "Clear the log", "Clear this app's activity log? System approvals stay in the system journal.") == QMessageBox.Yes:
            try:
                core.EVENT_LOG.write_text("")
            except OSError:
                pass
            self.refresh()
