"""Line icons drawn with QPainter on a 24x24 grid (this computer has no SVG module, and a drawn icon stays sharp at any size and colour).
`pix(name, size, color)` -> QPixmap, `icon(name, color, size)` -> QIcon."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# ops: M x y | L x y | H x | V y | Q cx cy x y | C c1x c1y c2x c2y x y | Z | E cx cy rx ry | R x y w h r | A x y w h start span | EF (filled dot) cx cy r | P (filled polygon follows) ...
_ICONS = {
    "lock": "R 4.5 10.5 15 10.5 2.5  A 8 3 8 8 0 180  M 8 7 L 8 10.5  M 16 7 L 16 10.5  EF 12 15.5 1.2",
    "unlock": "R 4.5 10.5 15 10.5 2.5  A 8 3 8 8 20 160  M 8 7 L 8 10.5  EF 12 15.5 1.2",
    "key": "E 7.5 15.5 4 4  M 10.5 12.5 L 20 3  M 16.5 6.5 L 19.5 9.5  M 13.5 9.5 L 15.8 11.8",
    "card": "R 2.5 5 19 14 2.5  M 2.5 10 L 21.5 10  M 6 15 L 10 15",
    "note": "R 5 3 14 18 2.5  M 9 8 L 15 8  M 9 12 L 15 12  M 9 16 L 12.5 16",
    "wifi": "A 7 13 10 10 45 90  A 3.5 9.5 17 17 45 90  A 0 6 24 24 45 90  EF 12 19.2 1.3",
    "usb": "M 12 4 L 12 18  E 12 20 2 2  M 12 10 L 7 13 L 7 15  E 7 16.2 1.2 1.2  M 12 8 L 17 11 L 17 12.5  R 15.8 12.5 2.4 2.4 0  M 10.5 5.5 L 12 3 L 13.5 5.5 Z",
    "shield": "M 12 3 L 19.5 6 L 19.5 11.5 C 19.5 16 16.5 19.5 12 21 C 7.5 19.5 4.5 16 4.5 11.5 L 4.5 6 Z",
    "shield_check": "M 12 3 L 19.5 6 L 19.5 11.5 C 19.5 16 16.5 19.5 12 21 C 7.5 19.5 4.5 16 4.5 11.5 L 4.5 6 Z  M 8.5 12 L 11 14.5 L 15.5 9.5",
    "shield_alert": "M 12 3 L 19.5 6 L 19.5 11.5 C 19.5 16 16.5 19.5 12 21 C 7.5 19.5 4.5 16 4.5 11.5 L 4.5 6 Z  M 12 8.5 L 12 13  EF 12 15.7 0.8",
    "search": "E 10.5 10.5 6.5 6.5  M 15.5 15.5 L 21 21",
    "plus": "M 12 5 L 12 19  M 5 12 L 19 12",
    "minus": "M 5 12 L 19 12",
    "copy": "M 9 9 L 9 9 R 9 9 11 11 2  M 15 9 L 15 6 Q 15 4 13 4 L 6 4 Q 4 4 4 6 L 4 13 Q 4 15 6 15 L 9 15",
    "eye": "M 2 12 C 5 6 9 5 12 5 C 15 5 19 6 22 12 C 19 18 15 19 12 19 C 9 19 5 18 2 12 Z  E 12 12 3 3",
    "eye_off": "M 2 12 C 5 6 9 5 12 5 C 15 5 19 6 22 12 C 19 18 15 19 12 19 C 9 19 5 18 2 12 Z  E 12 12 3 3  M 4 4 L 20 20",
    "edit": "M 4 20 L 4 16 L 15 5 L 19 9 L 8 20 Z  M 13 7 L 17 11",
    "trash": "M 4 7 L 20 7  M 9 7 L 9 4.5 L 15 4.5 L 15 7  M 6 7 L 7 20 L 17 20 L 18 7  M 10 11 L 10 16  M 14 11 L 14 16",
    "share": "E 6 12 2.5 2.5  E 18 6 2.5 2.5  E 18 18 2.5 2.5  M 8.2 10.8 L 15.8 7.2  M 8.2 13.2 L 15.8 16.8",
    "clock": "E 12 12 9 9  M 12 7 L 12 12 L 15.5 14",
    "refresh": "A 4 4 16 16 40 280  M 19.5 3.5 L 19.5 8.5 L 14.5 8.5",
    "check": "M 5 12.5 L 10 17.5 L 19 7.5",
    "check_circle": "E 12 12 9 9  M 8 12.5 L 11 15.5 L 16 9.5",
    "x": "M 6 6 L 18 18  M 18 6 L 6 18",
    "chevron_right": "M 9 5 L 16 12 L 9 19",
    "chevron_down": "M 5 9 L 12 16 L 19 9",
    "chevron_up": "M 5 15 L 12 8 L 19 15",
    "laptop": "R 4.5 5 15 11 1.5  M 1.5 19 L 22.5 19",
    "desktop": "R 3 4 18 12 2  M 9 20 L 15 20  M 12 16 L 12 20",
    "phone": "R 7 2.5 10 19 2.5  M 10.5 18.5 L 13.5 18.5",
    "list": "M 8 6 L 20 6  M 8 12 L 20 12  M 8 18 L 20 18  EF 4 6 1  EF 4 12 1  EF 4 18 1",
    "bolt": "M 13 2.5 L 5 13.5 L 11.5 13.5 L 10.5 21.5 L 19 10 L 12.5 10 Z",
    "external": "M 14 4 L 20 4 L 20 10  M 20 4 L 11 13  M 18 14 L 18 18 Q 18 20 16 20 L 6 20 Q 4 20 4 18 L 4 8 Q 4 6 6 6 L 10 6",
    "folder": "M 3 7 Q 3 5.5 4.5 5.5 L 9 5.5 L 11.5 8 L 19.5 8 Q 21 8 21 9.5 L 21 18 Q 21 19.5 19.5 19.5 L 4.5 19.5 Q 3 19.5 3 18 Z",
    "alert": "M 12 4 L 21 19 L 3 19 Z  M 12 10 L 12 14  EF 12 16.7 0.8",
    "info": "E 12 12 9 9  M 12 11 L 12 16  EF 12 8 0.9",
    "download": "M 12 4 L 12 15  M 7 10.5 L 12 15.5 L 17 10.5  M 5 20 L 19 20",
    "upload": "M 12 15.5 L 12 4.5  M 7 9.5 L 12 4.5 L 17 9.5  M 5 20 L 19 20",
    "user": "E 12 8 4 4  M 4 20 Q 4 14 12 14 Q 20 14 20 20",
    "globe": "E 12 12 9 9  M 3 12 L 21 12  E 12 12 4 9",
    "dice": "R 4 4 16 16 3.5  EF 9 9 1.2  EF 15 9 1.2  EF 9 15 1.2  EF 15 15 1.2  EF 12 12 1.2",
    "qr": "R 3 3 7 7 1.2  R 14 3 7 7 1.2  R 3 14 7 7 1.2  M 14 14 L 17 14 L 17 17  M 21 14 L 21 17  M 14 21 L 21 21  M 17 19 L 17 21",
    "dots": "EF 5 12 1.6  EF 12 12 1.6  EF 19 12 1.6",
    "power": "M 12 3 L 12 11  M 7 6.5 A 4 4 16 16 -50 280",
    "arrow_right": "M 5 12 L 19 12  M 13 6 L 19 12 L 13 18",
    "sparkle": "M 12 3 L 13.8 9.2 L 20 11 L 13.8 12.8 L 12 19 L 10.2 12.8 L 4 11 L 10.2 9.2 Z",
    "id": "R 3 5 18 14 2.5  E 9 11 2 2  M 6 16 Q 9 13 12 16  M 14.5 10 L 18 10  M 14.5 14 L 18 14",
    "vault": "R 3.5 4.5 17 15 2.5  E 12 12 3.2 3.2  M 12 8.8 L 12 6.8  M 12 17.2 L 12 15.2  M 8.8 12 L 6.8 12  M 17.2 12 L 15.2 12  M 6 19.5 L 6 21  M 18 19.5 L 18 21",
}

_cache = {}


def _gear():
    p = QPainterPath()
    n = 8
    pts = []
    for i in range(n * 2):
        a0 = math.radians(i * 180 / n - 90 / n)
        a1 = math.radians((i + 1) * 180 / n - 90 / n)
        r = 9.2 if i % 2 == 0 else 6.8
        pts += [(12 + r * math.cos(a0 + 0.07), 12 + r * math.sin(a0 + 0.07)), (12 + r * math.cos(a1 - 0.07), 12 + r * math.sin(a1 - 0.07))]
    p.moveTo(*pts[0])
    for x, y in pts[1:]:
        p.lineTo(x, y)
    p.closeSubpath()
    p.addEllipse(QPointF(12, 12), 3, 3)
    return p


def _star(filled):
    p = QPainterPath()
    pts = [(12 + (9.5 if i % 2 == 0 else 4.2) * math.cos(math.radians(-90 + i * 36)), 12.5 + (9.5 if i % 2 == 0 else 4.2) * math.sin(math.radians(-90 + i * 36))) for i in range(10)]
    p.moveTo(*pts[0])
    for x, y in pts[1:]:
        p.lineTo(x, y)
    p.closeSubpath()
    return p


def _path(name):
    if name == "gear":
        return _gear(), []
    if name in ("star", "star_fill"):
        return _star(name == "star_fill"), []
    spec = _ICONS[name].split()
    path, dots = QPainterPath(), []
    i = 0

    def num(k):
        return float(spec[i + k])
    while i < len(spec):
        op = spec[i]
        if op == "M":
            path.moveTo(num(1), num(2)); i += 3
        elif op == "L":
            path.lineTo(num(1), num(2)); i += 3
        elif op == "Q":
            path.quadTo(num(1), num(2), num(3), num(4)); i += 5
        elif op == "C":
            path.cubicTo(num(1), num(2), num(3), num(4), num(5), num(6)); i += 7
        elif op == "Z":
            path.closeSubpath(); i += 1
        elif op == "E":
            path.addEllipse(QPointF(num(1), num(2)), num(3), num(4)); i += 5
        elif op == "R":
            r = num(5)
            path.addRoundedRect(QRectF(num(1), num(2), num(3), num(4)), r, r); i += 6
        elif op == "A":
            rect = QRectF(num(1), num(2), num(3), num(4))
            path.arcMoveTo(rect, num(5)); path.arcTo(rect, num(5), num(6)); i += 7
        elif op == "EF":
            dots.append((num(1), num(2), num(3))); i += 4
        else:
            raise ValueError(f"icon {name}: bad op {op}")
    return path, dots


def pix(name, size=20, color="#e9edf5", width=1.8, dpr=2.0):
    key = (name, size, str(color), width)
    if key in _cache:
        return _cache[key]
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    col = QColor(color)
    pen = QPen(col, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    path, dots = _path(name)
    if name == "star_fill":
        p.setBrush(col)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(col)
    for x, y, r in dots:
        p.drawEllipse(QPointF(x, y), r, r)
    p.end()
    _cache[key] = px
    return px


def icon(name, color="#e9edf5", size=20, width=1.8):
    return QIcon(pix(name, size, color, width))


NAMES = sorted(list(_ICONS) + ["gear", "star", "star_fill"])
