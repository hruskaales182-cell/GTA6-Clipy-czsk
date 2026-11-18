// Klipárna: the phone app. Login with the password -> session token in localStorage; the feed shows the clip queue by
// tab (new / approved / uploaded) and language; approve / reject / download / copy title+description / mark uploaded;
// "Napiš mi" sends wishes for the next daily run.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private mode */ } },
  };
  let token = store.get("kt"), clips = [], tab = "new", lang = store.get("klang") || "all";

  const api = async (path, opts = {}) => {
    const r = await fetch(path, { ...opts, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...(opts.headers || {}) } });
    if (r.status === 401 && path !== "/api/login") { logout(); throw new Error("Přihlas se znovu"); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `Chyba ${r.status}`);
    return data;
  };
  const media = (key, dl) => key ? `/media/${encodeURIComponent(key)}?t=${encodeURIComponent(token)}&r=2${dl ? "&dl=1" : ""}` : "";
  let toastT;
  const toast = (msg) => { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 2200); };
  const buzz = () => { try { navigator.vibrate?.(12); } catch { /* iOS has none */ } };

  // ---------- auth ----------
  function logout() { token = null; store.set("kt", null); show(); }
  $("#loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#loginErr").textContent = "";
    try { const { token: t } = await api("/api/login", { method: "POST", body: JSON.stringify({ password: $("#pw").value }) }); token = t; store.set("kt", t); $("#pw").value = ""; show(); }
    catch (err) { $("#loginErr").textContent = err.message; }
  });

  // ---------- data ----------
  async function load(spin = false) {
    if (spin) $("#refresh").classList.add("spin");
    try { clips = await api("/api/clips"); render(); }
    catch (err) { toast(err.message); }
    finally { $("#refresh").classList.remove("spin"); }
  }
  async function patch(c, body) {
    Object.assign(c, body);
    try { await api(`/api/clips/${c.id}`, { method: "PATCH", body: JSON.stringify(body) }); }
    catch (err) { toast(err.message); }
  }

  // ---------- render ----------
  const fmtDur = (s) => s ? `${Math.round(s)} s` : "";
  const fmtViews = (v) => v ? `👁 ${v >= 1000 ? (v / 1000).toFixed(1).replace(".0", "") + "k" : v}` : "";
  const flag = (l) => (l === "cs" ? "🇨🇿 CZ" : l === "en" ? "🇺🇸 EN" : "");
  const inTab = (c) => tab === "new" ? c.status === "new" : tab === "approved" ? c.status === "approved" : c.status === "uploaded";

  function hero() {
    const n = clips.filter((c) => c.status === "new"), a = clips.filter((c) => c.status === "approved");
    const last = clips.reduce((m, c) => Math.max(m, c.created || 0), 0);
    const when = last ? new Date(last).toLocaleString("cs-CZ", { weekday: "long", hour: "2-digit", minute: "2-digit" }) : "zatím nic";
    const h = new Date().getHours(), hi = h < 11 ? "Dobré ráno" : h < 18 ? "Čau" : "Dobrý večer";
    $("#hero").innerHTML = `<h2>${hi} 👋</h2><p>${n.length ? `Máš <b>${n.length}</b> ${n.length === 1 ? "nový klip" : n.length < 5 ? "nové klipy" : "nových klipů"} ke schválení.` : "Všechno schválené. Další várka dorazí večer."}</p>
      <div class="stats"><span class="stat">Nové <b>${n.length}</b></span><span class="stat">K nahrání <b>${a.length}</b></span><span class="stat">Poslední běh: ${when}</span></div>`;
    const bn = $("#cNew"), ba = $("#cApp");
    bn.hidden = !n.length; bn.textContent = n.length; ba.hidden = !a.length; ba.textContent = a.length;
  }

  function render() {
    hero();
    const feed = $("#feed");
    const notes = tab === "notes";
    $("#notesView").hidden = !notes; feed.hidden = notes; $(".chips").hidden = notes; $("#hero").hidden = notes;
    if (notes) return renderNotes();
    const list = clips.filter(inTab).filter((c) => lang === "all" || c.lang === lang);
    feed.innerHTML = "";
    if (!list.length) {
      const msg = { new: ["🎬", "Nic nového", "Denní běh ti sem večer přidá čerstvé klipy."], approved: ["✅", "Nic k nahrání", "Schválené klipy najdeš tady, připravené ke stažení."], uploaded: ["🚀", "Zatím nic", "Sem se přesunou klipy, které označíš jako nahrané."] }[tab];
      feed.innerHTML = `<div class="empty"><div class="big">${msg[0]}</div><h3>${msg[1]}</h3><p>${msg[2]}</p></div>`;
      return;
    }
    for (const c of list) feed.appendChild(card(c));
  }

  function card(c) {
    const el = $("#cardTpl").content.firstElementChild.cloneNode(true);
    const v = $("video", el), box = $(".vid", el);
    v.src = media(c.video); if (c.thumb) v.poster = media(c.thumb);
    $(".play", el).onclick = () => { $$("video").forEach((o) => o !== v && o.pause()); v.play(); };
    v.onplay = () => box.classList.add("playing"); v.onpause = () => box.classList.remove("playing");
    v.onclick = () => (v.paused ? v.play() : v.pause());
    $(".streamer", el).textContent = c.streamer || ""; $(".lang", el).textContent = flag(c.lang);
    $(".dur", el).textContent = fmtDur(c.dur); $(".views", el).textContent = fmtViews(c.views);
    $(".hook", el).textContent = c.hook || "";
    const ti = $(".title", el), de = $(".desc", el);
    ti.value = c.title || ""; de.value = [c.desc, c.tags].filter(Boolean).join("\n");
    ti.onchange = () => patch(c, { title: ti.value }); de.onchange = () => patch(c, { desc: de.value });
    $$(".copy", el).forEach((b) => b.onclick = async () => {
      const txt = b.dataset.copy === "title" ? ti.value : de.value;
      try { await navigator.clipboard.writeText(txt); b.textContent = "✓"; b.classList.add("done"); buzz(); setTimeout(() => { b.textContent = "Kopírovat"; b.classList.remove("done"); }, 1400); }
      catch { toast("Kopírování nejde, podrž text a zkopíruj ručně"); }
    });
    const act = $(".actions", el);
    const btn = (cls, label, fn, wide) => { const b = document.createElement("button"); b.className = `btn big ${cls}${wide ? " wide" : ""}`; b.innerHTML = label; b.onclick = fn; act.appendChild(b); return b; };
    const leave = (dir, status, msg) => { buzz(); v.pause(); el.classList.add(dir); patch(c, { status }); setTimeout(() => { render(); toast(msg); }, 280); };
    if (c.status === "new") {
      btn("no", "✕ Zahodit", () => leave("out-no", "rejected", "Zahozeno"));
      btn("ok", "✓ Schválit", () => leave("out-ok", "approved", "Schváleno · najdeš ho v Schválených"));
    } else if (c.status === "approved") {
      const a = document.createElement("a"); a.className = "btn big primary wide"; a.href = media(c.video, true); a.innerHTML = "⬇ Stáhnout video"; a.setAttribute("download", ""); act.appendChild(a);
      btn("ghost", "↩ Zpět", () => leave("out-no", "new", "Vráceno mezi nové"));
      btn("ok", "🚀 Nahráno", () => leave("out-ok", "uploaded", "Super, označeno jako nahrané"));
    } else {
      btn("ghost wide", "↩ Vrátit do schválených", () => leave("out-no", "approved", "Vráceno"), true);
    }
    return el;
  }

  async function renderNotes() {
    try {
      const notes = await api("/api/notes");
      $("#notesList").innerHTML = notes.map((n) => `<li>${esc(n.text)}<time>${new Date(n.at).toLocaleString("cs-CZ")}${n.done ? ' · <span class="done">hotovo</span>' : ""}</time></li>`).join("");
    } catch (err) { toast(err.message); }
  }
  const esc = (s) => s.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
  $("#noteForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const t = $("#noteText"); if (!t.value.trim()) return;
    try { await api("/api/notes", { method: "POST", body: JSON.stringify({ text: t.value }) }); t.value = ""; buzz(); toast("Odesláno 👍"); renderNotes(); }
    catch (err) { toast(err.message); }
  });

  // ---------- nav ----------
  $$(".tab").forEach((b) => b.onclick = () => { $$(".tab").forEach((x) => x.classList.toggle("on", x === b)); tab = b.dataset.tab; window.scrollTo({ top: 0 }); render(); });
  $$(".chip").forEach((b) => { b.classList.toggle("on", b.dataset.lang === lang); b.onclick = () => { lang = b.dataset.lang; store.set("klang", lang); $$(".chip").forEach((x) => x.classList.toggle("on", x === b)); render(); }; });
  $("#refresh").onclick = () => load(true);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && token) load(); });

  function show() {
    $("#login").hidden = !!token; $("#app").hidden = !token;
    if (token) load();
  }
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  show();
})();
