// Finds a login on the page and fills it from the Password Manager. Runs only in the top page (not in embedded frames).
// Handles one-page logins (email and password together) and two-step logins (email first, password on the next screen, like Google and Microsoft).
(() => {
  if (window.top !== window) return;
  const api = typeof browser !== "undefined" ? browser : chrome;
  const SHARED = ["github.io", "blogspot.com", "herokuapp.com", "netlify.app", "vercel.app", "pages.dev", "web.app", "firebaseapp.com", "azurewebsites.net", "appspot.com"];
  const TWO = ["co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.jp", "co.nz", "co.in", "com.br", "com.mx", "co.za", "com.cn", "com.sg", "edu.au"];
  function regDomain(h) {
    h = (h || "").toLowerCase();
    for (const s of SHARED) if (h === s || h.endsWith("." + s)) return h.split(".").slice(-(s.split(".").length + 1)).join(".");
    const p = h.split(".");
    if (p.length <= 2) return h;
    return TWO.includes(p.slice(-2).join(".")) ? p.slice(-3).join(".") : p.slice(-2).join(".");
  }
  const visible = (el) => {
    if (!el || el.disabled || el.type === "hidden") return false;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 8 && r.height > 8 && s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
  };
  const textTypes = ["text", "email", "tel", ""];
  function userFieldFor(pw) {
    const scope = pw.form || pw.closest("form, main, section, div") || document;
    let best = null;
    for (const c of scope.querySelectorAll("input")) {
      if (c === pw || !visible(c)) continue;
      const t = (c.getAttribute("type") || "text").toLowerCase();
      if (!textTypes.includes(t)) continue;
      if (c.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING) best = c;     // the closest one before the password box
    }
    return best;
  }
  // an email / username box on a page that has no visible password box yet (the first step of a two-step login)
  function looksLikeUser(el) {
    if (!visible(el)) return false;
    const t = (el.getAttribute("type") || "text").toLowerCase();
    if (!textTypes.includes(t)) return false;
    const ac = (el.getAttribute("autocomplete") || "").toLowerCase();
    const tag = `${el.name || ""} ${el.id || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase();
    if (!(/username|email/.test(ac) || t === "email" || /(user|email|login|identifier|account)/.test(tag))) return false;
    const f = el.form;
    if (f && [...f.querySelectorAll("input")].filter((i) => visible(i) && textTypes.concat(["password"]).includes((i.getAttribute("type") || "text").toLowerCase())).length > 3) return false;   // a sign-up form
    return true;
  }
  function setValue(el, v) {
    const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value");
    el.focus();
    d.set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  }
  // never fill a form that sends its data to a different site than the page you are on
  function formIsSafe(el) {
    const f = el.form;
    if (!f || !f.getAttribute("action")) return true;
    try { return regDomain(new URL(f.action, location.href).hostname) === regDomain(location.hostname); } catch (e) { return false; }
  }
  const KEY = "__pm_user";
  const remember = (u) => { try { sessionStorage.setItem(KEY, u || ""); } catch (e) { /* private page */ } };
  const remembered = () => { try { return sessionStorage.getItem(KEY) || ""; } catch (e) { return ""; } };
  const done = new WeakSet();
  let overlay = null;
  function fillBoth(pw, login) {
    const u = userFieldFor(pw);
    if (u && login.username) setValue(u, login.username);
    setValue(pw, login.password);
    remember(login.username);
    done.add(pw);
  }
  function fillUserOnly(el, login) { setValue(el, login.username); remember(login.username); done.add(el); }
  function closePicker() { if (overlay) { overlay.remove(); overlay = null; } }
  function picker(field, matches, onPick) {
    closePicker();
    const host = document.createElement("div");
    host.style.cssText = "all:initial;position:absolute;z-index:2147483647;";
    const r = field.getBoundingClientRect();
    host.style.left = window.scrollX + r.left + "px";
    host.style.top = window.scrollY + r.bottom + 4 + "px";
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `<style>
      .b{font:13px system-ui,sans-serif;background:#12151c;color:#e8ebf3;border:1px solid #2c3348;border-radius:10px;box-shadow:0 8px 24px #0008;min-width:240px;overflow:hidden}
      .h{padding:7px 12px;color:#8b93a7;font-size:11px;letter-spacing:.06em;text-transform:uppercase;border-bottom:1px solid #232838}
      button{all:unset;display:block;width:100%;box-sizing:border-box;padding:9px 12px;cursor:pointer}
      button:hover{background:#1b2540} small{display:block;color:#8b93a7}</style>
      <div class="b"><div class="h">Password Manager</div></div>`;
    const box = root.querySelector(".b");
    for (const m of matches) {
      const b = document.createElement("button");
      b.innerHTML = "<b></b><small></small>";
      b.firstChild.textContent = m.title;
      b.lastChild.textContent = m.username || "(no username)";
      b.addEventListener("click", () => { onPick(m); closePicker(); });
      box.appendChild(b);
    }
    document.documentElement.appendChild(host);
    overlay = host;
    setTimeout(() => document.addEventListener("click", closePicker, { once: true }), 0);
  }
  let busy = false, tries = 0;
  async function ask() {
    const r = await api.runtime.sendMessage({ type: "fill", host: location.hostname, scheme: location.protocol });
    return r && !r.error && r.matches && r.matches.length ? r : null;
  }
  async function scan(force) {
    if (busy) return;
    const pws = [...document.querySelectorAll("input[type=password]")].filter((p) => visible(p) && (force || !done.has(p)));
    const users = pws.length ? [] : [...document.querySelectorAll("input")].filter((i) => looksLikeUser(i) && (force || !done.has(i)));
    if (!pws.length && !users.length) return;
    busy = true;
    try {
      const r = await ask();
      if (!r) return;
      if (pws.length) {
        for (const pw of pws) {
          if (!formIsSafe(pw)) continue;
          // on the password step, use the account picked on the email step
          const prev = remembered();
          const same = prev ? r.matches.filter((m) => (m.username || "").toLowerCase() === prev.toLowerCase()) : [];
          if (same.length === 1 && (r.auto || force)) fillBoth(pw, same[0]);
          else if (r.matches.length === 1 && (r.auto || force)) fillBoth(pw, r.matches[0]);
          else if (r.matches.length > 1) { picker(pw, r.matches, (m) => fillBoth(pw, m)); break; }
          else done.add(pw);
        }
      } else {
        const el = users[0];
        if (!formIsSafe(el)) return;
        const withUser = r.matches.filter((m) => m.username);
        if (withUser.length === 1 && (r.auto || force)) fillUserOnly(el, withUser[0]);
        else if (withUser.length > 1) picker(el, withUser, (m) => fillUserOnly(el, m));
      }
    } finally { busy = false; }
  }

  // ---- offer to save: when a form with a password is submitted (a sign-up, a changed password, or a login that isn't saved yet), tell the app.
  // The app decides whether it is new; it asks you before anything is saved. Only on https pages (or localhost).
  const LASTUSER = "__pm_lastuser";
  document.addEventListener("change", (e) => {
    const el = e.target;
    if (el && el.tagName === "INPUT" && looksLikeUser(el) && el.value) { try { sessionStorage.setItem(LASTUSER, el.value); } catch (x) { /* private page */ } }
  }, true);
  let lastSent = "";
  function capture(scope) {
    if (location.protocol !== "https:" && location.hostname !== "localhost") return;
    const pws = [...(scope || document).querySelectorAll("input[type=password]")].filter((p) => p.value && visible(p));
    if (!pws.length) return;
    const all = [...(scope || document).querySelectorAll("input[type=password]")].filter((p) => visible(p));
    const newish = pws.some((p) => /new-password/i.test(p.getAttribute("autocomplete") || "")) || all.length >= 2;
    // with two boxes the new password is the one that is repeated; with "current + new" it is the last non-empty one
    const pw = pws[pws.length - 1].value;
    const u = userFieldFor(pws[0]);
    let username = (u && u.value) || remembered();
    if (!username) { try { username = sessionStorage.getItem(LASTUSER) || ""; } catch (x) { username = ""; } }
    const key = location.hostname + "|" + username + "|" + pw;
    if (key === lastSent) return;
    lastSent = key;
    api.runtime.sendMessage({ type: "capture", host: location.hostname, scheme: location.protocol, username, password: pw, kind: newish ? "new" : "login" });
  }
  document.addEventListener("submit", (e) => { try { capture(e.target && e.target.querySelectorAll ? e.target : document); } catch (x) { /* ignore */ } }, true);
  // sites that sign in with a button and no real form
  document.addEventListener("click", (e) => {
    const b = e.target && e.target.closest && e.target.closest("button, input[type=submit], [role=button]");
    if (b && !b.form) try { capture(document); } catch (x) { /* ignore */ }
  }, true);
  api.runtime.onMessage.addListener((msg) => { if (msg && msg.type === "fillnow") scan(true); });
  scan(false);
  // pages that show the next step later (single-page apps, "next" screens)
  let t = null;
  new MutationObserver(() => { clearTimeout(t); t = setTimeout(() => { if (tries++ < 40) scan(false); }, 400); }).observe(document.documentElement, { childList: true, subtree: true });
})();
