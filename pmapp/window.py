"""The main window: the sidebar, the pages, and everything the app does around them (the stick, locking, syncing, the browser, your other computers)."""
import csv
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QLockFile, QThread, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QMenu, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

import jblol_pm as core
from . import demo as demo_data
from .logos import LogoStore
from . import icons, link, model, passkeys, sysinfo, theme
from .desktop import DesktopMixin
from .pages.audit import AuditPage
from .pages.codes import CodesPage
from .pages.generator import GeneratorPage
from .pages.health import HealthPage
from .pages.locked import LockedPage
from .pages.security import SecurityPage
from .pages.settings import SettingsPage
from .pages.vault import VaultPage
from .theme import C
from .pages.items import FolderDialog, PasskeyPrompt, drop_widget
from .widgets import IconButton, Toast, VScroll, button, label

try:
    import presence
except ImportError:
    presence = None

PAGE_INDEX = {"locked": 0, "vault": 1, "gen": 2, "health": 3, "usb": 4, "audit": 5, "settings": 6, "codes": 7}
NEEDS_VAULT = {"vault", "health", "codes"}


class LinkWorker(QThread):
    """One round with the device mailbox on the server: send an order if there is one, say "I'm here", and read who else is. Never blocks the window."""
    done = Signal(object, str)                 # the server's listing, or an error text

    def __init__(self, code, did, name, locked, order_to=""):
        super().__init__()
        self.code, self.did, self.name, self.locked, self.order_to = code, did, name, locked, order_to

    def run(self):
        try:
            key = link.mac_key(core.phone_key_bytes(self.code))
            if self.order_to:
                core.link_request("PUT", f"/pm-link/order/{self.order_to}", link.order(key, self.order_to, self.did, self.name))
            core.link_request("PUT", f"/pm-link/device/{self.did}", link.beacon(key, self.did, self.name, self.locked, os=sysinfo.os_name()))
            self.done.emit(core.link_request("GET", "/pm-link"), "")
        except core.VaultError as e:
            self.done.emit(None, str(e))
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, f"Link failed: {e}")


class NavButton(QPushButton):
    def __init__(self, icon_name, text, key, count=None):
        super().__init__()
        self.setObjectName("nav")
        self.key, self.icon_name = key, icon_name
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(38)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 10, 0)
        h.setSpacing(11)
        self.ic = QLabel()
        self.ic.setFixedSize(20, 20)
        h.addWidget(self.ic)
        self.tx = QLabel(text)
        h.addWidget(self.tx, 1)
        self.badge = QLabel("")
        h.addWidget(self.badge)
        for c in (self.ic, self.tx, self.badge):
            c.setAttribute(Qt.WA_TransparentForMouseEvents)
            c.setStyleSheet("background:transparent;")
        self.set_count(count)
        self.set_on(False)

    def set_count(self, n, tone=""):
        self.badge.setText("" if n in (None, "") else str(n))
        col = {"warn": C["warn"], "bad": C["bad"]}.get(tone, C["faint"])
        self.badge.setStyleSheet(f"background:transparent; color:{col}; font-size:12px; font-weight:{650 if tone else 500};")

    def set_on(self, on):
        self.setProperty("on", on)
        self.style().unpolish(self)
        self.style().polish(self)
        self.ic.setPixmap(icons.pix(self.icon_name, 18, C["accent_hi"] if on else C["muted"], 1.9))
        self.tx.setStyleSheet(f"background:transparent; color:{C['accent_hi'] if on else C['soft']}; font-weight:{650 if on else 550};")


class SyncWorker(core.SyncWorker):
    pass


