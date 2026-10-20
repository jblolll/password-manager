"""The new Password Manager window (pmapp/): the data model, the QR maker, and the pages, run in demo mode with no screen and no vault.

    QT_QPA_PLATFORM=offscreen python3 -m unittest tests.test_pmapp -v      (from the password-manager folder)
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["JBLOL_PM_CONF"] = tempfile.mkdtemp(prefix="pm-ui-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jblol_pm as core  # noqa: E402
_conf = Path(os.environ["JBLOL_PM_CONF"])
core.CONF_DIR, core.SETTINGS, core.EVENT_LOG = _conf, _conf / "settings.json", _conf / "events.log"     # another test file may have imported it first: never touch the real log
from pmapp import model, qr  # noqa: E402


class Model(unittest.TestCase):
    def test_typed_items_ride_in_the_notes_and_come_back(self):
        notes = model.pack("keep this", "card", {"number": "4242424242424242", "fav": True, "cvv": ""})
        plain, kind, data = model.unpack(notes)
        self.assertEqual((plain, kind), ("keep this", "card"))
        self.assertEqual(data, {"number": "4242424242424242", "fav": True})        # empty values aren't stored
        self.assertEqual(model.unpack("just a note"), ("just a note", "login", {}))
        self.assertEqual(model.pack("x", "login", {}), "x", "a plain login stores nothing extra")
        self.assertEqual(model.unpack("pm-kind|bogus\nx")[1], "login")
        self.assertEqual(model.unpack("pm-data|{broken\nx")[2], {})

    def test_cards(self):
        self.assertEqual(model.card_brand("4242 4242 4242 4242"), "Visa")
        self.assertEqual(model.card_brand("378282246310005"), "Amex")
        self.assertEqual(model.card_brand("5555555555554444"), "Mastercard")
        self.assertEqual(model.card_brand("2223003122003222"), "Mastercard")
        self.assertEqual(model.card_brand("6011111111111117"), "Discover")
        self.assertTrue(model.luhn_ok("4242424242424242"))
        self.assertFalse(model.luhn_ok("4242424242424241"))
        self.assertEqual(model.card_text("4242424242424242", hidden=False), "4242 4242 4242 4242")
        self.assertEqual(model.card_text("4242424242424242"), "•••• •••• •••• 4242")
        self.assertEqual(model.card_text("378282246310005", hidden=False), "3782 822463 10005")
        now = time.mktime((2026, 10, 19, 0, 0, 0, 0, 0, -1))
        self.assertTrue(model.card_expired("01/24", now))
        self.assertTrue(model.card_expired("09/2026", now))
        self.assertFalse(model.card_expired("10/26", now))
        self.assertFalse(model.card_expired("12/28", now))
        self.assertFalse(model.card_expired("soon", now))

    def test_wifi_text_for_phones(self):
        self.assertEqual(model.wifi_uri("Home", "pw"), "WIFI:T:WPA;S:Home;P:pw;;")
        self.assertEqual(model.wifi_uri("My;Net", "pa:ss\\x"), r"WIFI:T:WPA;S:My\;Net;P:pa\:ss\\x;;")
        self.assertEqual(model.wifi_uri("Cafe", "", "nopass"), "WIFI:T:nopass;S:Cafe;;")
        self.assertIn("H:true;", model.wifi_uri("Hidden", "pw", hidden=True))

    def test_passphrase_and_pin(self):
        p = model.passphrase(6, "-", cap=True, number=True)
        self.assertEqual(len(p.split("-")), 6)
        self.assertTrue(all(w[0].isupper() for w in p.split("-")))
        self.assertTrue(any(c.isdigit() for c in p))
        self.assertEqual(len(model.pin(8)), 8)
        self.assertGreaterEqual(len(model.words()), 1296)

    def test_audit_log_lines(self):
        e = model.parse_event_line("2026-10-19 09:10:11  USB stick plugged in")
        self.assertEqual((e["text"], e["cat"]), ("USB stick plugged in", "stick"))
        self.assertIsNone(model.parse_event_line("garbage"))
        cats = {"The stick was pulled: told desktop to lock": "device", "sudo: stick accepted": "approve", "Sync: Up to date": "sync",
                "Vault locked: The stick was removed, so your passwords were locked.": "vault", "Created a share link for 1 item(s)": "share"}
        for text, cat in cats.items():
            self.assertEqual(model.category_of(text), cat, text)


class QR(unittest.TestCase):
    def test_sizes_and_finder_patterns(self):
        for text, size in (("a", 21), ("x" * 20, 25), ("WIFI:T:WPA;S:Net;P:" + "p" * 60 + ";;", 37)):
            g = qr.make(text)
            self.assertEqual(len(g), size, text[:10])
            for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):                  # the three corner squares
                self.assertTrue(all(g[r0][c0 + i] and g[r0 + 6][c0 + i] and g[r0 + i][c0] and g[r0 + i][c0 + 6] for i in range(7)))
                self.assertFalse(g[r0 + 1][c0 + 1] or g[r0 + 5][c0 + 5])
                self.assertTrue(g[r0 + 3][c0 + 3])
            self.assertTrue(g[size - 8][8], "the always-dark module")
            self.assertEqual([g[6][i] for i in range(8, size - 8)], [i % 2 == 0 for i in range(8, size - 8)], "timing pattern")

    def test_format_information_is_a_valid_code_word(self):
        for text in ("hi", "WIFI:T:WPA;S:Home;P:secret-password;;", "z" * 150):
            g = qr.make(text)
            n = len(g)
            fmt = sum(int(g[i if i < 6 else i + 1 if i < 8 else n - 15 + i][8]) << i for i in range(15))
            self.assertEqual(qr._bch(fmt ^ 0x5412, 0x537, 0), 0, "a valid BCH(15,5) word")
            copy = sum(int(g[8][n - 1 - i if i < 8 else 7 if i == 8 else 14 - i]) << i for i in range(15))
            self.assertEqual(fmt, copy, "both copies agree")

    def test_data_survives_a_round_trip(self):
        """Read the code words back out of the picture and check each block's error-correction bytes."""
        for text, level in (("WIFI:T:WPA;S:Home;P:abc;;", "M"), ("x" * 100, "M"), ("y" * 150, "L"), ("é☃ wifi", "M")):
            g = qr.make(text, level)
            n = len(g)
            ver = (n - 17) // 4
            fmt = sum(int(g[i if i < 6 else i + 1 if i < 8 else n - 15 + i][8]) << i for i in range(15)) ^ 0x5412
            mask = fmt >> 10 & 7
            # function-module map: the same code with all data modules free
            func = [[False] * n for _ in range(n)]
            def mark(r0, c0, h, w):
                for r in range(r0, r0 + h):
                    for c in range(c0, c0 + w):
                        if 0 <= r < n and 0 <= c < n:
                            func[r][c] = True
            mark(0, 0, 9, 9); mark(0, n - 8, 9, 8); mark(n - 8, 0, 8, 9)
            for i in range(n):
                func[6][i] = func[i][6] = True
            pos = qr._ALIGN[ver - 1]
            for r in pos:
                for c in pos:
                    if (r, c) not in ((6, 6), (6, pos[-1]), (pos[-1], 6)):
                        mark(r - 2, c - 2, 5, 5)
            if ver >= 7:
                mark(0, n - 11, 6, 3); mark(n - 11, 0, 3, 6)
            bits, up = [], True
            for c in range(n - 1, 0, -2):
                if c <= 6:
                    c -= 1
                for r in (range(n - 1, -1, -1) if up else range(n)):
                    for cc in (c, c - 1):
                        if not func[r][cc]:
                            v = g[r][cc] != qr._mask_fn(mask)(r, cc)
                            bits.append(int(v))
                up = not up
            cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits) - 7, 8)]
            table = qr._M if level == "M" else qr._L
            ec, groups = table[ver - 1]
            sizes = [d for cnt, d in groups for _ in range(cnt)]
            total_data = sum(sizes)
            blocks = [[] for _ in sizes]
            idx = 0
            for k in range(max(sizes)):
                for b, d in enumerate(sizes):
                    if k < d:
                        blocks[b].append(cw[idx]); idx += 1
            eccs = [[] for _ in sizes]
            for k in range(ec):
                for b in range(len(sizes)):
                    eccs[b].append(cw[idx]); idx += 1
            for b, data in enumerate(blocks):
                self.assertEqual(qr._rs(data, ec), eccs[b], f"block {b} of {text[:12]!r}")
            stream = "".join(format(x, "08b") for blk in blocks for x in blk)
            self.assertEqual(stream[:4], "0100", "byte mode")
            width = 8 if ver < 10 else 16
            length = int(stream[4:4 + width], 2)
            body = bytes(int(stream[4 + width + 8 * i:4 + width + 8 * i + 8], 2) for i in range(length))
            self.assertEqual(body.decode("utf-8"), text)

    def test_too_long_is_refused(self):
        with self.assertRaises(ValueError):
            qr.make("x" * 400)


