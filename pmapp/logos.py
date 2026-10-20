"""Site logos for the item list. Each logo is the site's own icon, fetched from that site (never from a third-party icon service, which would be told
which sites you have accounts on), shrunk, and kept on this computer. If a site has none, the item keeps its coloured letter."""
import ipaddress
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QThread, Qt, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

CACHE = Path(os.environ.get("JBLOL_PM_CACHE") or (Path.home() / ".cache" / "jblol-password-manager")) / "logos"
RETRY_S = 7 * 86400                 # a site with no usable icon is asked again after a week
HTML_MAX, ICON_MAX = 150_000, 400_000
SIZE = 96
UA = "Mozilla/5.0 (X11; Linux x86_64) PasswordManager-logo-fetch"


# ------------------------------------------------------------------ which site is an item for?
def host_for(e):
    """The website an item belongs to ('github.com'), or '' (apps, ssh servers, plain names, IP addresses and local names have no logo)."""
    d = e.get("data") or {}
    url = d.get("rp_id") or e.get("url") or ""
    url = url.strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return ""
    host = u.hostname.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if "." not in host or host.endswith((".local", ".lan", ".internal", ".home", ".localdomain")) or not re.fullmatch(r"[a-z0-9.-]+", host):
        return ""
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        return host


# ------------------------------------------------------------------ finding the icon in a page
class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.icons, self.base = [], None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "base" and a.get("href") and self.base is None:
            self.base = a["href"]
        elif tag == "link" and a.get("href"):
            rel = a.get("rel", "").lower().split()
            if "icon" in rel or "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
                size = 0
                m = re.match(r"(\d+)x(\d+)", a.get("sizes", "").lower())
                if m:
                    size = int(m.group(1))
                self.icons.append((("apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel), size, a["href"], a.get("type", "")))


def pick_icons(html, page_url):
    """Icon addresses from a page, best first: the big touch icons, then the largest others (pictures before SVG), then /favicon.ico."""
    p = _Links()
    try:
        p.feed(html[:HTML_MAX])
    except Exception:
        pass
    base = urllib.parse.urljoin(page_url, p.base) if p.base else page_url
    ranked = []
    for touch, size, href, typ in p.icons:
        url = urllib.parse.urljoin(base, href)
        if urllib.parse.urlparse(url).scheme not in ("http", "https"):
            continue
        svg = url.lower().split("?")[0].endswith(".svg") or "svg" in typ
        ranked.append((0 if touch else 1, 1 if svg else 0, -size, url))
    out = []
    for *_, url in sorted(ranked):
        if url not in out:
            out.append(url)
    fav = urllib.parse.urljoin(page_url, "/favicon.ico")
    if fav not in out:
        out.append(fav)
    return out


# ------------------------------------------------------------------ fetching safely
def is_public(host):
    """True only if every address the name points to is a public internet address (never this computer, your network or a cloud metadata address)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


class _Redirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private):
        self.allow_private = allow_private
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        u = urllib.parse.urlparse(newurl)
        if self.count > 4 or u.scheme not in (("http", "https") if self.allow_private else ("https",)) or not u.hostname or not (self.allow_private or is_public(u.hostname)):
            raise urllib.error.URLError("redirect refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit, allow_private=False, timeout=6):
    """The first `limit` bytes at an https address, or None. Refuses anything that points inside your own network."""
    u = urllib.parse.urlparse(url)
    if u.scheme not in (("http", "https") if allow_private else ("https",)) or not u.hostname or not (allow_private or is_public(u.hostname)):
        return None
    opener = urllib.request.build_opener(_Redirect(allow_private))
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"}), timeout=timeout) as r:
            data = r.read(limit + 1)
            return (data, r.geturl()) if len(data) <= limit else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def decode_icon(data):
    """A QImage at most 96 px from icon bytes (png, jpg, gif, svg or a multi-size .ico: the largest picture is used), or None."""
    ba = QByteArray(data)                               # (kept in a name: the buffer only borrows it)
    buf = QBuffer(ba)
    buf.open(QIODevice.ReadOnly)
    r = QImageReader(buf)
    best = None
    for i in range(max(1, r.imageCount())):
        if r.imageCount() > 1:
            r.jumpToImage(i)
        img = r.read()
        if not img.isNull() and (best is None or img.width() > best.width()):
            best = img
        if r.imageCount() <= 1:
            break
    if best is None or best.width() < 16 or best.height() < 16:
        return None
    best = best.convertToFormat(QImage.Format_ARGB32)
    return best.scaled(SIZE, SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _parent(host):
    """The main domain of a subdomain ('account.live.com' -> 'live.com'), or None."""
    if ":" in host or re.fullmatch(r"[\d.]+", host):
        return None
    try:
        from jblol_pm import reg_domain
        p = reg_domain(host)
    except Exception:
        p = ".".join(host.split(".")[-2:])
    return p if p and p != host else None


def find_logo(host, allow_private=False):
    """Download the icon for a site into a QImage, or None. Tries the site's own page first, then its main domain."""
    tried = set()
    for h in (host, _parent(host)):
        if not h or h in tried:
            continue
        tried.add(h)
        scheme = "http" if allow_private else "https"
        page = fetch(f"{scheme}://{h}/", HTML_MAX, allow_private)
        urls = pick_icons(page[0].decode("utf-8", "replace"), page[1], ) if page else [f"{scheme}://{h}/favicon.ico"]
        for url in urls[:4]:
            got = fetch(url, ICON_MAX, allow_private)
            if got:
                img = decode_icon(got[0])
                if img is not None:
                    return img
    return None


# ------------------------------------------------------------------ the store the window uses
class _Worker(QThread):
    got = Signal(str, bool)

    def __init__(self, store):
        super().__init__()
        self.store = store

    def run(self):
        while True:
            with self.store.lock:
                if not self.store.queue:
                    return
                host = self.store.queue.pop(0)
            img = find_logo(host, self.store.allow_private)
            try:
                CACHE.mkdir(parents=True, exist_ok=True)
                if img is not None:
                    img.save(str(CACHE / f"{host}.png"), "PNG")
                else:
                    (CACHE / f"{host}.none").write_text(str(int(time.time())))
            except OSError:
                pass
            self.got.emit(host, img is not None)
            time.sleep(0.15)


class LogoStore(QObject):
    ready = Signal(str)                         # a logo for this site is now on disk

    def __init__(self, enabled=True, allow_private=False):
        super().__init__()
        self.enabled = enabled
        self.allow_private = allow_private      # (tests only)
        self.mem = {}                           # host -> QPixmap, or False for "no logo"
        self.queue, self.lock = [], threading.Lock()
        self.worker = None

    def pixmap_for(self, e):
        """The logo for an item, or None (while it is still being fetched, or if the site has none)."""
        if not self.enabled:
            return None
        host = host_for(e)
        if not host:
            return None
        if host in self.mem:
            return self.mem[host] or None
        png = CACHE / f"{host}.png"
        if png.exists():
            px = QPixmap(str(png))
            self.mem[host] = px if not px.isNull() else False
            return self.mem[host] or None
        none = CACHE / f"{host}.none"
        try:
            if none.exists() and time.time() - none.stat().st_mtime < RETRY_S:
                self.mem[host] = False
                return None
        except OSError:
            pass
        with self.lock:
            if host not in self.queue:
                self.queue.append(host)
        self._start()
        return None

    def _start(self):
        if self.worker is None or not self.worker.isRunning():
            self.worker = _Worker(self)
            self.worker.got.connect(self._got)
            self.worker.start()

    def _got(self, host, found):
        self.mem.pop(host, None)
        if found:
            self.ready.emit(host)

    def clear(self):
        self.mem.clear()
        with self.lock:
            self.queue.clear()
        for f in CACHE.glob("*") if CACHE.exists() else []:
            try:
                f.unlink()
            except OSError:
                pass

    def stop(self):
        with self.lock:
            self.queue.clear()
        if self.worker is not None:
            self.worker.wait(3000)
