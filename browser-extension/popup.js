const api = typeof browser !== "undefined" ? browser : chrome;
const $ = (id) => document.getElementById(id);
const TEXT = { unpaired: "Not paired yet. Open Pairing below and paste the code from the app.", badcode: "The pairing code doesn't match. Copy it again from the app.",
  offline: "The Password Manager app isn't running. Plug in your USB stick (it opens by itself).", locked: "The vault is locked. Plug in your USB stick." };
async function status() {
  const r = await api.runtime.sendMessage({ type: "status" });
  const st = $("st");
  if (r.error) { st.textContent = TEXT[r.error] || ("Problem: " + r.error); st.style.color = "#ffb347"; if (r.error === "unpaired" || r.error === "badcode") $("setup").open = true; }
  else { st.textContent = r.locked ? TEXT.locked : `Ready. ${r.count} logins available.`; st.style.color = r.locked ? "#ffb347" : "#3fcf8e"; }
}
(async () => {
  const c = await api.storage.local.get({ token: "", port: 47619, auto: true });
  $("token").value = c.token; $("port").value = c.port; $("auto").checked = c.auto;
  status();
})();
$("save").onclick = async () => {
  await api.runtime.sendMessage({ type: "config", values: { token: $("token").value.trim(), port: parseInt($("port").value, 10) || 47619, auto: $("auto").checked } });
  status();
};
$("fill").onclick = async () => {
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  if (tab) api.tabs.sendMessage(tab.id, { type: "fillnow" }).catch(() => {});
  window.close();
};
