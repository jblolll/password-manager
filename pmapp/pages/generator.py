"""Password generator: random, passphrase and PIN, a big colour-coded result, a strength bar and a short history."""
import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QSlider, QStackedWidget, QVBoxLayout, QWidget, QComboBox, QFrame)

from .. import model
from ..theme import C, MONO
from ..widgets import VScroll, Card, IconButton, SettingRow, StrengthBar, Switch, button, hline, label
import jblol_pm as core


def colorize(pw):
    out = []
    for ch in pw:
        col = C["accent_hi"] if ch.isdigit() else C["pink"] if not ch.isalnum() else C["ink"]
        out.append(f"<span style='color:{col}'>{html.escape(ch)}</span>")
    return "".join(out)


class GeneratorPanel(QWidget):
    """The generator itself. Emits `picked(password)` when Use is pressed (only shown when use_button=True)."""
    picked = Signal(str)
    generated = Signal(str)

    def __init__(self, win=None, use_button=False):
        super().__init__()
        self.win = win
        self.mode = "random"
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        modes = QHBoxLayout()
        modes.setSpacing(8)
        self.chips = {}
        for key, text in (("random", "Random password"), ("phrase", "Passphrase"), ("pin", "PIN")):
            b = button(text, "chip", lambda k=key: self.set_mode(k))
            b.setCheckable(True)
            self.chips[key] = b
            modes.addWidget(b)
        modes.addStretch(1)
        v.addLayout(modes)

        out = QFrame()
        out.setObjectName("cardflat")
        ol = QVBoxLayout(out)
        ol.setContentsMargins(20, 18, 20, 14)
        ol.setSpacing(10)
        self.out = QLabel()
        self.out.setTextFormat(Qt.RichText)
        self.out.setWordWrap(True)
        self.out.setMinimumWidth(1)
        self.out.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.out.setStyleSheet(f"font-family:{MONO}; font-size:22px; letter-spacing:1px;")
        self.out.setMinimumHeight(40)
        ol.addWidget(self.out)
        self.meter = StrengthBar()
        ol.addWidget(self.meter)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(button("New password", "", self.make, "refresh"))
        row.addWidget(button("Copy", "", self.copy, "copy"))
        if use_button:
            row.addWidget(button("Use this password", "primary", lambda: self.picked.emit(self.pw)))
        row.addStretch(1)
        ol.addLayout(row)
        v.addWidget(out)

        self.opts = QStackedWidget()
        v.addWidget(self.opts)
        # random
        rw = QWidget()
        rl = QVBoxLayout(rw)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        self.len, self.len_lbl = QSlider(Qt.Horizontal), label("20", "h3")
        self.len.setRange(8, 128)
        self.len.setValue(20)
        lr = QHBoxLayout()
        lr.addWidget(label("Length", "h3"))
        lr.addSpacing(12)
        lr.addWidget(self.len, 1)
        lr.addSpacing(8)
        self.len_lbl.setMinimumWidth(34)
        lr.addWidget(self.len_lbl)
        rl.addLayout(lr)
        self.sw = {}
        for key, title, text, on in (("upper", "Capital letters", "A–Z", True), ("lower", "Lowercase letters", "a–z", True), ("digits", "Numbers", "0–9", True),
                                     ("symbols", "Symbols", "! @ # $ % & *", True), ("sim", "Avoid look-alikes", "No O/0, l/1/I", True)):
            s = Switch(on)
            s.toggled.connect(self.make)
            self.sw[key] = s
            rl.addWidget(hline())
            rl.addWidget(SettingRow(title, text, s))
        self.opts.addWidget(rw)
        # passphrase
        pw_ = QWidget()
        pl = QVBoxLayout(pw_)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)
        self.words, self.words_lbl = QSlider(Qt.Horizontal), label("5", "h3")
        self.words.setRange(3, 10)
        self.words.setValue(5)
        wr = QHBoxLayout()
        wr.addWidget(label("Words", "h3"))
        wr.addSpacing(12)
        wr.addWidget(self.words, 1)
        wr.addSpacing(8)
        self.words_lbl.setMinimumWidth(34)
        wr.addWidget(self.words_lbl)
        pl.addLayout(wr)
        self.sep = QComboBox()
        for text, ch in (("Hyphen  -", "-"), ("Dot  .", "."), ("Space", " "), ("Underscore  _", "_"), ("None", "")):
            self.sep.addItem(text, ch)
        self.sep.currentIndexChanged.connect(self.make)
        pl.addWidget(hline())
        pl.addWidget(SettingRow("Separator", "Between the words", self.sep))
        self.cap, self.num = Switch(True), Switch(False)
        for sw in (self.cap, self.num):
            sw.toggled.connect(self.make)
        pl.addWidget(hline())
        pl.addWidget(SettingRow("Capitalize", "Start each word with a capital", self.cap))
        pl.addWidget(hline())
        pl.addWidget(SettingRow("Add a number", "One word ends in a two-digit number", self.num))
        self.opts.addWidget(pw_)
        # pin
        pn = QWidget()
        nl = QVBoxLayout(pn)
        nl.setContentsMargins(0, 0, 0, 0)
        self.digits, self.digits_lbl = QSlider(Qt.Horizontal), label("6", "h3")
        self.digits.setRange(4, 12)
        self.digits.setValue(6)
        dr = QHBoxLayout()
        dr.addWidget(label("Digits", "h3"))
        dr.addSpacing(12)
        dr.addWidget(self.digits, 1)
        dr.addSpacing(8)
        self.digits_lbl.setMinimumWidth(34)
        dr.addWidget(self.digits_lbl)
        nl.addLayout(dr)
        nl.addStretch(1)
        self.opts.addWidget(pn)
        self.len.valueChanged.connect(lambda n: (self.len_lbl.setText(str(n)), self.make()))
        self.words.valueChanged.connect(lambda n: (self.words_lbl.setText(str(n)), self.make()))
        self.digits.valueChanged.connect(lambda n: (self.digits_lbl.setText(str(n)), self.make()))
        self.pw = ""
        self.set_mode("random")

    def set_mode(self, mode):
        self.mode = mode
        for k, b in self.chips.items():
            b.setChecked(k == mode)
        self.opts.setCurrentIndex(("random", "phrase", "pin").index(mode))
        self.make()

    def make(self, *_):
        if self.mode == "random":
            c = self.sw
            if not any(c[k].isChecked() for k in ("upper", "lower", "digits", "symbols")):
                c["lower"].blockSignals(True)
                c["lower"].setChecked(True)
                c["lower"].blockSignals(False)
            self.pw = core.generate(self.len.value(), c["lower"].isChecked(), c["upper"].isChecked(), c["digits"].isChecked(), c["symbols"].isChecked(), c["sim"].isChecked())
        elif self.mode == "phrase":
            try:
                self.pw = model.passphrase(self.words.value(), self.sep.currentData(), self.cap.isChecked(), self.num.isChecked())
            except ValueError:
                self.pw = core.generate(24)
        else:
            self.pw = model.pin(self.digits.value())
        self.out.setText(colorize(self.pw))
        if self.mode == "pin":
            self.meter.level, self.meter.text, self.meter.color = 1, "PINs are only as safe as the lock that limits guesses", C["warn"]
            self.meter.update()
        else:
            self.meter.set_password(self.pw)
        self.generated.emit(self.pw)

    def copy(self):
        if self.win:
            self.win.copy_secret(self.pw)
        else:
            QApplication.clipboard().setText(self.pw)


class GeneratorDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.setWindowTitle("Generate a password")
        self.setMinimumWidth(600)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 22)
        v.setSpacing(12)
        v.addWidget(label("Generate a password", "h2"))
        self.g = GeneratorPanel(win, use_button=True)
        self.g.picked.connect(self._pick)
        v.addWidget(self.g)
        self.result = None

    def _pick(self, pw):
        self.result = pw
        self.accept()


class GeneratorPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.history = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 20)
        outer.setSpacing(6)
        outer.addWidget(label("Generator", "h1"))
        outer.addWidget(label("Makes strong random passwords on this computer. Nothing is sent anywhere, and nothing is saved until you put it in an item.", "muted", True))
        outer.addSpacing(12)
        body = QHBoxLayout()
        body.setSpacing(20)
        sc = VScroll()
        host = QWidget()
        hv = QVBoxLayout(host)
        hv.setContentsMargins(0, 0, 8, 0)
        self.panel = GeneratorPanel(win)
        hv.addWidget(self.panel)
        hv.addStretch(1)
        sc.setWidget(host)
        body.addWidget(sc, 3)
        side = Card("Recent", "This session only. Cleared when you close the app.", pad=18)
        self.hist_box = QVBoxLayout()
        self.hist_box.setSpacing(2)
        side.body.addLayout(self.hist_box)
        self.hist_empty = label("Nothing yet.", "faint")
        side.body.addWidget(self.hist_empty)
        side.body.addSpacing(6)
        side.body.addWidget(button("Save as a login…", "primary", self.save_login, "plus"))
        side.body.addStretch(1)
        side.setFixedWidth(300)
        body.addWidget(side, 0, Qt.AlignTop)
        outer.addLayout(body, 1)
        self.panel.generated.connect(self._track)
        self._last = None

    def _track(self, pw):
        # a new password counts as "recent" only once it is copied or the next one replaces it
        if self._last and self._last != pw:
            self.history = [self._last] + [p for p in self.history if p != self._last][:7]
            self._paint_hist()
        self._last = pw

    def _paint_hist(self):
        while self.hist_box.count():
            w = self.hist_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.hist_empty.setVisible(not self.history)
        for pw in self.history:
            row = QHBoxLayout()
            t = QLabel(pw if len(pw) <= 22 else pw[:20] + "…")
            t.setStyleSheet(f"font-family:{MONO}; font-size:12.5px; color:{C['soft']};")
            row.addWidget(t, 1)
            row.addWidget(IconButton("copy", "Copy", 16, fn=lambda p=pw: self.win.copy_secret(p)))
            w = QWidget()
            w.setLayout(row)
            self.hist_box.addWidget(w)

    def save_login(self):
        self.win.new_item("login", password=self.panel.pw)
