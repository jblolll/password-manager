// Talks to the Password Manager app on this computer (127.0.0.1) and nothing else. Web pages never see the pairing code.
const api = typeof browser !== "undefined" ? browser : chrome;
const DEFAULTS = { token: "", port: 47619, auto: true };

async function cfg() { return Object.assign({}, DEFAULTS, await api.storage.local.get(DEFAULTS)); }

// The Password Manager app writes pairing.json into this folder (on this computer only), so nobody has to paste a code.
// Used when there is no code yet, or when the app says the code is wrong (it was renewed).
async function pairFromFile() {
  try {
    const r = await fetch(api.runtime.getURL("pairing.json"), { cache: "no-store" });
    if (!r.ok) return false;
    const j = await r.json();
    if (!j.token) return false;
    const cur = await api.storage.local.get({ token: "", port: DEFAULTS.port });
    if (cur.token === j.token && cur.port === (j.port || DEFAULTS.port)) return false;
    await api.storage.local.set({ token: j.token, port: j.port || DEFAULTS.port });
    return true;
  } catch (e) { return false; }
}

async function post(path, body, retried = false) {
  let { token, port } = await cfg();
  if (!token && await pairFromFile()) ({ token, port } = await cfg());
  if (!token) return { error: "unpaired" };
  try {
    const r = await fetch(`http://127.0.0.1:${port}${path}`, { method: "POST", headers: { "X-PM-Token": token, "Content-Type": "application/json" }, body: JSON.stringify(body), cache: "no-store" });
    if (r.status === 401) return (!retried && await pairFromFile()) ? post(path, body, true) : { error: "badcode" };
    return r.ok ? await r.json() : { error: "http " + r.status };
  } catch (e) { return { error: "offline" }; }
}

async function call(path, retried = false) {
  let { token, port } = await cfg();
  if (!token && await pairFromFile()) ({ token, port } = await cfg());
  if (!token) return { error: "unpaired" };
  try {
    const r = await fetch(`http://127.0.0.1:${port}${path}`, { headers: { "X-PM-Token": token }, cache: "no-store" });
    if (r.status === 423) return { error: "locked" };
    if (r.status === 401) return (!retried && await pairFromFile()) ? call(path, true) : { error: "badcode" };
    if (!r.ok) return { error: "http " + r.status };
    return await r.json();
  } catch (e) { return { error: "offline" }; }
}

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    if (msg.type === "fill") {
      const { auto } = await cfg();
      const r = await call(`/fill?host=${encodeURIComponent(msg.host)}&scheme=${encodeURIComponent(msg.scheme)}`);
      return Object.assign({ auto }, r);
    }
    if (msg.type === "capture") return await post("/capture", { host: msg.host, scheme: msg.scheme, username: msg.username, password: msg.password, kind: msg.kind });
    if (msg.type === "status") return await call("/status");
    if (msg.type === "passkey") return await post("/passkey/" + msg.kind, msg.body);
    if (msg.type === "config") { await api.storage.local.set(msg.values); return { ok: true }; }
    return { error: "unknown" };
  })().then(sendResponse);
  return true;
});

// the little badge on the toolbar icon: how many logins this page can use
async function badge(tabId, url) {
  try {
    const u = new URL(url);
    if (u.protocol !== "https:" && u.hostname !== "localhost") { api.action.setBadgeText({ tabId, text: "" }); return; }
    const r = await call(`/fill?host=${encodeURIComponent(u.hostname)}&scheme=${encodeURIComponent(u.protocol)}`);
    api.action.setBadgeText({ tabId, text: r.error ? (r.error === "locked" ? "🔒" : "") : (r.matches && r.matches.length ? String(r.matches.length) : "") });
    api.action.setBadgeBackgroundColor({ tabId, color: "#4d7cfe" });
  } catch (e) { /* not a normal page */ }
}
api.tabs.onUpdated.addListener((id, info, tab) => { if (info.status === "complete" && tab.url) badge(id, tab.url); });