class Main(DesktopMixin, QMainWindow):
    def __init__(self, demo=False):
        super().__init__()
        self.demo = demo
        self.setWindowTitle(core.APP_NAME)
        self.setMinimumSize(1120, 680)
        self.resize(1260, 800)
        icon = core.HERE / "icon.svg"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.s = core.load_settings()
        self.vault = None
        self.vault_real = not demo
        self.entries, self.groups, self.pending = [], [], []
        self.health = {}
        self.page, self.f_kind, self.f_group, self.f_fav, self.f_tag = "vault", None, None, False, None
        self.manual_key = self.keyfile = self.remote_key = self.remote_from = None
        self.remote_paused = False
        self.remote_checked = 0.0
        self.last_active = time.time()
        self.clip_token = None
        self.was_plugged = None
        self.worker = self.sync_worker = self.phone_worker = None
        self.phone_status = self.last_sync = self.bridge_note = ""
        self.sudo_code, self.sudo_msg = "off", "Not set up"
        self.signals = core.Signals()
        self.bridge = None
        self.screen_locked = False
        self.offers, self.offer_seq, self.offer_dialog = {}, 0, None
        self.lock_in_seen = 0.0
        self.link_devs, self.link_worker, self.link_order, self.link_error = [], None, "", ""
        self.link_id = link.device_id(sysinfo.machine_name())
        self.link_seen_file = core.CONF_DIR / "link-seen.json"
        self.upgrading = False
        self._dev_cache = (0, [])
        self.phone_timer = QTimer(self)
        self.phone_timer.setSingleShot(True)
        self.phone_timer.timeout.connect(self.send_phone)
        self.sync_timer = QTimer(self)
        self.sync_timer.setSingleShot(True)
        self.sync_timer.timeout.connect(self.start_sync)

        self.logos = LogoStore(enabled=bool(self.s.get("show_logos", True)) and (not demo or bool(os.environ.get("JBLOL_PM_DEMO_LOGOS"))))
        self.logos.ready.connect(self.on_logo)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.build_side())
        self.pages = QStackedWidget()
        self.locked = LockedPage(self)
        self.vault_page = VaultPage(self)
        self.gen_page = GeneratorPage(self)
        self.health_page = HealthPage(self)
        self.usb_page = SecurityPage(self)
        self.audit_page = AuditPage(self)
        self.settings_page = SettingsPage(self)
        self.codes_page = CodesPage(self)
        for p in (self.locked, self.vault_page, self.gen_page, self.health_page, self.usb_page, self.audit_page, self.settings_page, self.codes_page):
            self.pages.addWidget(p)
        outer.addWidget(self.pages, 1)
        self.toast_w = Toast(root)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        if not demo:                              # (a demo window must never look at the real stick or open the real vault)
            self.timer.start(1500)
        QApplication.instance().installEventFilter(self)
        for key, fn in (("Ctrl+L", self.lock_now), ("Ctrl+K", self.focus_search), ("Ctrl+F", self.focus_search), ("Ctrl+N", lambda: self.new_item("login")), ("Esc", self.escape)):
            act = QAction(self)
            act.setShortcut(QKeySequence(key))
            act.triggered.connect(fn)
            self.addAction(act)
        self.signals.filled.connect(lambda host, n: (self.toast(f"Browser asked for {host}: {n} login(s) offered"), self.log(f"Browser fill: {host}, {n} login(s) offered")))
        self.signals.captured.connect(self.on_capture)
        self.signals.passkey.connect(self.on_passkey)
        self.link_timer = QTimer(self)
        self.link_timer.timeout.connect(self.link_tick)
        if not demo:
            self.link_timer.start(15000)
            QTimer.singleShot(4000, self.link_tick)
        self.periodic = QTimer(self)
        self.periodic.timeout.connect(lambda: self.start_sync())
        self.watchers = []
        if demo:
            self.vault = demo_data.DemoVault()
            self.keyfile = Path("/run/media/demo/Vault/recall-vault.keyx")
            self.set_entries(self.vault.load())
            self.groups = self.vault.groups()
            self.sudo_code, self.sudo_msg = "on", "Active: sudo needs no password"
            self.s.update(cross_device=True, lock_screen_on_pull=True, lock_peers_on_pull=True)
            self.paint_status()
        else:
            self.periodic.start(5 * 60 * 1000)
            self.start_bridge()
            self.tick()
            self.watchers = [self.watch_bus(["--session", "--dest", "org.gnome.ScreenSaver"], "ActiveChanged", lambda a: self.on_screen_active("true" in a)),
                             self.watch_bus(["--system", "--dest", "org.freedesktop.login1", "--object-path", "/org/freedesktop/login1"], "PrepareForSleep", lambda a: self.on_sleep("true" in a)),
                             self.watch_bus(["--session", "--dest", "org.jblol.PmFocus"], "WindowOpened", self.on_window_opened)]
        self.rebuild_nav()
        self.go("vault")

    # ------------------------------------------------------------------ the sidebar
    def build_side(self):
        side = QWidget()
        side.setObjectName("side")
        side.setFixedWidth(256)
        v = QVBoxLayout(side)
        v.setContentsMargins(14, 18, 14, 14)
        v.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 0)
        brand.setSpacing(11)
        logo = QLabel()
        logo.setFixedSize(34, 34)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet(f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {C['accent_hi']}, stop:1 {C['accent_lo']}); border-radius: 10px;")
        logo.setPixmap(icons.pix("shield_check", 20, "#ffffff", 2.0))
        brand.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(0)
        nm = QLabel(core.APP_NAME)
        nm.setObjectName("brand")
        col.addWidget(nm)
        col.addWidget(label(core.BRAND, "faint"))
        brand.addLayout(col)
        brand.addStretch(1)
        v.addLayout(brand)
        v.addSpacing(14)
        self.status_card = QFrame()
        self.status_card.setObjectName("cardflat")
        sl = QHBoxLayout(self.status_card)
        sl.setContentsMargins(12, 10, 12, 10)
        sl.setSpacing(10)
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        sl.addWidget(self.dot)
        sc = QVBoxLayout()
        sc.setSpacing(0)
        self.st_title = QLabel("Vault locked")
        self.st_title.setStyleSheet("font-weight:650;")
        self.st_sub = label("", "faint")
        sc.addWidget(self.st_title)
        sc.addWidget(self.st_sub)
        sl.addLayout(sc, 1)
        v.addWidget(self.status_card)
        nav_host = QWidget()
        self.nav_box = QVBoxLayout(nav_host)
        self.nav_box.setContentsMargins(0, 0, 4, 0)
        self.nav_box.setSpacing(2)
        self.nav_scroll = VScroll()
        self.nav_scroll.setWidget(nav_host)
        v.addWidget(self.nav_scroll, 1)
        self.pin_box = QVBoxLayout()
        self.pin_box.setSpacing(2)
        v.addLayout(self.pin_box)
        self.b_lock = button("Lock vault", "primary", self.lock_now, "lock")
        self.b_lock.setFixedHeight(40)
        v.addSpacing(8)
        v.addWidget(self.b_lock)
        return side

    def rebuild_nav(self):
        for box in (self.nav_box, self.pin_box):
            while box.count():
                it = box.takeAt(0)
                if it.widget():
                    drop_widget(it.widget())
        self.nav = {}
        counts = {k: 0 for k in model.KINDS}
        favs = 0
        codes = 0
        for e in self.entries:
            counts[model.kind_of(e)] += 1
            favs += bool((e.get("data") or {}).get("fav"))
            codes += bool(e.get("otp"))

        def add(key, icon, text, count=None, fn=None, box=None, tip=""):
            b = NavButton(icon, text, key, count)
            go = fn or (lambda k=key: self.go(k))
            b.clicked.connect(lambda _checked=False, go=go: go())      # (clicked sends a True/False that must not land in the lambda's own arguments)
            if tip:
                b.setToolTip(tip)
            self.nav[key] = b
            (box or self.nav_box).addWidget(b)
            return b

        def section(text, plus=None):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 4, 0)
            row.addWidget(label(text, "section"), 1)
            if plus:
                row.addWidget(IconButton("plus", plus[0], 16, fn=plus[1]))
            w = QWidget()
            w.setLayout(row)
            self.nav_box.addWidget(w)
        self.nav_box.addSpacing(6)
        section("VAULT")
        add("v:all", "vault", "All items", len(self.entries), lambda: self.go("vault"))
        add("v:fav", "star", "Favorites", favs, lambda: self.go("vault", fav=True))
        for kind, icon in (("login", "key"), ("card", "card"), ("note", "note"), ("wifi", "wifi"), ("passkey", "passkey")):
            add("v:" + kind, icon, model.KIND_PLURAL[kind], counts[kind], lambda k=kind: self.go("vault", kind=k))
        section("FOLDERS", ("New folder", lambda: self.new_folder()))
        collapsed = set(self.s.get("collapsed_folders", []))
        hidden_by = None
        for path, name, depth, n, has_kids in model.folder_tree(self.groups or core.STARTER_GROUPS, self.entries, core.HIDDEN_GROUPS):
            if hidden_by and path.startswith(hidden_by + "/"):
                continue
            hidden_by = path if path in collapsed else None if hidden_by and not path.startswith(hidden_by + "/") else hidden_by
            row = QHBoxLayout()
            row.setContentsMargins(depth * 14, 0, 0, 0)
            row.setSpacing(0)
            if has_kids:
                chev = IconButton("chevron_right" if path in collapsed else "chevron_down", "Show or hide the folders inside", 14, fn=lambda p=path: self.toggle_folder(p))
                chev.setFixedSize(22, 30)
                row.addWidget(chev)
            else:
                row.addSpacing(22)
            b = NavButton("folder", name, "g:" + path, n)
            b.clicked.connect(lambda _c=False, p=path: self.go("vault", group=p))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, p=path, bb=b: self.folder_menu(p, bb.mapToGlobal(pos)))
            self.nav["g:" + path] = b
            row.addWidget(b, 1)
            w = QWidget()
            w.setLayout(row)
            self.nav_box.addWidget(w)
        tags = model.tag_counts(self.entries)
        if tags:
            section("TAGS")
            for t, n in tags[:14]:
                b = add("t:" + t, "tag", t, n, lambda t=t: self.go("vault", tag=t))
                b.setContextMenuPolicy(Qt.CustomContextMenu)
                b.customContextMenuRequested.connect(lambda pos, t=t, bb=b: self.tag_menu(t, bb.mapToGlobal(pos)))
            if len(tags) > 14:
                self.nav_box.addWidget(label(f"  +{len(tags) - 14} more tags (search for them)", "faint"))
        section("TOOLS")
        add("gen", "dice", "Generator")
        add("codes", "otp", "One-time codes", codes or None)
        add("health", "shield", "Security Check")
        add("usb", "usb", "USB & Security")
        add("audit", "list", "Audit log")
        self.nav_box.addStretch(1)
        add("settings", "gear", "Settings", box=self.pin_box)
        self.update_badges()
        self.mark_nav()

    # -- folders and tags
    def toggle_folder(self, path):
        c = set(self.s.get("collapsed_folders", []))
        c.symmetric_difference_update({path})
        self.s["collapsed_folders"] = sorted(c)
        self.save()
        self.rebuild_nav()

    def folder_menu(self, path, pos):
        m = QMenu(self)
        m.addAction("New folder inside…").triggered.connect(lambda: self.new_folder(path))
        m.addAction("Delete this folder").triggered.connect(lambda: self.delete_folder(path))
        m.exec(pos)

    def new_folder(self, parent=""):
        if not self.vault:
            return
        folders = [r[0] for r in model.folder_tree(self.groups or core.STARTER_GROUPS, self.entries, core.HIDDEN_GROUPS)]
        d = FolderDialog(self, folders, parent)
        if not d.exec():
            return
        path = "/".join(x for x in (d.parent(), d.name()) if x)
        try:
            self.vault.check_name(d.name(), "folder")
            self.vault.ensure_group(path)
        except core.VaultError as e:
            QMessageBox.warning(self, "Couldn't make the folder", str(e))
            return
        self.log(f"Made the folder “{path}”")
        self.groups = sorted(set(self.vault.groups()))
        self.rebuild_nav()
        self.go("vault", group=path)
        self.toast(f"Folder “{path}” made", "good")

    def delete_folder(self, path):
        if QMessageBox.question(self, "Delete folder", f"Delete the empty folder “{path}”?") != QMessageBox.Yes:
            return
        try:
            self.vault.delete_group(path)
        except core.VaultError as e:
            QMessageBox.information(self, "Can't delete it", str(e))
            return
        self.log(f"Deleted the folder “{path}”")
        self.groups = sorted(set(self.vault.groups()))
        if self.f_group == path:
            self.f_group = None
        self.rebuild_nav()
        self.go("vault")

    def tag_menu(self, tag, pos):
        m = QMenu(self)
        m.addAction("Rename this tag…").triggered.connect(lambda: self.rename_tag(tag))
        m.addAction("Remove this tag from every item").triggered.connect(lambda: self.rename_tag(tag, remove=True))
        m.exec(pos)

    def rename_tag(self, tag, remove=False):
        new = ""
        if not remove:
            new, ok = QInputDialog.getText(self, core.APP_NAME, f"Rename the tag “{tag}” to:", text=tag)
            new = (model.norm_tags(new) or [""])[0]
            if not ok or not new or new == tag:
                return
        elif QMessageBox.question(self, "Remove tag", f"Remove “{tag}” from every item that has it? The items themselves stay.") != QMessageBox.Yes:
            return
        todo = [e for e in self.entries if tag in model.item_tags(e)]
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            for e in todo:
                tags = [new if t == tag else t for t in model.item_tags(e)] if new else [t for t in model.item_tags(e) if t != tag]
                data = dict(e.get("data") or {}, tags=model.norm_tags(tags))
                self.vault.edit(e, dict(e, data=data))
        except core.VaultError as ex:
            QMessageBox.warning(self, "Couldn't change the tag", str(ex))
        finally:
            QApplication.restoreOverrideCursor()
        self.log(f"Tag “{tag}” " + (f"renamed to “{new}”" if new else "removed") + f" on {len(todo)} item(s)")
        self.f_tag = new if (self.f_tag == tag and new) else None if self.f_tag == tag else self.f_tag
        self.reload(select=False)
        self.go("vault", tag=self.f_tag)

    def on_logo(self, host):
        """A site's logo arrived: redraw what shows it."""
        vp = self.vault_page
        vp.list.viewport().update()
        cur = vp.current
        if cur and not vp.detail.editing and self.logos_host(cur) == host:
            vp.show(cur)
        if self.page == "codes":
            self.codes_page.sig = None
            self.codes_page.refresh()

    @staticmethod
    def logos_host(e):
        from .logos import host_for
        return host_for(e)

    def update_badges(self):
        b = self.nav.get("health")
        if b:
            n = len({k for k in self.health}) + len(getattr(self.health_page, "breached", {}))
            b.set_count(n if n else None, "warn" if n else "")

    def mark_nav(self):
        if self.page == "vault":
            key = ("v:fav" if self.f_fav else "t:" + self.f_tag if self.f_tag else "v:" + self.f_kind if self.f_kind else
                   "g:" + self.f_group if self.f_group is not None else "v:all")
        else:
            key = self.page
        for k, b in self.nav.items():
            b.set_on(k == key)
        if key in self.nav and hasattr(self, "nav_scroll"):
            self.nav_scroll.ensureWidgetVisible(self.nav[key], 0, 40)

    # ------------------------------------------------------------------ navigation
    def go(self, page, kind=None, group=None, fav=False, tag=None):
        self.page = page
        if page == "vault":
            self.f_kind, self.f_group, self.f_fav, self.f_tag = kind, group, fav, tag
            self.vault_page.set_filter(kind, group, fav, tag)
        self.mark_nav()
        self.show_page()

    def show_page(self):
        if self.page in NEEDS_VAULT and not self.vault:
            self.pages.setCurrentIndex(0)
            return
        self.pages.setCurrentIndex(PAGE_INDEX[self.page])
        if self.page == "health":
            self.health_page.refresh()
        elif self.page == "usb":
            self.usb_page.refresh()
        elif self.page == "audit":
            self.audit_page.refresh()
        elif self.page == "settings":
            self.settings_page.refresh()
        elif self.page == "codes":
            self.codes_page.refresh()

    def focus_search(self):
        self.go("vault", self.f_kind, self.f_group, self.f_fav, self.f_tag)
        self.vault_page.focus_search()

    def escape(self):
        if self.page == "vault" and self.vault_page.detail.editing:
            self.vault_page.cancel_edit()

    def open_item(self, e):
        self.vault_page.search.clear()
        self.go("vault")
        self.vault_page.select(e)

    def toast(self, text, tone="", ms=3200):
        self.toast_w.show_message(text, tone, ms)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.toast_w.place()

    def log(self, text):
        core.log_event(text)
        if self.page == "audit":
            self.audit_page.refresh()

    def save(self):
        core.save_settings(self.s)

    def set_opt(self, key, value):
        self.s[key] = value
        self.save()
        if key == "warn_passwords":
            self.set_entries(list(self.entries) + list(self.pending))
            self.rebuild_nav()
            self.vault_page.refresh()
            if self.vault_page.current:
                self.vault_page.show(self.vault_page.current)
            if self.page == "health":
                self.health_page.refresh()
        if key == "warn_security" and self.page == "usb":
            QTimer.singleShot(0, self.usb_page.refresh)
        if key in ("lock_screen_on_pull", "lock_peers_on_pull"):
            self.log(f"{key.replace('_', ' ').capitalize()}: {'on' if value else 'off'}")
        if self.page == "usb":
            QTimer.singleShot(0, self.usb_page.refresh)

    def current_group_for(self, kind):
        if kind != "login":
            return model.KIND_GROUP[kind]
        g = self.f_group
        return g if g not in (None, *model.KIND_GROUP.values()) else "Websites"

    # ------------------------------------------------------------------ items
    def set_entries(self, entries):
        self.pending = [e for e in entries if e["group"].split("/")[0] == core.PENDING_GROUP]
        entries = [e for e in entries if e["group"].split("/")[0] != core.PENDING_GROUP]
        self.entries = entries
        logins = [e for e in entries if model.kind_of(e) == "login"]
        self.bridge_entries = logins
        self.health = core.compute_health(logins) if self.s.get("warn_passwords", True) else {}
        if entries and self.vault and self.s["phone_enabled"] and self.s["phone_key"] and not self.demo:
            self.phone_timer.start(6000)

    def reload(self, select=None):
        self.set_entries(self.vault.load())
        self.groups = sorted(set(self.vault.groups()))
        self.rebuild_nav()
        if self.page == "vault":
            self.vault_page.finish_edit(select) if select is not False else None
        elif self.page == "codes":
            self.codes_page.refresh()
        self.request_sync()

    def new_item(self, kind, **preset):
        if not self.vault:
            self.toast("Plug in your stick first", "warn")
            return
        self.go("vault", kind=self.f_kind if kind == self.f_kind else None, group=self.f_group, fav=False) if self.page != "vault" else None
        self.vault_page.start_new(kind, **preset)

    def save_item(self, old, new, quiet=False):
        try:
            if "data" in new and new.get("kind") in (None, "login") and not new["data"]:
                new["data"] = {}
            same = old is not None and (new["title"], new["group"]) == (old["title"], old["group"])
            taken = {e["title"].lower() for e in self.entries if e["group"] == new["group"]}
            if not same and new["title"].lower() in taken:                      # another account on the same site: name it after the account
                base = new["title"]
                t = f"{base} ({new['username']})" if new["username"] else base
                n = 2
                while t.lower() in taken:
                    t = f"{base} {n}"
                    n += 1
                new["title"] = t
                self.toast(f"You already have “{base}”, so this one is saved as “{t}”.", "warn", 6000)
            if old is None:
                self.vault.add(new)
            else:
                self.vault.edit(old, new)
            if model.kind_of(new) == "login" and not self.demo:
                self.update_twins(old, new)
            self.reload(select=new if not quiet else self.vault_page.current and new)
            if not quiet:
                self.toast("Saved", "good")
        except core.VaultError as e:
            QMessageBox.warning(self, "Couldn't save", str(e))

    def delete_item(self, e):
        try:
            self.vault.delete(e)
            self.vault_page.current = None
            self.reload(select=None)
            self.toast("Deleted")
        except core.VaultError as x:
            QMessageBox.warning(self, "Couldn't delete", str(x))

    def save_twin(self, t):
        self.vault.ensure_group("Apps")
        self.vault.add(t)
        if t.get("move_from"):
            self.vault.delete(t["move_from"])
            self.log(f"Moved “{t['move_from']['title']}” to Apps as “{t['title']}” (it has no website)")
        else:
            self.log(f"Also saved under Apps as “{t['title']}”")

    def update_twins(self, old, new):
        """Keep a website login and its Apps copy the same; a new website login for a service with an app gets one."""
        if old is None:
            for t in core.plan_app_twins(self.entries + [new], only=[new]):
                self.save_twin(t)
            return
        if (old["password"], old["username"], old["title"]) == (new["password"], new["username"], new["title"]):
            return
        for x in core.twins_of(self.entries, old):
            y = dict(x, password=new["password"], username=new["username"])
            m = core.TWIN_RE.match(x.get("notes") or "")
            if m and m.group(1) == old["title"]:
                y["notes"] = x["notes"].replace(f"“{old['title']}”", f"“{new['title']}”", 1)
            self.vault.edit(x, y)
            self.log(f"Updated the linked login “{x['title']}” too")

    def arm_reveal(self, field):
        """A password the user reveals hides itself again after the time set in Settings."""
        secs = int(self.s.get("reveal_timeout_s", 0) or 0)
        if not secs or not field.b_show:
            return
        t = QTimer(field)
        t.setSingleShot(True)
        t.timeout.connect(lambda: field.shown and field.toggle())
        field.b_show.clicked.connect(lambda: t.start(secs * 1000) if field.shown else t.stop())

    # ------------------------------------------------------------------ the stick, locking and unlocking
    def key_now(self):
        return self.keyfile or self.manual_key or self.remote_key

    def check_remote(self, plugged):
        """Cross-device (presence.py): while your stick is in your other computer, sudo works here through the root helper on its own;
        this side only opens the passwords, if you allowed that, with the key file the other computer sealed for this one."""
        st = presence.remote_status() if presence is not None and self.s["cross_device"] and not plugged else None
        was, self.remote_from = self.remote_from, (st[0] if st else None)
        if was and not self.remote_from:
            self.log(f"Your stick is no longer in {was}")
            self.remote_paused = False
        elif self.remote_from and not was:
            self.log(f"Your stick is in {self.remote_from}")
        if plugged and self.remote_key:
            if self.vault:
                self.vault.key = str(self.keyfile)
            self.remote_key = None
            presence.wipe_ram_keyfile()
            return
        want = bool(self.remote_from and self.s["cross_device_vault"])
        if want and not self.remote_key and not self.vault and not self.screen_locked and not self.remote_paused and time.time() - self.remote_checked > 5:
            self.remote_checked = time.time()
            data = presence.remote_keyfile()
            if data:
                self.remote_key = str(presence.ram_keyfile(data))
                self.log(f"Opening your passwords with the stick in {self.remote_from}")
                self.unlock()
        elif self.remote_key and not want:
            self.remote_key = None
            presence.wipe_ram_keyfile()
            if self.vault and not self.keyfile and not self.manual_key:
                self.lock_now(msg="Your stick left your other computer, so your passwords were locked.")

    def check_lock_order(self):
        """Another computer told this one to lock (presence.py already locked the screen): lock the vault too. Only a fresh order counts: one left over from
        earlier (the app was closed when it came) must not lock you out the moment you open the app."""
        if presence is None:
            return
        try:
            m = presence.LOCK_IN.stat().st_mtime
        except OSError:
            return
        if m <= self.lock_in_seen:
            return
        self.lock_in_seen = m
        try:
            info = json.loads(presence.LOCK_IN.read_text())
        except (OSError, ValueError):
            return
        if time.time() - float(info.get("at", 0)) > 120:
            return
        if self.vault and not self.manual_key:
            self.lock_now(msg=f"{info.get('from', 'your other computer')} told this computer to lock.")

    def maybe_upgrade_link(self):
        """Older paired computers talk to the server with a script that doesn't know the device list yet: update it while the stick is here."""
        if presence is None or self.upgrading or not (self.keyfile and core.stick_is_mine(self.keyfile)) or not self.s["cross_device"]:
            return
        if not presence.state().get("server_old"):
            return
        self.upgrading = True

        def run():
            try:
                presence.server_setup(presence._settings(), presence.machine_name())
                core.log_event("Updated the link to your server so your computers can see each other")
            except Exception as e:
                core.log_event(f"Couldn't update the link to your server: {e}")
            finally:
                time.sleep(60)
                self.upgrading = False
        threading.Thread(target=run, daemon=True).start()

    def tick(self):
        self.keyfile = core.find_keyfile()
        plugged = self.keyfile is not None
        if self.was_plugged is None or plugged != self.was_plugged:
            self.was_plugged is None
            self.was_plugged = plugged
            self.log("USB stick plugged in" if plugged else "USB stick removed")
            if plugged:
                self.unlock_screen_with_stick()
            if plugged and not self.vault and not self.screen_locked:
                self.unlock()
            elif not plugged and self.vault and self.s["lock_on_unplug"] and not self.manual_key and not self.remote_key:
                self.lock_now(msg="The stick was removed, so your passwords were locked.")
        self.check_remote(plugged)
        self.check_lock_order()
        self.maybe_upgrade_link()
        if self.vault and self.s["idle_lock_min"] and time.time() - self.last_active > self.s["idle_lock_min"] * 60:
            self.lock_now(msg="Locked after being idle.")
        self.sudo_code, self.sudo_msg = core.sudo_state(self.keyfile)
        self.paint_status()
        if self.page == "usb" and not self.usb_page.underMouse():
            self.usb_page.refresh()
        elif self.page == "settings":
            self.settings_page.bridge_label.setText(self.bridge_text())

    def paint_status(self):
        if self.vault:
            self.dot.setStyleSheet(f"background:{C['good']}; border-radius:5px;")
            self.st_title.setText("Vault unlocked")
            self.st_sub.setText("USB stick connected" if self.keyfile else f"Using the stick in {self.remote_from}" if self.remote_key else "Key file open")
        else:
            self.dot.setStyleSheet(f"background:{C['warn']}; border-radius:5px;")
            self.st_title.setText("Vault locked")
            self.st_sub.setText(f"Stick is in {self.remote_from}" if self.remote_from else "Plug in your USB stick")
        self.b_lock.setVisible(bool(self.vault))
        self.locked.pill.set("USB stick plugged in" if self.keyfile else f"Stick in {self.remote_from}" if self.remote_from else "USB stick not plugged in",
                             "good" if (self.keyfile or self.remote_from) else "")

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.MouseMove):
            self.last_active = time.time()
        return False

    def unlock(self):
        key = self.key_now()
        if not key:
            return
        if not Path(self.s["vault"]).exists() and self.s["sync_enabled"]:
            self.toast("Fetching your vault from the server…")
            QApplication.processEvents()
            try:
                msg, _ = core.sync_vault(self.s["vault"], key, self.s["sync_remote"], self.s["sync_path"])
                self.last_sync = f"{msg}, {time.strftime('%H:%M')}"
            except core.VaultError as e:
                self.show_locked("Couldn't get your vault", "This computer has no vault yet and the server didn't answer.", error=str(e))
                return
        if not Path(self.s["vault"]).exists():
            self.show_locked("Can't find the vault file", f"Expected {self.s['vault']}", error="Pick another one under Settings.")
            return
        try:
            v = core.Vault(self.s["vault"], key)
            self.set_entries(v.load())
            have = v.groups()
            for g in core.STARTER_GROUPS:
                if g not in have:
                    try:
                        v.ensure_group(g)
                    except core.VaultError:
                        pass
            self.groups = sorted(set(v.groups()))
        except core.VaultError as e:
            self.vault = None
            self.show_locked("Couldn't open the vault", "", error=str(e))
            return
        self.vault = v
        self.log(f"Vault unlocked ({len(self.entries)} items)")
        self.rebuild_nav()
        self.vault_page.refresh()
        self.paint_status()
        self.show_page()
        self.toast(f"Opened {len(self.entries)} items", "good")
        self.request_sync(3000)
        if self.entries and self.s["phone_enabled"] and self.s["phone_key"] and not self.demo:
            self.phone_timer.start(8000)                       # the phone's copy is brought up to date each time the vault opens, not only after an edit

    def show_locked(self, title, msg, error=None):
        self.locked.set(title, msg or ("Plug in your USB stick to open it." if not self.key_now() else ""), error if isinstance(error, str) else "")
        self.show_page()

    def lock_now(self, msg=None):
        if not isinstance(msg, str):
            msg = None
        if self.vault:
            self.log("Vault locked" + (f": {msg}" if msg else ""))
        if self.remote_key:
            self.remote_key = None
            self.remote_paused = bool(self.remote_from)
            presence.wipe_ram_keyfile()
        self.vault = None
        self.set_entries([])
        if self.keyfile is None:
            self.manual_key = None
        self.vault_page.current = None
        self.vault_page.list.clear()
        self.vault_page.search.clear()
        self.vault_page.show_none()
        if self.clip_token:
            QApplication.clipboard().clear()
        self.locked.set("Your vault is locked", msg or "Plug in your USB stick to open it.")
        self.rebuild_nav()
        self.paint_status()
        self.show_page()

    def lock_this_computer(self):
        self.log("You locked this computer from the app")
        self.lock_now()
        if not sysinfo.lock_session():
            self.toast("Couldn't lock the screen", "bad")

    # ------------------------------------------------------------------ your phone (pmapp/link.py)
    def link_phones(self):
        if self.demo:
            return demo_data.phones()
        return [d for d in self.link_devs if d.get("kind") == "phone"]

    def link_tick(self):
        """Every 15 s while the phone copy is on: tell the server this computer is here and whether it's locked, see which phones are around,
        and carry out a fresh lock order a phone sent."""
        if self.demo or self.link_worker or not (self.s["phone_enabled"] and self.s["phone_key"]):
            return
        order_to, self.link_order = self.link_order, ""
        self.link_worker = LinkWorker(self.s["phone_key"], self.link_id, sysinfo.machine_name(), self.vault is None, order_to)
        self.link_worker.done.connect(self.link_done)
        self.link_worker.start()

    def link_done(self, listing, err):
        if self.link_worker:
            self.link_worker.wait()
        self.link_worker = None
        self.link_error = err
        if listing is not None:
            try:
                key = link.mac_key(core.phone_key_bytes(self.s["phone_key"]))
                self.link_devs = link.devices(key, listing, self.link_id)
                try:
                    seen = json.loads(self.link_seen_file.read_text())
                except (OSError, ValueError):
                    seen = []
                orders, seen = link.orders_for(key, listing, self.link_id, seen)
                if orders:
                    try:
                        self.link_seen_file.write_text(json.dumps(seen))
                    except OSError:
                        pass
                    who = orders[-1].get("from_name") or "your phone"
                    self.log(f"{who} told this computer to lock")
                    if self.vault and not self.manual_key:
                        self.lock_now(msg=f"{who} told this computer to lock.")
                    sysinfo.lock_session()
            except core.VaultError:
                pass
        if self.page == "usb":
            self.usb_page.refresh()

    def lock_phone(self, did, name):
        if self.demo:
            self.toast(f"(demo) Told {name} to lock its passwords", "good")
            return
        if not (self.s["phone_enabled"] and self.s["phone_key"]):
            self.toast("Turn on the phone copy in Settings first", "warn")
            return
        self.link_order = did
        self.log(f"You asked {name} to lock its passwords page")
        self.toast(f"Told {name} to lock its passwords. It happens within a few seconds.", "good", 5000)
        self.link_tick()
        QTimer.singleShot(1500, self.link_tick)

    def pick_key(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose the key file", str(core.HOME), "Key files (*.keyx *.key);;All files (*)")
        if f:
            self.manual_key = Path(f)
            self.unlock()

    # ------------------------------------------------------------------ your other computers
    def devices(self):
        if self.demo:
            return demo_data.devices()
        now = time.time()
        if now - self._dev_cache[0] > 4 and presence is not None:
            try:
                self._dev_cache = (now, presence.devices(self.keyfile if core.stick_is_mine(self.keyfile) else None))
            except Exception:
                self._dev_cache = (now, [{"name": sysinfo.machine_name(), "me": True, "vault": False, "seen": None}])
        return self._dev_cache[1] or [{"name": sysinfo.machine_name(), "me": True, "vault": False, "seen": None}]

    def lock_device(self, name):
        if self.demo:
            self.toast(f"(demo) Told {name} to lock", "good")
            return
        if presence is None or not core.stick_is_mine(self.keyfile) or not self.s["cross_device"]:
            self.toast("Plug your stick into this computer (cross-device on) to lock another computer", "warn")
            return
        if QMessageBox.question(self, "Lock device", f"Lock {name} now? Its screen locks within a few seconds.") != QMessageBox.Yes:
            return
        presence.request_lock([name])
        self.log(f"You asked {name} to lock")
        self.toast(f"Told {name} to lock. It happens within a few seconds.", "good", 5000)

    def remove_device(self, name):
        if not core.stick_is_mine(self.keyfile):
            return
        if QMessageBox.question(self, "Remove computer", f"Remove {name} from your stick's list? It stops getting sudo, lock-screen and lock orders from your stick.") != QMessageBox.Yes:
            return
        try:
            (presence.peers_dir(self.keyfile) / f"{name}.json").unlink()
            self.log(f"Removed {name} from the stick's list")
            self._dev_cache = (0, [])
            self.usb_page.refresh()
        except OSError as e:
            QMessageBox.warning(self, "Couldn't remove it", str(e))

    def pair_here(self, vault):
        try:
            presence.pair(self.keyfile, vault=vault)
            return True
        except (OSError, subprocess.SubprocessError) as e:
            QMessageBox.warning(self, core.APP_NAME, f"Couldn't pair this computer: {e}")
            return False

    def toggle_cross(self, on):
        if presence is None:
            self.toast("Needs python3-cryptography", "bad")
            return
        if on:
            if not core.stick_is_mine(self.keyfile):
                QMessageBox.information(self, core.APP_NAME, "Plug your stick into this computer to turn this on: that's how it pairs this computer.")
                QTimer.singleShot(0, self.usb_page.refresh)
                return
            if not self.pair_here(False):
                QTimer.singleShot(0, self.usb_page.refresh)
                return
            self.log(f"Cross-device turned on: {presence.machine_name()} added to the stick")
        else:
            presence.unpair(self.keyfile if core.stick_is_mine(self.keyfile) else None)
            self.log("Cross-device turned off on this computer")
            self.s["cross_device_vault"] = False
        self.s["cross_device"] = bool(on)
        self.save()
        QTimer.singleShot(0, self.usb_page.refresh)

    def toggle_cross_vault(self, on):
        if presence is None:
            return
        if on and not core.stick_is_mine(self.keyfile):
            QMessageBox.information(self, core.APP_NAME, "Plug your stick into this computer to turn this on: this computer's key goes onto the stick.")
            QTimer.singleShot(0, self.usb_page.refresh)
            return
        if on:
            if not self.pair_here(True):
                QTimer.singleShot(0, self.usb_page.refresh)
                return
        else:
            presence.forget_box()
            if core.stick_is_mine(self.keyfile):
                self.pair_here(False)
            if self.remote_key:
                self.lock_now(msg="Passwords from your other computer's stick are turned off.")
        self.log("Passwords from the stick in another computer: " + ("allowed" if on else "not allowed"))
        self.s["cross_device_vault"] = bool(on)
        self.save()
        QTimer.singleShot(0, self.usb_page.refresh)

    # ------------------------------------------------------------------ vault file, import, backup
    def choose_vault(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose a vault file", str(Path(self.s["vault"]).parent), "KeePass vaults (*.kdbx)")
        if f:
            self.s["vault"] = f
            self.save()
            self.lock_now()
            self.unlock()

    def backup(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder for the backup", str(core.HOME))
        if d:
            try:
                dest = Path(d) / f"vault-backup-{time.strftime('%Y%m%d-%H%M%S')}.kdbx"
                shutil.copy2(self.s["vault"], dest)
                QMessageBox.information(self, "Backed up", f"Saved {dest}\n\nIt is encrypted and needs the key file on your stick to open.")
            except OSError as e:
                QMessageBox.warning(self, "Couldn't back up", str(e))

    def choose_import(self):
        sp = self.settings_page
        if not self.vault:
            self.toast("Plug in your stick first", "warn")
            return
        f, _ = QFileDialog.getOpenFileName(self, "Choose the exported passwords file", str(core.HOME / "Downloads"), "CSV files (*.csv);;All files (*)")
        if not f:
            return
        try:
            rows = core.parse_csv(f)
        except (core.VaultError, OSError, csv.Error) as e:
            sp.imp_msg.setText(f"<span style='color:{C['bad']}'>Couldn't read that file: {e}</span>")
            return
        group = sp.imp_group.currentText().strip().strip("/") or "Websites"
        add, skipped = core.plan_import(rows, self.entries, group)
        if not add:
            sp.imp_msg.setText(f"Nothing new to import: {len(rows)} found, {skipped} already in the vault.")
            return
        mins = max(1, round(len(add) * 0.5 / 60))
        if QMessageBox.question(self, "Import", f"Import {len(add)} passwords into “{group}”?\n{skipped} already in the vault will be skipped.\nThis takes about {mins} minute(s); keep the stick plugged in.") != QMessageBox.Yes:
            return
        try:
            self.vault.ensure_group(group)
        except core.VaultError as e:
            sp.imp_msg.setText(f"<span style='color:{C['bad']}'>{e}</span>")
            return
        self.import_file = f
        sp.imp_bar.setRange(0, len(add))
        sp.imp_bar.setValue(0)
        sp.imp_bar.setVisible(True)
        sp.imp_msg.setText("Importing…")
        self.worker = core.ImportWorker(self.vault.db, self.vault.key, add)
        self.worker.progress.connect(lambda n, t: (sp.imp_bar.setValue(n), sp.imp_msg.setText(f"Importing… {n} of {t}")))
        self.worker.finished_ok.connect(self.import_done)
        self.worker.start()

    def import_done(self, n, errors):
        sp = self.settings_page
        self.worker.wait()
        self.worker = None
        sp.imp_bar.setVisible(False)
        msg = f"<span style='color:{C['good']}'>Imported {n} passwords.</span>"
        if errors:
            msg += f"<br><span style='color:{C['warn']}'>{len(errors)} couldn't be added: {'; '.join(errors[:3])}{'…' if len(errors) > 3 else ''}</span>"
        sp.imp_msg.setText(msg)
        if self.vault:
            self.reload(select=False)
        if QMessageBox.question(self, "Delete the exported file?", f"Overwrite and delete the plain-text file now?\n{self.import_file}") == QMessageBox.Yes:
            if not core.wipe_file(self.import_file):
                QMessageBox.warning(self, "Couldn't delete it", "Delete the file yourself, and empty the Trash.")

    def import_wifi(self):
        if not self.vault:
            self.toast("Plug in your stick first", "warn")
            return
        profiles = model.nm_wifi_profiles()
        have = {(e.get("data") or {}).get("ssid", e["title"]) for e in self.entries if model.kind_of(e) == "wifi"}
        new = [p for p in profiles if p[0] not in have]
        if not profiles:
            QMessageBox.information(self, core.APP_NAME, "No saved Wi-Fi networks were found on this computer (NetworkManager).")
            return
        if not new:
            QMessageBox.information(self, core.APP_NAME, "Every Wi-Fi network on this computer is already in your vault.")
            return
        if QMessageBox.question(self, "Add Wi-Fi networks", f"Add {len(new)} network(s) from this computer to your vault, with their passwords?\n\n" + "\n".join(n for n, _, _ in new[:10])) != QMessageBox.Yes:
            return
        try:
            for ssid, psk, sec in new:
                e = model.new_item("wifi")
                e.update(title=ssid, password=psk, data={"ssid": ssid, "security": sec} if sec != "WPA" else {"ssid": ssid})
                self.vault.ensure_group("Wi-Fi")
                self.vault.add(e)
            self.log(f"Added {len(new)} Wi-Fi network(s) from this computer")
            self.reload(select=False)
            self.toast(f"Added {len(new)} Wi-Fi network(s)", "good")
        except core.VaultError as e:
            QMessageBox.warning(self, "Couldn't add them", str(e))

    def add_this_computer_ssh(self):
        """Save this computer's SSH key in Servers & SSH: who and where, the public key and its fingerprint in the notes, the private key attached."""
        if not self.vault:
            self.toast("Plug in your stick first", "warn")
            return
        keys = sysinfo.ssh_keys(core.HOME)
        if not keys:
            QMessageBox.information(self, core.APP_NAME, "No private SSH key was found in ~/.ssh. Make one with:  ssh-keygen -t ed25519")
            return
        key = keys[0]
        if len(keys) > 1:                                  # more than one: ask which (a GitHub key, a server key, the usual one…)
            names = [k.name for k in keys]
            pick, ok = QInputDialog.getItem(self, core.APP_NAME, "Which SSH key should be saved in your vault?", names, 0, False)
            if not ok:
                return
            key = keys[names.index(pick)]
        host = os.uname().nodename
        default = "desktop" if "desktop" in host.lower() else "laptop" if "laptop" in host.lower() else host
        if key.name not in ("id_ed25519", "id_ecdsa", "id_rsa"):
            default = f"{default} {key.name}"
        title, ok = QInputDialog.getText(self, core.APP_NAME, f"Save {key.name} from {host} as:", text=default)
        title = (title or "").strip()
        if not ok or not title:
            return
        if any(e["group"] == "Servers & SSH" and e["title"].lower() == title.lower() for e in self.entries):
            QMessageBox.information(self, core.APP_NAME, f"“{title}” is already in Servers & SSH. Pick another name, or delete that one first.")
            return
        try:
            pub = Path(str(key) + ".pub").read_text().strip() if Path(str(key) + ".pub").exists() else \
                subprocess.run(["ssh-keygen", "-y", "-P", "", "-f", str(key)], capture_output=True, text=True, timeout=10).stdout.strip()
            fp = subprocess.run(["ssh-keygen", "-lf", str(key)], capture_output=True, text=True, timeout=10).stdout.split()
            no_pass = subprocess.run(["ssh-keygen", "-y", "-P", "", "-f", str(key)], capture_output=True, timeout=10).returncode == 0
            notes = "\n".join([f"Public key: {pub}", f"Fingerprint: {fp[1] if len(fp) > 1 else '?'}",
                               f"Private key attached as {key.name}" + (" (no passphrase)." if no_pass else " (it has a passphrase).")])
            self.vault.ensure_group("Servers & SSH")
            self.vault.add({"group": "Servers & SSH", "title": title, "username": os.environ.get("USER", ""), "password": "", "url": f"ssh://{host}", "notes": notes,
                            "otp": "", "history": []})
            self.vault.attach("Servers & SSH", title, key)
        except (OSError, subprocess.SubprocessError, core.VaultError) as e:
            QMessageBox.warning(self, "Couldn't save the key", str(e))
            return
        self.log(f"Saved this computer's SSH key as “{title}”")
        self.reload(select=False)
        self.toast(f"Saved {key.name} as “{title}”", "good")

    def add_app_twins(self):
        if not self.vault:
            self.toast("Plug in your stick first", "warn")
            return
        plan = core.plan_app_twins(self.entries)
        if not plan:
            QMessageBox.information(self, core.APP_NAME, "Every website login with a computer app is already under Apps.")
            return
        try:
            for t in plan:
                self.save_twin(t)
        except core.VaultError as e:
            QMessageBox.warning(self, "Couldn't save", str(e))
        self.log(f"Copied {len(plan)} website logins into Apps")
        self.reload(select=False)
        self.toast(f"Added {len(plan)} logins under Apps", "good")

    # ------------------------------------------------------------------ sync with the server
    def request_sync(self, ms=4000):
        if self.vault and self.vault_real and self.s["sync_enabled"]:
            self.sync_timer.start(ms)

    def start_sync(self, manual=False):
        if not self.vault or not self.vault_real or self.sync_worker or not (self.s["sync_enabled"] or manual):
            return
        self.sync_worker = core.SyncWorker(self.vault.db, self.vault.key, self.s["sync_remote"], self.s["sync_path"])
        self.sync_worker.done.connect(self.sync_done)
        self.settings_page.sync_label.setText("Syncing…")
        self.sync_worker.start()

    def sync_done(self, msg, changed, err):
        if self.sync_worker:
            self.sync_worker.wait()
        self.sync_worker = None
        if err:
            self.last_sync = f"Last sync failed at {time.strftime('%H:%M')}: {err}"
            self.log(f"Sync failed: {err}")
            self.toast("Sync failed. Your passwords are safe on this computer; it will try again.", "warn", 6000)
        else:
            self.last_sync = f"{msg}, {time.strftime('%H:%M')}"
            self.log(f"Sync: {msg}")
            if changed and self.vault and not self.vault_page.detail.editing:
                try:
                    cur = self.vault_page.current
                    self.set_entries(self.vault.load())
                    self.groups = sorted(set(self.vault.groups()))
                    self.rebuild_nav()
                    self.vault_page.refresh()
                    if cur:
                        self.vault_page.select(cur)
                    self.toast("Updated with changes from your other computer")
                except core.VaultError:
                    pass
        if self.page == "settings":
            self.settings_page.refresh()

    # ------------------------------------------------------------------ new passwords seen by the browser
    def on_capture(self, d):
        if not self.vault:
            return
        host, user, pw = d.get("host", "").lower().strip("."), d.get("username", "").strip(), d.get("password", "")
        if not host or not pw:
            return
        mine = [e for e in core.match_entries(self.entries, host) if (e["username"] or "").lower() == user.lower()]
        if any(e["password"] == pw for e in mine):
            return
        existing = mine[0] if mine else None
        if existing and d.get("kind") != "new":
            return
        now = time.time()
        self.offers = {i: o for i, o in self.offers.items() if now - o["at"] < 900}
        if any(o["host"] == host and o["user"] == user and o["pw"] == pw for o in self.offers.values()):
            return
        self.offer_seq += 1
        oid = self.offer_seq
        kind = "change" if existing else "new"
        self.offers[oid] = {"id": oid, "host": host, "user": user, "pw": pw, "kind": kind, "existing": existing, "at": now}
        self.log(f"New password found on {host} ({kind})")
        text = f"I found a {'changed' if existing else 'new'} password for {host}. Want to add it to the password manager?" if not existing else f"The password for {host} changed. Want to update it in the password manager?"
        if core.tell_cat({"cmd": "offer", "id": oid, "text": text, "host": host}) is None:
            self.popup_offer(oid)

    def popup_offer(self, oid):
        o = self.offers.get(oid)
        if not o:
            return
        box = QMessageBox(None)
        box.setWindowTitle(core.APP_NAME)
        box.setIcon(QMessageBox.Question)
        box.setText(f"A {'changed' if o['kind'] == 'change' else 'new'} password was found for {o['host']}.")
        box.setInformativeText("Would you like to add it to the password manager?")
        box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        rev = box.addButton("Review…", QMessageBox.AcceptRole)
        box.addButton("Not now", QMessageBox.RejectRole)
        box.setModal(False)
        box.buttonClicked.connect(lambda b: self.show_offer(oid) if b is rev else self.dismiss_offer(oid))
        self.popup_box = box
        box.show()
        box.raise_()

    def show_offer(self, oid):
        o = self.offers.get(oid)
        if not o or not self.vault:
            return
        if self.offer_dialog:
            self.offer_dialog.close()
        self.offer_dialog = CaptureDialog(self, o)
        self.offer_dialog.finished.connect(lambda r, i=oid: self.offers.pop(i, None))
        self.offer_dialog.show()
        self.offer_dialog.raise_()
        self.offer_dialog.activateWindow()

    def dismiss_offer(self, oid):
        self.offers.pop(oid, None)

    def save_captured(self, offer, new):
        try:
            if offer["kind"] == "change":
                self.vault.edit(offer["existing"], new)
                self.update_twins(offer["existing"], new)
            else:
                taken = {e["title"].lower() for e in self.entries if e["group"] == new["group"]}
                if new["title"].lower() in taken:
                    new["title"] = f"{new['title']} ({new['username']})" if new["username"] else f"{new['title']} 2"
                self.vault.ensure_group(new["group"])
                self.vault.add(new)
                self.update_twins(None, new)
            self.log(f"Saved password for {offer['host']}")
            self.reload(select=False)
            self.toast(f"Saved the password for {offer['host']}", "good", 5000)
            return True
        except core.VaultError as e:
            QMessageBox.warning(self.offer_dialog or self, "Couldn't save", str(e))
            return False

    def on_ipc(self, sock):
        data = bytes(sock.readAll()).strip()
        if data.startswith(b"{"):
            reply = {"ok": False}
            try:
                m = json.loads(data.split(b"\n")[0].decode("utf-8", "replace"))
                oid = int(m.get("id"))
                if m.get("cmd") == "show_offer":
                    self.show_offer(oid)
                    reply = {"ok": True}
                elif m.get("cmd") == "dismiss_offer":
                    self.dismiss_offer(oid)
                    reply = {"ok": True}
            except (ValueError, TypeError):
                pass
            sock.write((json.dumps(reply) + "\n").encode())
            sock.flush()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        sock.disconnectFromServer()

    # ------------------------------------------------------------------ phone copy
    def toggle_phone(self, on):
        self.s["phone_enabled"] = on
        sp = self.settings_page
        if on and not self.s["phone_key"]:
            self.s["phone_key"] = core.new_phone_key()
            sp.phone_key_box.setText(self.s["phone_key"])
        self.save()
        if on:
            self.send_phone()
        else:
            self.phone_status = "Off. The copy already on the server stays until you remove it."
        sp.refresh()

    def new_phone_key_ui(self):
        if QMessageBox.question(self, "New phone key", "Make a new key? The phone can't read the copy until you type the new key into it.") != QMessageBox.Yes:
            return
        self.s["phone_key"] = core.new_phone_key()
        self.save()
        self.settings_page.phone_key_box.setText(self.s["phone_key"])
        self.send_phone(manual=True)

    def remove_phone(self):
        if QMessageBox.question(self, "Remove the phone copy", "Delete the encrypted copy from the server? The phone will show nothing until you turn it on again.") != QMessageBox.Yes:
            return
        try:
            core.phone_request("DELETE")
            self.phone_status = "Removed from the server."
        except core.VaultError as e:
            self.phone_status = str(e)
        self.settings_page.refresh()

    def send_phone(self, manual=False):
        logins = getattr(self, "entries", [])
        if not self.vault or self.phone_worker or not logins or not (self.s["phone_enabled"] or manual) or not self.s["phone_key"]:
            return
        self.phone_worker = core.PhoneWorker(list(logins), self.s["phone_key"])
        self.phone_worker.done.connect(self.phone_done)
        self.phone_status = "Sending…"
        self.phone_worker.start()

    def phone_done(self, msg, err):
        if self.phone_worker:
            self.phone_worker.wait()
        self.phone_worker = None
        self.phone_status = f"{msg}, {time.strftime('%H:%M')}" if not err else f"Last send failed at {time.strftime('%H:%M')}: {err}"
        if self.page == "settings":
            self.settings_page.refresh()

    # ------------------------------------------------------------------ browser bridge
    def bridge_state(self):
        return (self.vault is None, getattr(self, "bridge_entries", []) if self.vault else [])

    def write_pairing_file(self):
        d = core.HOME / "PasswordManager-Extension"
        if not d.is_dir() or not self.s["bridge_token"]:
            return
        try:
            f = d / "pairing.json"
            f.write_text(json.dumps({"token": self.s["bridge_token"], "port": int(self.s["bridge_port"])}))
            f.chmod(0o600)
        except OSError:
            pass

    def start_bridge(self):
        if not self.s["bridge_token"]:
            self.s["bridge_token"] = secrets.token_urlsafe(24)
            self.save()
        self.write_pairing_file()
        if self.bridge:
            self.bridge.stop()
            self.bridge = None
        if not self.s["bridge_enabled"]:
            self.bridge_note = "Off."
            return
        try:
            self.bridge = core.Bridge(int(self.s["bridge_port"]), self.s["bridge_token"], self.bridge_state, lambda h, n: self.signals.filled.emit(h, n), lambda d: self.signals.captured.emit(d),
                                      self.passkey_from_bridge)
            self.bridge.start()
            self.bridge_note = f"Listening on 127.0.0.1:{self.s['bridge_port']} (this computer only)."
        except OSError as e:
            self.bridge_note = f"Couldn't start (port {self.s['bridge_port']} busy: {e}). Is another copy of the app running?"

    def bridge_text(self):
        if not self.s["bridge_enabled"]:
            return "Off."
        if not self.bridge:
            return self.bridge_note
        last = self.bridge.last
        if not last:
            return (f"{self.bridge_note}\nNo browser has contacted the app yet. After installing the extension, click its icon, open Pairing, paste the code and press Save. Then open a login page.")
        when = time.strftime("%H:%M:%S", time.localtime(last["at"]))
        meaning = {200: f"a browser asked for {last['host'] or 'status'} and got an answer", 401: "the extension has the WRONG pairing code (paste the current one into its Pairing box)",
                   403: "a request was refused because it didn't come from the browser extension", 423: "the browser asked while the vault was locked (plug in the stick)",
                   400: "the page wasn't https, so nothing was offered"}.get(last["code"], f"answered {last['code']}")
        return f"{self.bridge_note}\nLast contact from a browser at {when}: {meaning}."

    # -- passkeys: a website asks (through the browser extension), you approve here
    def passkey_from_bridge(self, kind, body):
        """Runs in the bridge's thread: hand the request to the window and wait for your answer (up to two minutes)."""
        if not self.s.get("passkeys_enabled", True):
            return {"available": 0} if kind == "query" else {"fallback": True}
        if kind == "query":                                       # no window, just: could this app answer?
            try:
                rp = passkeys.check_origin(str(body.get("origin", "")), str(body.get("rpId", "")))
                return {"available": len(passkeys.find(self.entries, rp, body.get("allowCredentials") or None))}
            except passkeys.PasskeyError:
                return {"available": 0}
        box = {"event": threading.Event(), "result": None}
        self.signals.passkey.emit(kind, body, box)
        box["event"].wait(130)
        return box["result"] or {"error": "NotAllowedError", "message": "No answer in time."}

    def on_passkey(self, kind, body, box):
        try:
            box["result"] = self.passkey_answer(kind, body)
        except passkeys.PasskeyError as e:
            box["result"] = {"error": e.name, "message": str(e)}
        except Exception as e:                                    # never leave the page hanging
            self.log(f"Passkey request failed: {type(e).__name__}")
            box["result"] = {"error": "NotAllowedError", "message": "Something went wrong in the Password Manager."}
        finally:
            box["event"].set()

    def passkey_answer(self, kind, body):
        """The answer to one request. May show a window. Raises PasskeyError for a refusal the page should see."""
        if not self.vault:
            raise passkeys.PasskeyError("NotAllowedError", "The vault is locked.")
        origin = str(body.get("origin", ""))
        rp_id = passkeys.check_origin(origin, str(body.get("rpId", "")))
        try:
            chash = passkeys.unb64u(str(body.get("clientDataHash", "")))
        except ValueError:
            chash = b""
        if len(chash) != 32:
            raise passkeys.PasskeyError("NotAllowedError", "Bad request.")
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if kind == "create":
            user = {k: str((body.get("user") or {}).get(k, ""))[:200] for k in ("id", "name", "displayName")}
            name = str(body.get("rpName") or rp_id)[:80]
            algs = [int(a.get("alg")) for a in (body.get("pubKeyCredParams") or []) if isinstance(a, dict) and isinstance(a.get("alg"), int)]
            existing = passkeys.find(self.entries, rp_id)
            if any((e["data"].get("cred_id") in (body.get("excludeCredentials") or [])) for e in existing):
                raise passkeys.PasskeyError("InvalidStateError", "You already have a passkey for this account.")
            same = [e for e in existing if e["data"].get("user_handle") == user["id"] and user["id"]]
            if algs and passkeys.ALG_ES256 not in algs:
                raise passkeys.PasskeyError("NotSupportedError", "This site doesn't accept the kind of passkey this app makes.")
            what, _ = PasskeyPrompt(self, "create", name, rp_id, [user["name"] or user["displayName"]], replaces=bool(same)).run()
            if what == "browser":
                return {"fallback": True}
            if what != "allow":
                raise passkeys.PasskeyError("NotAllowedError", "You said no.")
            record, answer = passkeys.create(rp_id, name, user, chash, algs)
            tags = model.item_tags(same[0]) if same else []
            item = passkeys.as_item(record, tags or None)
            if same:
                item["title"] = same[0]["title"]
                self.vault.edit(same[0], dict(same[0], data=item["data"], username=item["username"]))
                self.reload(select=False)
            else:
                self.save_item(None, item, quiet=True)
            self.log(f"Passkey saved for {rp_id}" + (" (replaced the old one)" if same else ""))
            self.toast(f"Passkey saved for {rp_id}", "good")
            return {"ok": True, **answer, "transports": ["internal", "hybrid"]}
        if kind == "get":
            allow = body.get("allowCredentials") or None
            cands = passkeys.find(self.entries, rp_id, allow)
            if not cands:
                return {"fallback": True}
            names = [c["data"].get("user_name") or c["data"].get("display_name") or c["title"] for c in cands]
            what, idx = PasskeyPrompt(self, "get", cands[0]["data"].get("rp_name") or rp_id, rp_id, names).run()
            if what == "browser":
                return {"fallback": True}
            if what != "allow":
                raise passkeys.PasskeyError("NotAllowedError", "You said no.")
            self.log(f"Passkey used to sign in to {rp_id}")
            return {"ok": True, **passkeys.sign(cands[idx]["data"], chash)}
        raise passkeys.PasskeyError("NotSupportedError", "Unknown request.")

    def toggle_bridge(self, on):
        self.start_bridge()
        self.settings_page.refresh()

    def new_pairing(self):
        if QMessageBox.question(self, "New pairing code", "Make a new code? The browser extension stops working until you paste the new code into it.") == QMessageBox.Yes:
            self.s["bridge_token"] = secrets.token_urlsafe(24)
            self.save()
            self.settings_page.pair_code.setText(self.s["bridge_token"])
            self.start_bridge()

    def forget_apps(self):
        self.s["apps"] = {}
        self.save()
        self.settings_page.refresh()

    # ------------------------------------------------------------------ clipboard
    def copy_plain(self, text, what):
        QApplication.clipboard().setText(text)
        self.toast(f"{what} copied", "good", 2200)

    def copy_secret(self, text):
        if not text:
            return
        cb = QApplication.clipboard()
        cb.setText(text)
        token = object()
        self.clip_token = token
        secs = self.s["clip_clear_s"]
        self.toast("Copied" + (f". It will be cleared in {secs} seconds." if secs else ""), "good", 3500)
        if secs:
            def clear():
                if self.clip_token is token and cb.text() == text:
                    cb.clear()
                    self.toast("Clipboard cleared", "", 2000)
            QTimer.singleShot(secs * 1000, clear)


# ------------------------------------------------------------------ "a new password was found" confirm window
class CaptureDialog(QDialog):
    """Shows the password the browser saw, lets you look at it and change it, then Save or Cancel. The password is never sent to the cat."""

    def __init__(self, win, offer):
        super().__init__(None)
        self.win, self.offer = win, offer
        self.setWindowTitle(f"{core.APP_NAME} · new password found")
        self.setMinimumWidth(500)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        if win.windowIcon():
            self.setWindowIcon(win.windowIcon())
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 24, 26, 22)
        v.setSpacing(12)
        change = offer["kind"] == "change"
        v.addWidget(label("Update this password?" if change else "Add this to your vault?", "h2"))
        v.addWidget(label(f"Found on {offer['host']}." + (f" It's different from the one saved as “{offer['existing']['title']}”." if change else ""), "muted", True))
        self.title = QLineEdit(offer["existing"]["title"] if change else (core.reg_domain(offer["host"]) or offer["host"]))
        self.user = QLineEdit(offer["user"])
        self.pw = QLineEdit(offer["pw"])
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setProperty("role", "mono")
        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItems([g for g in (win.groups or core.STARTER_GROUPS) if g not in model.KIND_GROUP.values()])
        self.group.setCurrentText(offer["existing"]["group"] if change else "Websites")
        from .pages.items import form_row
        from .widgets import IconButton
        v.addWidget(form_row("Name", self.title))
        v.addWidget(form_row("Username", self.user))
        v.addWidget(form_row("Password", self.pw, IconButton("eye", "Show", fn=self.toggle)))
        v.addWidget(form_row("Folder", self.group))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Cancel", "", self.reject))
        row.addWidget(button("Update" if change else "Save", "primary", self.save, "check"))
        v.addLayout(row)

    def toggle(self):
        self.pw.setEchoMode(QLineEdit.Normal if self.pw.echoMode() == QLineEdit.Password else QLineEdit.Password)

    def save(self):
        o = self.offer
        base = o["existing"] if o["kind"] == "change" else {"notes": "", "otp": "", "history": [], "modified": "", "kind": "login", "data": {}}
        new = {**base, "group": self.group.currentText().strip().strip("/") or "Websites", "title": self.title.text().strip() or o["host"], "username": self.user.text().strip(),
               "password": self.pw.text(), "url": base.get("url") or f"https://{o['host']}", "pending_for": None}
        if not new["password"]:
            return
        if self.win.save_captured(o, new):
            self.accept()


# ------------------------------------------------------------------ starting the app
def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    core.CONF_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication(argv)
    app.setApplicationName("jblol-password-manager")
    app.setDesktopFileName("jblol-password-manager")
    theme.apply(app)
    demo = "--demo" in argv
    if demo:
        win = Main(demo=True)
        app.window = win
        win.show()
        return app.exec()
    lock = QLockFile(str(core.CONF_DIR / "app.lock"))
    lock.setStaleLockTime(0)
    name = f"jblol-pm-{os.getuid()}"
    if not lock.tryLock(100):
        s = QLocalSocket()
        s.connectToServer(name)
        if s.waitForConnected(500):
            s.write(b"show")
            s.waitForBytesWritten(500)
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(name)
    server.setSocketOptions(QLocalServer.UserAccessOption)
    server.listen(name)
    win = Main()
    app.window = win
    if "--minimized" not in argv:
        win.show()

    def new_conn():
        while server.hasPendingConnections():
            sk = server.nextPendingConnection()
            sk.readyRead.connect(lambda sk=sk: win.on_ipc(sk))
            QTimer.singleShot(3000, sk.deleteLater)
    server.newConnection.connect(new_conn)
    code = app.exec()
    lock.unlock()
    return code
