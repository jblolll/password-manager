"""The screen you see while the vault is locked."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import icons
from ..theme import C
from ..widgets import Pill, button, label


class LockedPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(10)
        tile = QLabel()
        tile.setFixedSize(92, 92)
        tile.setAlignment(Qt.AlignCenter)
        tile.setStyleSheet(f"background:{C['accent_soft']}; border:1px solid #2a3f7e; border-radius:26px;")
        tile.setPixmap(icons.pix("lock", 42, C["accent_hi"], 1.7))
        v.addWidget(tile, 0, Qt.AlignCenter)
        v.addSpacing(8)
        self.title = label("Your vault is locked", "h1")
        self.title.setAlignment(Qt.AlignCenter)
        self.msg = label("Plug in your USB stick to open it.", "muted", True)
        self.msg.setAlignment(Qt.AlignCenter)
        self.msg.setFixedWidth(420)
        self.err = QLabel("")
        self.err.setWordWrap(True)
        self.err.setMinimumWidth(1)
        self.err.setAlignment(Qt.AlignCenter)
        self.err.setFixedWidth(460)
        self.err.setStyleSheet(f"color:{C['bad']};")
        v.addWidget(self.title)
        v.addWidget(self.msg, 0, Qt.AlignCenter)
        v.addWidget(self.err, 0, Qt.AlignCenter)
        self.pill = Pill("USB stick not plugged in")
        v.addSpacing(6)
        v.addWidget(self.pill, 0, Qt.AlignCenter)
        v.addSpacing(10)
        v.addWidget(button("Use a key file from another place…", "ghost", win.pick_key, "key"), 0, Qt.AlignCenter)

    def set(self, title, msg, error=""):
        self.title.setText(title)
        self.msg.setText(msg)
        self.err.setText(error or "")