from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from pmapp import theme, window  # noqa: E402
theme.apply(app)


def pump(n=5):
    for _ in range(n):
        app.processEvents()


class Pages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = window.Main(demo=True)
        cls.w.resize(1300, 820)
        cls.w.show()
        pump()

    def setUp(self):
        from pmapp import demo
        self.w.vault = demo.DemoVault()
        self.w.set_entries(self.w.vault.load())
        self.w.s["warn_passwords"] = True
        self.w.s["warn_security"] = True
        self.w.rebuild_nav()
        self.w.go("vault")

    def find(self, title):
        return next(e for e in self.w.entries if e["title"] == title)

    def test_every_page_builds(self):
        for page in ("vault", "gen", "health", "usb", "audit", "settings"):
            self.w.go(page)
            pump()
            self.assertEqual(self.w.pages.currentIndex(), window.PAGE_INDEX[page])

    def test_filters_and_counts(self):
        self.w.go("vault", kind="card")
        self.assertEqual(self.w.vault_page.list.count(), 3)
        self.w.go("vault", kind="wifi")
        self.assertEqual(self.w.vault_page.list.count(), 2)
        self.w.go("vault", fav=True)
        self.assertEqual(self.w.vault_page.list.count(), 4)
        self.assertEqual(self.w.nav["v:card"].badge.text(), "3")
        self.w.go("vault")
        self.w.vault_page.search.setText("netflix")
        pump()
        self.assertEqual(self.w.vault_page.list.count(), 1)
        self.w.vault_page.search.setText("")
        self.w.vault_page.search.setText("MySpectrum")                              # finds a Wi-Fi network by its network name
        self.assertEqual(self.w.vault_page.list.count(), 1)
        self.w.vault_page.search.setText("")

    def test_clicking_the_sidebar_opens_each_page(self):
        """A real click on each sidebar button (this once passed a True/False into the page filter, so Logins, Cards, Notes, Wi-Fi and every folder did nothing)."""
        w, vp = self.w, self.w.vault_page
        for key, kind in (("v:login", "login"), ("v:card", "card"), ("v:note", "note"), ("v:wifi", "wifi")):
            w.nav[key].click()
            pump()
            self.assertEqual((w.page, vp.f_kind), ("vault", kind), key)
            self.assertTrue(all(__import__("pmapp.model", fromlist=["x"]).kind_of(vp.list.item(i).data(0x100)) == kind for i in range(vp.list.count())), key)
        w.nav["v:fav"].click()
        self.assertTrue(vp.f_fav)
        for key in [k for k in w.nav if k.startswith("g:")]:
            w.nav[key].click()
            pump()
            self.assertEqual(vp.f_group, key[2:], key)
            self.assertTrue(all(vp.list.item(i).data(0x100)["group"] == key[2:] for i in range(vp.list.count())), key)
        w.nav["v:all"].click()
        self.assertEqual((vp.f_kind, vp.f_group, vp.f_fav), (None, None, False))
        for key, page in (("gen", "gen"), ("health", "health"), ("usb", "usb"), ("audit", "audit"), ("settings", "settings")):
            w.nav[key].click()
            self.assertEqual(w.page, page)

    def test_a_login_reused_everywhere_keeps_a_short_warning_and_a_page_that_fits(self):
        """Twenty-two logins sharing one password once gave a warning as tall as the window and a page stretched sideways past its panel."""
        import jblol_pm as core
        shared = []
        for i in range(22):
            shared.append({"group": "Websites", "title": f"very-long-site-name-{i}.example.com (someone{i}@some-long-mail-provider.example.org)", "username": "u",
                           "password": "sunshine1", "url": f"https://very-long-site-name-{i}.example.com/login", "notes": "", "otp": "", "history": [], "pending_for": None,
                           "modified": "", "kind": "login", "data": {}})
        for e in shared:
            self.w.vault.add(e)
        self.w.set_entries(self.w.vault.load())
        iss = core.compute_health(self.w.entries)[("Websites", shared[0]["title"])]
        reused = next(t for k, t in iss if k == "reused")
        self.assertLess(len(reused), 260, reused)
        self.assertIn("and 18 more", reused)
        self.w.resize(1120, 700)
        self.w.go("vault")
        self.w.vault_page.select(next(e for e in self.w.entries if e["title"] == shared[0]["title"]))
        pump(10)
        v = self.w.vault_page.detail.view
        sc = v.findChild(type(v.host.parent().parent()))
        self.assertLessEqual(v.host.width(), v.host.parent().width(), "the page must not be wider than its window")
        ban = v.host.findChild(type(v.host), "warnbanner")
        self.assertIsNotNone(ban)
        self.assertLess(ban.height(), 190, "the warning stays a small box")
        self.w.resize(1300, 820)

    def test_empty_states_keep_their_text_readable(self):
        """"Nothing here" and "Select an item" once squeezed their sentence into a sliver one word wide."""
        self.w.go("vault", kind="card")
        self.w.vault_page.search.setText("zzz-no-match")
        pump(8)
        vp = self.w.vault_page
        for es in (vp.empty_list, vp.detail.empty):
            sentence = [l for l in es.findChildren(type(vp.title)) if l.wordWrap()][0]
            self.assertGreaterEqual(sentence.width(), 250, sentence.text())
            self.assertLessEqual(sentence.sizeHint().height() if False else sentence.heightForWidth(sentence.width()), 90)
        vp.search.setText("")

    def test_refreshing_a_page_does_not_stack_up_its_headings(self):
        self.w.go("usb")
        for _ in range(3):
            self.w.usb_page.refresh()
            pump()
        labels = [l.text() for l in self.w.usb_page.findChildren(type(self.w.vault_page.title)) if l.isVisibleTo(self.w.usb_page)]
        self.assertEqual(labels.count("YOUR COMPUTERS"), 1)
        self.assertEqual(labels.count("PROTECTION ON THIS COMPUTER"), 1)
        self.assertFalse(any("Accept lock orders" in t for t in labels), "computers always obey a lock order: there is no switch")
        self.w.go("health")
        for _ in range(3):
            self.w.health_page.refresh()
        pump()

    def test_each_kind_of_item_shows(self):
        for title in ("GitHub", "Chase Sapphire", "Locker combination", "Home Wi-Fi"):
            self.w.vault_page.search.setText("")
            self.w.go("vault")
            self.w.vault_page.select(self.find(title))
            pump()
            self.assertIs(self.w.vault_page.detail.stack.currentWidget(), self.w.vault_page.detail.view, title)

    def test_new_card_is_saved_with_a_name_and_grouped_number(self):
        vp = self.w.vault_page
        self.w.new_item("card")
        ed = vp.detail.editor
        ed.w["number"].setText("4242424242424242")
        ed.w["holder"].setText("Justin Q Brook")
        ed.w["expiry"].setText("0829")
        self.assertEqual(ed.w["number"].text(), "4242 4242 4242 4242")
        self.assertEqual(ed.w["expiry"].text(), "08/29")
        ed.save()
        pump()
        e = next(x for x in self.w.entries if (x.get("data") or {}).get("holder") == "Justin Q Brook")
        self.assertEqual(e["title"], "Visa •••• 4242", "an unnamed card is named after its brand and last digits")
        self.assertEqual((e["kind"], e["group"], e["data"]["number"], e["data"]["expiry"]), ("card", "Cards", "4242424242424242", "08/29"))
        self.assertEqual(e["password"], "", "a card number is never treated as a password")

    def test_new_wifi_and_note_and_login(self):
        vp = self.w.vault_page
        self.w.new_item("wifi")
        vp.detail.editor.w["ssid"].setText("Cafe Net")
        vp.detail.editor.w["password"].setText("latte-2026")
        vp.detail.editor.save()
        w = next(x for x in self.w.entries if x["title"] == "Cafe Net")
        self.assertEqual((w["kind"], w["password"], w["data"]["ssid"]), ("wifi", "latte-2026", "Cafe Net"))
        self.w.new_item("note")
        vp.detail.editor.w["title"].setText("Door code")
        vp.detail.editor.w["notes"].setPlainText("1234#")
        vp.detail.editor.save()
        self.assertEqual(self.find("Door code")["notes"], "1234#")
        self.w.new_item("login", password="Zq9!vL2#mXk8")
        ed = vp.detail.editor
        self.assertEqual(ed.w["password"].text(), "Zq9!vL2#mXk8")
        ed.w["title"].setText("Example")
        ed.w["username"].setText("me@example.com")
        ed.save()
        self.assertEqual(self.find("Example")["username"], "me@example.com")

    def test_editor_refuses_a_nameless_login_and_a_bad_setup_key(self):
        vp = self.w.vault_page
        self.w.new_item("login")
        ed = vp.detail.editor
        ed.save()
        self.assertIn("name", ed.err.text().lower())
        ed.w["title"].setText("X")
        ed.w["otp"].setText("not a key!!")
        ed.save()
        self.assertTrue(ed.err.text())
        self.assertFalse(any(e["title"] == "X" for e in self.w.entries))

    def test_favorite_and_delete(self):
        vp = self.w.vault_page
        e = self.find("Amazon")
        vp.select(e)
        vp.toggle_fav()
        self.assertTrue(self.find("Amazon")["data"]["fav"])
        vp.toggle_fav()
        self.assertFalse(self.find("Amazon").get("data", {}).get("fav"))
        self.w.delete_item(self.find("Amazon"))
        self.assertFalse(any(x["title"] == "Amazon" for x in self.w.entries))

    def test_warnings_can_be_turned_off(self):
        self.assertTrue(self.w.health)
        self.assertTrue(self.w.nav["health"].badge.text())
        self.w.set_opt("warn_passwords", False)
        self.assertEqual(self.w.health, {})
        self.assertEqual(self.w.nav["health"].badge.text(), "")
        self.w.go("health")
        self.assertEqual(self.w.pages.currentIndex(), window.PAGE_INDEX["health"])
        self.w.set_opt("warn_passwords", True)
        self.assertTrue(self.w.health)
        self.w.set_opt("warn_security", False)
        self.w.go("usb")
        pump()

    def test_generator_modes(self):
        from pmapp.pages.generator import GeneratorPanel
        g = GeneratorPanel(self.w)
        g.len.setValue(32)
        self.assertEqual(len(g.pw), 32)
        for k in ("upper", "digits", "symbols"):
            g.sw[k].setChecked(False)
        self.assertTrue(g.pw.islower())
        g.set_mode("phrase")
        g.words.setValue(7)
        self.assertEqual(len(g.pw.split("-")), 7)
        g.set_mode("pin")
        g.digits.setValue(9)
        self.assertTrue(g.pw.isdigit() and len(g.pw) == 9)

    def test_settings_search_hides_what_does_not_match(self):
        sp = self.w.settings_page
        sp.search.setText("pairing")
        shown = [r.title.text() for c in sp.cards for r, _ in c.rows if r.isVisibleTo(sp)]
        self.assertEqual(shown, ["Pairing code"])
        sp.search.setText("zzzz")
        self.assertTrue(sp.nomatch.isVisibleTo(sp))
        sp.search.setText("")

    def test_audit_filters(self):
        ap = self.w.audit_page
        ap.events = [{"ts": time.time() - i, "text": t, "cat": c, "src": "app"} for i, (t, c) in enumerate((("a", "stick"), ("b", "sync"), ("c", "stick")))]
        ap.set_cat("stick")
        rows = [ap.list.item(i).data(0x100 + 0) for i in range(ap.list.count())]
        self.assertEqual(sum(1 for r in rows if r["text"] in ("a", "c")), 2)
        ap.set_cat(None)

    def test_locking_and_the_locked_screen(self):
        self.w.lock_now("because")
        self.assertIsNone(self.w.vault)
        self.assertEqual(self.w.pages.currentIndex(), 0)
        self.assertEqual(self.w.entries, [])
        self.w.go("gen")
        self.assertEqual(self.w.pages.currentIndex(), window.PAGE_INDEX["gen"], "the generator works while locked")
        self.w.go("audit")
        self.assertEqual(self.w.pages.currentIndex(), window.PAGE_INDEX["audit"])

    def test_qr_for_a_wifi_item(self):
        from pmapp.widgets import QRTile
        t = QRTile(200)
        t.set_text(model.wifi_uri("Home", "pw"))
        self.assertIsNotNone(t.grid)
        t.grab()


