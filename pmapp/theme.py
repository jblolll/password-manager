"""Colours and the style sheet. Everything the app looks like is defined here, so a design change is a change to this one file."""
import tempfile
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

from . import icons

C = dict(bg="#0a0c11", side="#0d1017", panel="#11151d", raised="#171c27", hover="#1d2331", line="#202636", line2="#2b3347",
         ink="#e9edf5", soft="#c3cad8", muted="#8b94a9", faint="#5d667b",
         accent="#5b8cff", accent_hi="#7aa2ff", accent_lo="#3f6fe6", accent_soft="#16224a",
         good="#34d399", good_soft="#0e2a22", warn="#fbbf24", warn_soft="#2a2210", bad="#f87171", bad_soft="#2c1417",
         teal="#2dd4bf", violet="#a78bfa", amber="#f59e0b", pink="#f472b6")
MONO = "'JetBrains Mono','DejaVu Sans Mono','Ubuntu Mono',monospace"
_ASSETS = Path(tempfile.gettempdir()) / f"pmapp-assets-{__import__('os').getuid()}"


def _asset(name, px):
    _ASSETS.mkdir(parents=True, exist_ok=True, mode=0o700)
    f = _ASSETS / f"{name}.png"
    px.toImage().save(str(f))
    return f.as_posix()


