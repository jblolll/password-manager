// Runs inside the web page (not in the extension's private world). When a site asks for a passkey it asks the Password Manager app first,
// through webauthn-bridge.js. If the app can't or shouldn't answer (not running, locked, you chose "use my browser instead", or it has no passkey for that site),
// the original browser behaviour runs as if this file wasn't here. It never sees your pairing code or any key: it only passes requests and answers along.
(() => {
  if (window.__pmWebAuthn || !navigator.credentials || !window.PublicKeyCredential) return;
  window.__pmWebAuthn = true;
  const creds = navigator.credentials;
  const origCreate = creds.create.bind(creds), origGet = creds.get.bind(creds);
  const origUvpaa = PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable ? PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable.bind(PublicKeyCredential) : null;
  const ERRORS = ["NotAllowedError", "InvalidStateError", "NotSupportedError", "SecurityError", "AbortError"];

  const bytes = (v) => v instanceof ArrayBuffer ? new Uint8Array(v) : new Uint8Array(v.buffer, v.byteOffset, v.byteLength);
  const b64u = (v) => { let s = ""; for (const x of bytes(v)) s += String.fromCharCode(x); return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); };
  const unb64u = (t) => { t = (t || "").replace(/-/g, "+").replace(/_/g, "/"); while (t.length % 4) t += "="; const s = atob(t), u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u.buffer; };

  let seq = 0;
  const pending = new Map();
  window.addEventListener("message", (ev) => {
    if (ev.source !== window || !ev.data || ev.data.pmReply !== true) return;
    const done = pending.get(ev.data.id);
    if (done) { pending.delete(ev.data.id); done(ev.data.reply || {}); }
  });
  function ask(kind, payload) {
    return new Promise((resolve) => {
      const id = ++seq + ":" + Math.random();
      pending.set(id, resolve);
      window.postMessage({ pmRequest: true, id, kind, payload }, "*");
      setTimeout(() => { if (pending.delete(id)) resolve({ error: "timeout" }); }, 128000);
    });
  }
  // the app isn't there (offline, locked, unpaired) or says "use the browser": do what the browser would have done
  const useBrowser = (r) => !r || !!r.fallback || (!!r.error && !ERRORS.includes(r.error));
  const refuse = (r) => { throw new DOMException(r.message || "Not allowed", ERRORS.includes(r.error) ? r.error : "NotAllowedError"); };
  const def = (o, k, v, enumerable = true) => Object.defineProperty(o, k, { value: v, enumerable, configurable: true, writable: true });

  function credential(kind, r) {
    const rawId = unb64u(r.credentialId), cdj = unb64u(r.clientDataJSON);
    const protoR = kind === "create" ? (window.AuthenticatorAttestationResponse || Object).prototype : (window.AuthenticatorAssertionResponse || Object).prototype;
    const response = Object.create(protoR);
    def(response, "clientDataJSON", cdj);
    const json = { clientDataJSON: r.clientDataJSON };
    if (kind === "create") {
      def(response, "attestationObject", unb64u(r.attestationObject));
      def(response, "getTransports", () => r.transports || ["internal"], false);
      def(response, "getAuthenticatorData", () => unb64u(r.authData), false);
      def(response, "getPublicKey", () => unb64u(r.publicKey), false);
      def(response, "getPublicKeyAlgorithm", () => r.publicKeyAlgorithm, false);
      Object.assign(json, { attestationObject: r.attestationObject, transports: r.transports || ["internal"], publicKeyAlgorithm: r.publicKeyAlgorithm, publicKey: r.publicKey, authenticatorData: r.authData });
    } else {
      def(response, "authenticatorData", unb64u(r.authenticatorData));
      def(response, "signature", unb64u(r.signature));
      def(response, "userHandle", r.userHandle ? unb64u(r.userHandle) : null);
      Object.assign(json, { authenticatorData: r.authenticatorData, signature: r.signature, userHandle: r.userHandle || undefined });
    }
    const cred = Object.create(PublicKeyCredential.prototype);
    def(cred, "id", r.credentialId);
    def(cred, "rawId", rawId);
    def(cred, "type", "public-key");
    def(cred, "authenticatorAttachment", "platform");
    def(cred, "response", response);
    def(cred, "getClientExtensionResults", () => ({}), false);
    def(cred, "toJSON", () => ({ id: r.credentialId, rawId: r.credentialId, type: "public-key", authenticatorAttachment: "platform", clientExtensionResults: {}, response: json }), false);
    return cred;
  }

  const common = (pk) => ({
    rpId: pk.rpId || (pk.rp && pk.rp.id) || "", challenge: b64u(pk.challenge),
    userVerification: pk.userVerification || (pk.authenticatorSelection && pk.authenticatorSelection.userVerification) || "preferred",
  });

  creds.create = async function (options) {
    const pk = options && options.publicKey;
    if (!pk || !pk.user || !pk.challenge) return origCreate(options);
    if (options.signal && options.signal.aborted) throw new DOMException("Aborted", "AbortError");
    const r = await ask("create", Object.assign(common(pk), {
      rpName: pk.rp && pk.rp.name, user: { id: b64u(pk.user.id), name: pk.user.name, displayName: pk.user.displayName },
      pubKeyCredParams: (pk.pubKeyCredParams || []).map((p) => ({ type: p.type, alg: p.alg })),
      excludeCredentials: (pk.excludeCredentials || []).map((c) => b64u(c.id)),
    }));
    if (useBrowser(r)) return origCreate(options);
    if (r.error) refuse(r);
    return credential("create", r);
  };

  creds.get = async function (options) {
    const pk = options && options.publicKey;
    if (!pk || !pk.challenge || options.mediation === "conditional") return origGet(options);      // the browser's own sign-in suggestions stay the browser's
    if (options.signal && options.signal.aborted) throw new DOMException("Aborted", "AbortError");
    const payload = Object.assign(common(pk), { allowCredentials: (pk.allowCredentials || []).map((c) => b64u(c.id)) });
    const q = await ask("query", payload);
    if (!q || !q.available) return origGet(options);                                               // nothing saved for this site: the browser handles it
    const r = await ask("get", payload);
    if (useBrowser(r)) return origGet(options);
    if (r.error) refuse(r);
    return credential("get", r);
  };

  // sites ask "can this device do passkeys?" before they show the option: say yes while the app is there to answer
  PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable = async function () {
    const r = await ask("status", {});
    return r && r.ok ? true : (origUvpaa ? origUvpaa() : false);
  };
})();
