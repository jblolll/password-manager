"""Passkeys end to end: a real web page (in Chromium, inside Qt) asks for a passkey, the browser-extension scripts pass it to the app's real bridge, the app
(the real window code, in demo mode) answers, and a pretend website checks the answer the way a real one would. Only the extension's chrome.* calls are stood in
for (a tiny shim that does what background.js does). Skipped without QtWebEngine.

    QT_QPA_PLATFORM=offscreen python3 -m unittest tests.test_passkey_web -v
"""
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
os.environ["JBLOL_PM_CONF"] = tempfile.mkdtemp(prefix="pm-pkweb-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

try:
    from PySide6.QtCore import QEventLoop, QObject, QTimer, QUrl, Signal
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication
    HAVE = True
except ImportError:
    HAVE = False

if HAVE:
    import jblol_pm as core
    _conf = Path(os.environ["JBLOL_PM_CONF"])
    core.CONF_DIR, core.SETTINGS, core.EVENT_LOG = _conf, _conf / "settings.json", _conf / "events.log"
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from pmapp import passkeys as pk, theme, window
    from pmapp.pages import items
    from test_passkeys import cbor_decode, parse_attested, public_key_from_cose

EXT = ROOT / "browser-extension"
TOKEN = "test-token"

PAGE = """<!doctype html><meta charset=utf-8><body><script>
const b64u = (v) => { let s = ""; for (const x of new Uint8Array(v)) s += String.fromCharCode(x); return btoa(s).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/, ""); };
const scenario = new URLSearchParams(location.search).get("s");
async function run() {
  const out = { scenario };
  const rpId = scenario === "badrp" ? "example.org" : "localhost";
  try {
    const ch = crypto.getRandomValues(new Uint8Array(32));
    const c = await navigator.credentials.create({ publicKey: { challenge: ch, rp: { id: rpId, name: "Test Site" },
      user: { id: new TextEncoder().encode("user-1"), name: "ann@example.com", displayName: "Ann" },
      pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }], authenticatorSelection: { userVerification: "required" } } });
    if (c && c.spy) out.create = { spy: true };
    else out.create = { ok: true, isPK: c instanceof PublicKeyCredential, attResp: c.response instanceof AuthenticatorAttestationResponse, id: c.id, rawId: b64u(c.rawId), type: c.type,
      cdj: b64u(c.response.clientDataJSON), att: b64u(c.response.attestationObject), challenge: b64u(ch), json: c.toJSON(), transports: c.response.getTransports(),
      alg: c.response.getPublicKeyAlgorithm(), spki: b64u(c.response.getPublicKey()), authData: b64u(c.response.getAuthenticatorData()), ext: c.getClientExtensionResults() };
  } catch (e) { out.create = { ok: false, name: e.name, message: e.message }; }
  if (scenario === "basic" || scenario === "browser2") {
    try {
      const ch = crypto.getRandomValues(new Uint8Array(32));
      const g = await navigator.credentials.get({ publicKey: { challenge: ch, rpId: "localhost", userVerification: "required", allowCredentials: [] } });
      if (g && g.spy) out.get = { spy: true };
      else out.get = { ok: true, isPK: g instanceof PublicKeyCredential, id: g.id, cdj: b64u(g.response.clientDataJSON), authData: b64u(g.response.authenticatorData),
        sig: b64u(g.response.signature), user: g.response.userHandle ? b64u(g.response.userHandle) : null, challenge: b64u(ch) };
    } catch (e) { out.get = { ok: false, name: e.name, message: e.message }; }
  }
  await fetch("/result", { method: "POST", body: JSON.stringify(out) });
}
addEventListener("load", () => setTimeout(run, 200));
</script>"""

SPY = """(() => { window.__spy = []; const c = navigator.credentials;
  c.create = async function () { window.__spy.push('create'); return { spy: true }; };
  c.get = async function () { window.__spy.push('get'); return { spy: true }; }; })();"""

SHIM = """(() => { const wait = {};
  window.chrome = { runtime: { sendMessage: (msg, cb) => { const id = String(Math.random()); wait[id] = cb; console.log("PMOUT " + JSON.stringify({ id, msg })); } } };
  window.addEventListener("message", (e) => { if (e.source === window && e.data && e.data.pmBgReply) { const cb = wait[e.data.id]; delete wait[e.data.id]; if (cb) cb(e.data.reply); } }); })();"""


class Rp(http.server.BaseHTTPRequestHandler):
    results = []

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE.encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Rp.results.append(json.loads(self.rfile.read(n)))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


@unittest.skipUnless(HAVE, "QtWebEngine isn't installed")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply(cls.app)
        cls.win = window.Main(demo=True)
        cls.win.vault.items = []                       # a vault with no passkeys yet
        cls.win.set_entries([])
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Rp)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.bridge = core.Bridge(0, TOKEN, cls.win.bridge_state, lambda h, n: None, lambda d: None, cls.win.passkey_from_bridge)
        cls.bridge.httpd.server_close()
        cls.bridge.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), cls.bridge.httpd.RequestHandlerClass)
        cls.bridge.port = cls.bridge.httpd.server_address[1]
        cls.bridge.thread = threading.Thread(target=cls.bridge.httpd.serve_forever, daemon=True)
        cls.bridge.start()
        cls.prompts = []
        cls.built = []
        cls.answer = ("allow", 0)
        items.PasskeyPrompt.run = lambda self_: (cls.prompts.append(self_.windowTitle()), cls.answer)[1]
        # PasskeyPrompt is built then run(): record what it was built with
        orig_init = items.PasskeyPrompt.__init__

        def init(self_, win, mode, site, rp_id, accounts, replaces=False):
            cls.last_prompt = (mode, site, rp_id, list(accounts), replaces)
            cls.built.append(cls.last_prompt)
            orig_init(self_, win, mode, site, rp_id, accounts, replaces)
        items.PasskeyPrompt.__init__ = init

    @classmethod
    def tearDownClass(cls):
        cls.bridge.stop()
        cls.srv.shutdown()

    def setUp(self):
        self.win.vault.items = []
        self.win.set_entries([])
        self.win.vault.items = []
        self.win.s["passkeys_enabled"] = True
        self.prompts.clear()
        self.built.clear()
        Rp.results.clear()
        type(self).answer = ("allow", 0)

    # -- the browser side: scripts in the page (main world) and the extension's own world, with chrome.runtime stood in for
    def visit(self, scenario, seconds=40):
        test = self
        loop = QEventLoop()

        class Relay(QObject):
            answered = Signal(str, str)

        relay = Relay()

        class Page(QWebEnginePage):
            def javaScriptConsoleMessage(self, level, msg, line, src):
                if msg.startswith("PMOUT "):
                    item = json.loads(msg[6:])
                    # what background.js does: forward the extension's message to the app over http (in a thread: the app may be waiting for you to click)
                    threading.Thread(target=lambda: relay.answered.emit(item["id"], json.dumps(test.background(item["msg"]))), daemon=True).start()

        page = Page()
        view = QWebEngineView()
        view.setPage(page)
        relay.answered.connect(lambda id_, reply: page.runJavaScript(f"window.postMessage({{pmBgReply: true, id: {json.dumps(id_)}, reply: {reply}}}, '*')", QWebEngineScript.MainWorld))

        def script(name, src, world):
            sc = QWebEngineScript()
            sc.setName(name)
            sc.setSourceCode(src)
            sc.setInjectionPoint(QWebEngineScript.DocumentCreation)
            sc.setWorldId(world)
            sc.setRunsOnSubFrames(False)
            page.scripts().insert(sc)
        main, iso = QWebEngineScript.MainWorld, QWebEngineScript.ApplicationWorld
        script("spy", SPY, main)
        script("main", (EXT / "webauthn-main.js").read_text(), main)
        script("bridge", SHIM + "\n" + (EXT / "webauthn-bridge.js").read_text(), iso)       # (one script, so the stand-in is surely there first)
        poll = QTimer()
        poll.timeout.connect(lambda: loop.quit() if Rp.results else None)
        poll.start(50)
        QTimer.singleShot(seconds * 1000, loop.quit)
        page.load(QUrl(f"http://localhost:{self.srv.server_address[1]}/?s={scenario}"))
        loop.exec()
        poll.stop()
        page.deleteLater()
        self.assertTrue(Rp.results, "the page never reported back")
        return Rp.results[-1]

    def background(self, msg):
        base = f"http://127.0.0.1:{self.bridge.port}"
        try:
            if msg["type"] == "status":
                req = urllib.request.Request(base + "/status", headers={"X-PM-Token": TOKEN})
            else:
                req = urllib.request.Request(base + "/passkey/" + msg["kind"], data=json.dumps(msg["body"]).encode(), method="POST", headers={"X-PM-Token": TOKEN, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=100) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return {"error": f"http {e.code}"}
        except OSError:
            return {"error": "offline"}

    # -- the website side: check what came back like a relying party would
    def verify_registration(self, c, origin):
        self.assertTrue(c.get("ok"), c)
        self.assertTrue(c["isPK"] and c["attResp"], "the page gets real PublicKeyCredential / AuthenticatorAttestationResponse objects")
        self.assertEqual(c["type"], "public-key")
        self.assertEqual(c["ext"], {})
        cdj = pk.unb64u(c["cdj"])
        client = json.loads(cdj)
        self.assertEqual((client["type"], client["challenge"], client["origin"], client["crossOrigin"]), ("webauthn.create", c["challenge"], origin, False))
        att, _ = cbor_decode(pk.unb64u(c["att"]))
        self.assertEqual(att["fmt"], "none")
        rp_hash, flags, counter, _aaguid, cred_id, cose = parse_attested(att["authData"])
        self.assertEqual(rp_hash, hashlib.sha256(b"localhost").digest())
        self.assertTrue(flags & pk.UP and flags & pk.UV and flags & pk.AT)
        self.assertEqual(cred_id, pk.unb64u(c["rawId"]))
        self.assertEqual(c["id"], pk.b64u(cred_id))
        self.assertEqual(c["alg"], -7)
        self.assertEqual(pk.unb64u(c["authData"]), att["authData"])
        self.assertEqual(c["json"]["id"], c["id"])
        self.assertEqual(c["json"]["response"]["clientDataJSON"], c["cdj"])
        return public_key_from_cose(cose), cred_id

    def verify_assertion(self, g, pub, cred_id, origin):
        self.assertTrue(g.get("ok"), g)
        self.assertTrue(g["isPK"])
        self.assertEqual(pk.unb64u(g["id"]), cred_id)
        cdj = pk.unb64u(g["cdj"])
        client = json.loads(cdj)
        self.assertEqual((client["type"], client["challenge"], client["origin"]), ("webauthn.get", g["challenge"], origin))
        auth = pk.unb64u(g["authData"])
        self.assertEqual(auth[:32], hashlib.sha256(b"localhost").digest())
        self.assertTrue(auth[32] & pk.UP and auth[32] & pk.UV)
        pub.verify(pk.unb64u(g["sig"]), auth + hashlib.sha256(cdj).digest(), ec.ECDSA(hashes.SHA256()))      # raises if the signature is wrong
        self.assertEqual(pk.unb64u(g["user"]), b"user-1")

    # -- the scenarios
    def test_sign_up_then_sign_in_with_a_passkey(self):
        origin = f"http://localhost:{self.srv.server_address[1]}"
        res = self.visit("basic")
        pub, cred_id = self.verify_registration(res["create"], origin)
        self.assertNotIn("spy", res["create"], "the browser's own passkey code was never needed")
        self.assertNotIn("spy", res["get"])
        stored = [e for e in self.win.entries if e["kind"] == "passkey"]
        self.assertEqual(len(stored), 1)
        self.assertEqual((stored[0]["group"], stored[0]["username"], stored[0]["data"]["rp_id"], stored[0]["data"]["user_name"]), ("Passkeys", "ann@example.com", "localhost", "ann@example.com"))
        self.assertEqual(stored[0]["data"]["cred_id"], pk.b64u(cred_id))
        self.verify_assertion(res["get"], pub, cred_id, origin)
        self.assertEqual(self.prompts.count("Passkey"), 2, "one question when saving, one when signing in")
        log = (core.CONF_DIR / "events.log").read_text()
        self.assertIn("Passkey saved for localhost", log)
        self.assertIn("Passkey used to sign in to localhost", log)

    def test_saying_no_gives_the_page_a_normal_refusal(self):
        type(self).answer = ("cancel", None)
        res = self.visit("cancel")
        self.assertEqual((res["create"]["ok"], res["create"]["name"]), (False, "NotAllowedError"))
        self.assertEqual([e for e in self.win.entries if e["kind"] == "passkey"], [])

    def test_use_my_browser_instead_runs_the_browsers_own_code(self):
        type(self).answer = ("browser", None)
        res = self.visit("browser")
        self.assertEqual(res["create"], {"spy": True}, "the original browser call was made")
        self.assertEqual([e for e in self.win.entries if e["kind"] == "passkey"], [])

    def test_a_site_cannot_ask_for_a_passkey_for_another_site(self):
        res = self.visit("badrp")
        self.assertEqual((res["create"]["ok"], res["create"]["name"]), (False, "SecurityError"))
        self.assertEqual(self.prompts, [], "the app didn't even ask you")

    def test_with_the_vault_locked_the_browser_carries_on_as_usual(self):
        saved = self.win.vault
        self.win.vault = None
        try:
            res = self.visit("locked")
        finally:
            self.win.vault = saved
        self.assertEqual(res["create"], {"spy": True})
        self.assertEqual(self.prompts, [])

    def test_passkeys_switched_off_hands_everything_to_the_browser(self):
        self.win.s["passkeys_enabled"] = False
        res = self.visit("off")
        self.assertEqual(res["create"], {"spy": True})
        self.assertEqual(self.prompts, [])

    def test_a_second_sign_up_for_the_same_account_replaces_the_first(self):
        first = self.visit("basic")
        Rp.results.clear()
        second = self.visit("basic")
        self.assertNotEqual(first["create"]["id"], second["create"]["id"])
        mine = [e for e in self.win.entries if e["kind"] == "passkey"]
        self.assertEqual(len(mine), 1, "still one passkey for this account")
        self.assertEqual(mine[0]["data"]["cred_id"], second["create"]["id"])
        creates = [b for b in self.built if b[0] == "create"]
        self.assertEqual([b[4] for b in creates], [False, True], "the second sign-up told you it replaces the first")
        self.assertEqual(creates[0][1:4], ("Test Site", "localhost", ["ann@example.com"]), "and the question named the site and the account")

    def test_with_no_saved_passkey_signing_in_is_left_to_the_browser(self):
        res = self.visit("browser2")                   # create is refused-to-browser first? no: answer allow, so a passkey is saved, then get works
        self.assertTrue(res["create"].get("ok"))
        self.win.vault.items = []
        self.win.set_entries([])
        Rp.results.clear()
        type(self).answer = ("browser", None)
        res = self.visit("browser")
        self.assertEqual(res["create"], {"spy": True})


if __name__ == "__main__":
    unittest.main()
