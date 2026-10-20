// The extension's side of passkeys. webauthn-main.js (in the page) asks; this builds the part of the request that must be trusted (the page's real address and
// the clientDataJSON that contains it, which a page must not be able to fake) and passes it to the app through the background script.
(() => {
  if (window.top !== window) return;
  const b64u = (v) => { let s = ""; for (const x of new Uint8Array(v)) s += String.fromCharCode(x); return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); };
  const secure = location.protocol === "https:" || location.hostname === "localhost";
  const send = (msg) => new Promise((resolve) => {
    try { (typeof browser !== "undefined" ? browser : chrome).runtime.sendMessage(msg, (r) => resolve(r || { error: "offline" })); } catch (e) { resolve({ error: "offline" }); }
  });

  async function handle(kind, p) {
    if (!secure) return { fallback: true };
    if (kind === "status") { const r = await send({ type: "status" }); return { ok: !!(r && r.ok && !r.locked) }; }
    const body = Object.assign({}, p, { origin: location.origin, rpId: p.rpId || location.hostname });
    if (kind === "query") return await send({ type: "passkey", kind: "query", body });
    const clientDataJSON = JSON.stringify({ type: kind === "create" ? "webauthn.create" : "webauthn.get", challenge: p.challenge, origin: location.origin, crossOrigin: false });
    const raw = new TextEncoder().encode(clientDataJSON);
    body.clientDataHash = b64u(await crypto.subtle.digest("SHA-256", raw));
    const r = await send({ type: "passkey", kind, body });
    return r && r.ok ? Object.assign({}, r, { clientDataJSON: b64u(raw) }) : r;
  }

  window.addEventListener("message", async (ev) => {
    if (ev.source !== window || !ev.data || ev.data.pmRequest !== true) return;
    let reply;
    try { reply = await handle(ev.data.kind, ev.data.payload || {}); } catch (e) { reply = { error: "offline" }; }
    window.postMessage({ pmReply: true, id: ev.data.id, reply }, "*");
  });
})();