if __name__ == "__main__":
    unittest.main()


class LockSession(unittest.TestCase):
    """sysinfo.lock_session finds the desktop session from a background service (pretend loginctl: nothing is really locked)."""

    def test_finds_the_graphical_session(self):
        import stat
        import subprocess
        from pmapp import sysinfo
        d = Path(tempfile.mkdtemp())
        log = d / "log"
        sh = d / "loginctl"
        sh.write_text(f'''#!/bin/bash
echo "$@" >> {log}
case "$1" in
  list-sessions) printf '2 1000 {os.environ.get("USER", "me")} seat0 4429 user tty2 no -\\n3 1000 {os.environ.get("USER", "me")} - 4764 manager - no -\\n' ;;
  show-session) if [ "$2" = 2 ]; then printf 'Class=user\\nType=wayland\\nState=active\\n'; else printf 'Class=manager\\nType=unspecified\\nState=active\\n'; fi ;;
  lock-session) exit 0 ;;
esac''')
        sh.chmod(sh.stat().st_mode | stat.S_IEXEC)
        old = dict(os.environ)
        os.environ["PATH"] = f"{d}:{os.environ['PATH']}"
        os.environ.pop("XDG_SESSION_ID", None)
        try:
            self.assertTrue(sysinfo.lock_session())
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertIn("lock-session 2", log.read_text(), "locks the graphical session, not the manager one")