def qss():
    chev = _asset("chev", icons.pix("chevron_down", 14, C["muted"], 2.0, 1.0))
    chk = _asset("chk", icons.pix("check", 14, "#ffffff", 2.6, 1.0))
    return f"""
* {{ font-family: 'Inter','Ubuntu','Noto Sans',sans-serif; font-size: 13px; color: {C['ink']}; outline: none; }}
QMainWindow, QDialog {{ background: {C['bg']}; }}
QWidget#root {{ background: {C['bg']}; }}
QWidget#side {{ background: {C['side']}; border-right: 1px solid {C['line']}; }}
QWidget#topbar {{ background: {C['bg']}; }}
QWidget#listcol {{ background: {C['bg']}; }}
QWidget#detail {{ background: {C['panel']}; border: 1px solid {C['line']}; border-radius: 16px; }}
QFrame#card {{ background: {C['panel']}; border: 1px solid {C['line']}; border-radius: 14px; }}
QFrame#cardflat {{ background: {C['raised']}; border: 1px solid {C['line']}; border-radius: 12px; }}
QFrame#sep {{ background: {C['line']}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#vsep {{ background: {C['line']}; border: none; max-width: 1px; min-width: 1px; }}
QLabel {{ background: transparent; border: none; }}
QLabel[role="muted"] {{ color: {C['muted']}; }}
QLabel[role="faint"] {{ color: {C['faint']}; font-size: 12px; }}
QLabel[role="h1"] {{ font-size: 24px; font-weight: 700; letter-spacing: -0.3px; }}
QLabel[role="h2"] {{ font-size: 16px; font-weight: 650; }}
QLabel[role="h3"] {{ font-size: 14px; font-weight: 650; }}
QLabel[role="label"] {{ color: {C['muted']}; font-size: 11px; font-weight: 650; letter-spacing: 0.8px; }}
QLabel[role="section"] {{ color: {C['faint']}; font-size: 11px; font-weight: 700; letter-spacing: 1.1px; padding: 14px 12px 6px 12px; }}
QLabel[role="mono"] {{ font-family: {MONO}; font-size: 14px; }}
QLabel[role="big"] {{ font-size: 30px; font-weight: 700; letter-spacing: -0.5px; }}
QLabel#brand {{ font-size: 15px; font-weight: 700; letter-spacing: -0.2px; }}
QLabel#pill {{ border: 1px solid {C['line2']}; border-radius: 10px; padding: 2px 9px; background: {C['raised']}; color: {C['muted']}; font-size: 11.5px; font-weight: 600; }}
QLabel#pill[tone="good"] {{ color: {C['good']}; border-color: #1c5b45; background: {C['good_soft']}; }}
QLabel#pill[tone="warn"] {{ color: {C['warn']}; border-color: #5c4a1c; background: {C['warn_soft']}; }}
QLabel#pill[tone="bad"] {{ color: {C['bad']}; border-color: #5c2530; background: {C['bad_soft']}; }}
QLabel#pill[tone="accent"] {{ color: {C['accent_hi']}; border-color: #2a3f7e; background: {C['accent_soft']}; }}
QPushButton {{ background: {C['raised']}; border: 1px solid {C['line2']}; border-radius: 9px; padding: 8px 15px; font-weight: 550; }}
QPushButton:hover {{ background: {C['hover']}; border-color: #3a4560; }}
QPushButton:pressed {{ background: {C['panel']}; }}
QPushButton:disabled {{ color: {C['faint']}; background: {C['panel']}; border-color: {C['line']}; }}
QPushButton#primary {{ background: {C['accent']}; border: 1px solid {C['accent']}; color: white; font-weight: 650; }}
QPushButton#primary:hover {{ background: {C['accent_hi']}; border-color: {C['accent_hi']}; }}
QPushButton#primary:disabled {{ background: #26335e; border-color: #26335e; color: #7885b3; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {C['soft']}; }}
QPushButton#ghost:hover {{ background: {C['raised']}; }}
QPushButton#danger {{ color: {C['bad']}; background: transparent; border: 1px solid #4a2530; }}
QPushButton#danger:hover {{ background: {C['bad_soft']}; }}
QPushButton#nav {{ text-align: left; border: none; background: transparent; padding: 9px 12px; border-radius: 9px; color: {C['soft']}; font-weight: 550; }}
QPushButton#nav:hover {{ background: {C['raised']}; color: {C['ink']}; }}
QPushButton#nav[on="true"] {{ background: {C['accent_soft']}; color: {C['accent_hi']}; font-weight: 650; }}
QPushButton#chip {{ background: transparent; border: 1px solid {C['line2']}; border-radius: 14px; padding: 5px 13px; color: {C['muted']}; font-weight: 550; }}
QPushButton#chip:hover {{ color: {C['ink']}; border-color: #3a4560; }}
QPushButton#chip:checked {{ background: {C['accent_soft']}; border-color: #2e458f; color: {C['accent_hi']}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#iconbtn {{ background: transparent; border: none; border-radius: 8px; padding: 6px; }}
QToolButton#iconbtn:hover {{ background: {C['hover']}; }}
QToolButton#iconbtn:pressed {{ background: {C['raised']}; }}
QLineEdit, QPlainTextEdit, QTextEdit {{ background: {C['bg']}; border: 1px solid {C['line2']}; border-radius: 10px; padding: 9px 12px; selection-background-color: {C['accent_lo']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {C['accent']}; }}
QLineEdit:read-only, QPlainTextEdit:read-only {{ background: transparent; border-color: transparent; padding-left: 0; }}
QLineEdit[role="mono"], QPlainTextEdit[role="mono"] {{ font-family: {MONO}; }}
QLineEdit#search {{ background: {C['raised']}; border: 1px solid {C['line']}; border-radius: 11px; padding: 10px 14px 10px 6px; }}
QLineEdit#search:focus {{ border-color: {C['accent']}; background: {C['bg']}; }}
QLineEdit#bigtitle {{ font-size: 21px; font-weight: 700; padding: 6px 10px; }}
QComboBox {{ background: {C['bg']}; border: 1px solid {C['line2']}; border-radius: 10px; padding: 8px 12px; min-height: 18px; }}
QComboBox:focus, QComboBox:on {{ border-color: {C['accent']}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({chev}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {C['panel']}; border: 1px solid {C['line2']}; border-radius: 8px; padding: 4px; selection-background-color: {C['accent_soft']}; outline: none; }}
QSpinBox {{ background: {C['bg']}; border: 1px solid {C['line2']}; border-radius: 10px; padding: 7px 10px; min-width: 120px; }}
QSpinBox:focus {{ border-color: {C['accent']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
QCheckBox {{ spacing: 9px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {C['line2']}; border-radius: 5px; background: {C['bg']}; }}
QCheckBox::indicator:hover {{ border-color: {C['accent']}; }}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; image: url({chk}); }}
QSlider::groove:horizontal {{ height: 5px; background: {C['line2']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {C['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #ffffff; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; border: 2px solid {C['accent']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {C['line2']}; border-radius: 4px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4560; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QScrollBar:horizontal {{ height: 0; }}
QListWidget, QListView {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border: none; }}
QMenu {{ background: {C['panel']}; border: 1px solid {C['line2']}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 8px 16px 8px 10px; border-radius: 7px; }}
QMenu::item:selected {{ background: {C['accent_soft']}; color: {C['accent_hi']}; }}
QMenu::separator {{ height: 1px; background: {C['line']}; margin: 5px 6px; }}
QToolTip {{ background: {C['raised']}; border: 1px solid {C['line2']}; color: {C['ink']}; padding: 5px 8px; border-radius: 6px; }}
QProgressBar {{ background: {C['bg']}; border: 1px solid {C['line']}; border-radius: 5px; max-height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 4px; }}
QDialog QLabel[role="muted"] {{ color: {C['muted']}; }}
"""


def apply(app):
    app.setStyleSheet(qss())
    f = QFont("Inter")
    f.setPixelSize(13)
    app.setFont(f)
    for fam in ("Inter",):
        QFontDatabase.families()
