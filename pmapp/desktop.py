"""Things the app does in the background of your desktop: lock with the screen, unlock the screen with the stick, and fill logins into
desktop apps when their window opens (through the small GNOME window-helper extension). Mixed into the main window."""
import ast
import re
import shutil
import subprocess
import threading
import time

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtWidgets import QComboBox, QMessageBox

from jblol_pm import (APP_NAME, SKIP_APPS, focused_app, log_event, match_app_entries, match_site_entries_for_app, norm, save_settings, stick_is_mine,
                      type_login_text)


class DesktopMixin:
    def watch_bus(self, args, signal, fn):
        """Follows a D-Bus signal by reading `gdbus monitor` (this Python install has no D-Bus module)."""
        gdbus = shutil.which("gdbus")
        if not gdbus:
            log_event("gdbus not found, so the screen lock can't be followed")
            return None
        proc = QProcess(self)
        buf = {"t": ""}

        def read():
            buf["t"] += bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
            *lines, buf["t"] = buf["t"].split("\n")
            for ln in lines:
                if f".{signal} (" in ln:
                    fn(ln.split(f".{signal} (", 1)[1])
        proc.readyReadStandardOutput.connect(read)
        proc.finished.connect(lambda *_: QTimer.singleShot(5000, lambda: proc.start(gdbus, ["monitor"] + args)))
        proc.start(gdbus, ["monitor"] + args)
        return proc

    def on_window_opened(self, raw):
        """The window helper says an application window just opened."""
        try:
            appid, appname, wmclass, title, _pid = ast.literal_eval(re.sub(r"uint32 (\d+)", r"\1", "(" + raw.strip()))
        except (ValueError, SyntaxError):
            return
        if not (self.vault and self.s["app_fill"] and not self.screen_locked):
            return
        low = " ".join((norm(appid), norm(wmclass), norm(appname)))
        if any(k in low for k in SKIP_APPS):
            return
        key = norm(appid) or norm(wmclass)
        info = self.s["apps"].get(key, {})
        if info.get("mode") == "never":
            return
        cands = match_app_entries(self.entries, appid, appname, wmclass)
        from_site = not cands
        if from_site:                                   # nothing saved under the app's name: try its website's logins
            cands = match_site_entries_for_app(self.entries, appid, appname, wmclass)
        if not cands or time.time() - getattr(self, "_app_last", {}).get(key, 0) < 20:
            return
        self.__dict__.setdefault("_app_last", {})[key] = time.time()
        pick = next((e for e in cands if f"{e['group']}/{e['title']}" == info.get("entry")), cands[0])
        if info.get("mode") == "always":
            self.fill_app(appid, appname, pick)
        else:
            self.ask_app(key, appid, appname, cands, from_site)

    def ask_app(self, key, appid, appname, cands, from_site=False):
        box = QMessageBox(None)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Question)
        box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        box.setText(f"{appname or appid} just opened.")
        names = ", ".join(f"“{e['title']}”" for e in cands[:3])
        box.setInformativeText(f"You have a saved login for its website: {names}. Fill it in?" if from_site else
                               f"You have a saved login for it: {names}. Fill it in?")
        combo = None
        if len(cands) > 1:
            combo = QComboBox()
            for e in cands:
                combo.addItem(f"{e['title']}  ·  {e['username']}", e)
            box.layout().addWidget(combo, 3, 0, 1, box.layout().columnCount())
        once = box.addButton("Fill now", QMessageBox.AcceptRole)
        always = box.addButton("Always fill this app", QMessageBox.AcceptRole)
        never = box.addButton("Never for this app", QMessageBox.DestructiveRole)
        box.addButton("Not now", QMessageBox.RejectRole)
        box.setModal(False)

        def done(b):
            e = combo.currentData() if combo else cands[0]
            if b in (once, always):
                if b is always:
                    self.s["apps"][key] = {"mode": "always", "entry": f"{e['group']}/{e['title']}"}
                    save_settings(self.s)
                self.fill_app(appid, appname, e, ask_focus=False)
            elif b is never:
                self.s["apps"][key] = {"mode": "never"}
                save_settings(self.s)
        box.buttonClicked.connect(done)
        self.app_box = box
        box.show()
        box.raise_()

    def fill_app(self, appid, appname, e, ask_focus=True):
        if not (stick_is_mine(self.keyfile) or self.remote_key):
            self.toast("Plug in your stick to fill app logins")
            return

        def run():
            time.sleep(0.3 if not ask_focus else 0.2)
            f = focused_app()
            if ask_focus and (not f or f[0] != appid):       # you switched to another window: never type into it
                log_event(f"{appname or appid} opened, but it wasn't the focused window, so nothing was typed")
                return
            if not ask_focus:                                # you just answered the question: give focus back to the app first
                for _ in range(20):
                    f = focused_app()
                    if f and f[0] == appid:
                        break
                    time.sleep(0.25)
                else:
                    log_event(f"{appname or appid}: couldn't tell it had focus, so nothing was typed")
                    return
            err = type_login_text(e["username"], e["password"])
            log_event(f"Filled the login “{e['title']}” into {appname or appid}" if not err else f"Fill for {appname or appid} failed: {err}")
        threading.Thread(target=run, daemon=True).start()

    def on_screen_active(self, locked):
        """The screen locked or unlocked (Super+L, idle lock, closing the lid)."""
        self.screen_locked = bool(locked)
        log_event("Screen locked" if locked else "Screen unlocked")
        if locked:
            if self.vault and self.s["lock_on_screen"] and not self.manual_key:
                self.lock_now(msg="The screen was locked, so your passwords were locked too.")
        elif not self.vault and self.s["lock_on_screen"] and self.keyfile and self.was_plugged:
            self.unlock()
        else:
            self.remote_paused = False          # back at the computer: the stick in your other one may open them again

    def on_sleep(self, going):
        if going and self.vault and self.s["lock_on_screen"] and not self.manual_key:
            log_event("Computer going to sleep")
            self.lock_now(msg="The computer went to sleep, so your passwords were locked.")

    def unlock_screen_with_stick(self):
        """Plugging the real stick in while the screen is locked unlocks the screen, so you don't have to type your password."""
        if not (self.s["unlock_screen_with_stick"] and self.screen_locked):
            return
        if not stick_is_mine(self.keyfile):
            log_event("A USB stick was plugged in while the screen was locked, but it isn't your stick, so the screen stayed locked")
            return
        try:
            subprocess.run(["loginctl", "unlock-session"], timeout=10, check=True, capture_output=True)
            log_event("Screen unlocked by your USB stick")
        except (OSError, subprocess.SubprocessError) as e:
            log_event(f"Couldn't unlock the screen with the stick: {e}")
