// ClipApp API (Cloudflare Worker). The phone app (../web, served as static assets) talks to /api/*; the daily pipeline
// (GitHub Actions) uploads finished clips with the PIPELINE_TOKEN. Storage: KV "queue" (one JSON list of clip records)
// + "notes" (the user's wishes for the next runs); R2 holds the mp4s and thumbnails.
//   clip = { id, lang: "cs"|"en", streamer, title, hook, desc, tags, dur, views, source, created, status, video, thumb, note }
//   status: "new" -> "approved" | "rejected" -> "uploaded"
const J = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
const enc = new TextEncoder();
const b64u = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function hmac(secret, msg) {
  const k = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return b64u(await crypto.subtle.sign("HMAC", k, enc.encode(msg)));
}
/** session token = "<expiry>.<hmac(expiry)>", 60 days */
async function makeToken(env) {
  const exp = String(Date.now() + 60 * 864e5);
  return `${exp}.${await hmac(env.APP_PASSWORD, "s:" + exp)}`;
}
async function okToken(env, tok) {
  if (!tok || !env.APP_PASSWORD) return false;
  const [exp, sig] = tok.split(".");
  if (!exp || !sig || Number(exp) < Date.now()) return false;
  return sig === (await hmac(env.APP_PASSWORD, "s:" + exp));
}
const bearer = (req) => (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
const isPipeline = (req, env) => !!env.PIPELINE_TOKEN && bearer(req) === env.PIPELINE_TOKEN;
/** constant-ish time compare for the password */
const same = (a, b) => { if (a.length !== b.length) return false; let d = 0; for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i); return d === 0; };

const getQueue = async (env) => (await env.KV.get("queue", "json")) || [];
const putQueue = (env, q) => env.KV.put("queue", JSON.stringify(q.slice(0, 400)));

export default {
  async fetch(req, env) {
    const url = new URL(req.url), p = url.pathname;

    // ---- login: password -> session token ----
    if (p === "/api/login" && req.method === "POST") {
      const { password } = await req.json().catch(() => ({}));
      if (!env.APP_PASSWORD || typeof password !== "string" || !same(password, env.APP_PASSWORD)) {
        await new Promise((r) => setTimeout(r, 600));
        return J({ error: "Špatné heslo" }, 401);
      }
      return J({ token: await makeToken(env) });
    }

    // ---- media: videos/thumbnails (token in the query, <video> cannot send headers) ----
    if (p.startsWith("/media/")) {
      if (!(await okToken(env, url.searchParams.get("t"))) && !isPipeline(req, env)) return new Response("unauthorized", { status: 401 });
      const key = decodeURIComponent(p.slice(7));
      const range = req.headers.get("range");
      const obj = await env.MEDIA.get(key, range ? { range: req.headers } : undefined);
      if (!obj) return new Response("not found", { status: 404 });
      const h = new Headers();
      obj.writeHttpMetadata(h);
      h.set("etag", obj.httpEtag); h.set("accept-ranges", "bytes"); h.set("cache-control", "private, max-age=600");
      if (url.searchParams.get("dl")) h.set("content-disposition", `attachment; filename="${key.split("/").pop()}"`);
      if (range && obj.range) {
        const { offset = 0, length = obj.size - offset } = obj.range;
        h.set("content-range", `bytes ${offset}-${offset + length - 1}/${obj.size}`);
        h.set("content-length", String(length));
        return new Response(obj.body, { status: 206, headers: h });
      }
      h.set("content-length", String(obj.size));
      return new Response(obj.body, { headers: h });
    }

    if (!p.startsWith("/api/")) return env.ASSETS.fetch(req);

    // ---- pipeline endpoints ----
    if (p.startsWith("/api/upload/") && req.method === "PUT") {
      if (!isPipeline(req, env)) return J({ error: "unauthorized" }, 401);
      const key = decodeURIComponent(p.slice(12));
      await env.MEDIA.put(key, req.body, { httpMetadata: { contentType: req.headers.get("content-type") || "application/octet-stream" } });
      return J({ ok: true, key });
    }
    if (p === "/api/pipeline/clips" && req.method === "POST") {
      if (!isPipeline(req, env)) return J({ error: "unauthorized" }, 401);
      const items = await req.json();
      const q = await getQueue(env), have = new Set(q.map((c) => c.id));
      const add = (Array.isArray(items) ? items : [items]).filter((c) => c && c.id && !have.has(c.id))
        .map((c) => ({ status: "new", created: Date.now(), ...c }));
      // keep R2 inside the free 10 GB: drop the files of clips rejected / uploaded more than 7 days ago and of anything
      // older than 21 days (the record stays, so the clip is never offered again)
      const now = Date.now(), day = 864e5;
      for (const c of q) {
        const age = now - (c.updated || c.created || now);
        const stale = (["rejected", "uploaded"].includes(c.status) && age > 7 * day) || now - (c.created || now) > 21 * day;
        if (stale && c.video) { await env.MEDIA.delete([c.video, c.thumb].filter(Boolean)); c.video = null; c.thumb = null; c.status = c.status === "new" ? "expired" : c.status; }
      }
      await putQueue(env, [...add, ...q]);
      return J({ ok: true, added: add.length });
    }
    if (p === "/api/pipeline/state" && req.method === "GET") {
      if (!isPipeline(req, env)) return J({ error: "unauthorized" }, 401);
      const q = await getQueue(env);
      return J({ seen: q.map((c) => c.source).filter(Boolean), notes: (await env.KV.get("notes", "json")) || [],
        approved: q.filter((c) => c.status === "approved"), log: (await env.KV.get("lastlog")) || "",
        queue: q.map((c) => ({ id: c.id, status: c.status, streamer: c.streamer, hook: c.hook, source: c.source })) });
    }
    // the daily run's log (last run only), so problems can be read without GitHub access
    if (p === "/api/pipeline/log" && req.method === "POST") {
      if (!isPipeline(req, env)) return J({ error: "unauthorized" }, 401);
      await env.KV.put("lastlog", (await req.text()).slice(-60000));
      return J({ ok: true });
    }
    // bulk status change from the pipeline / maintenance: { ids: [...], status }
    if (p === "/api/pipeline/status" && req.method === "POST") {
      if (!isPipeline(req, env)) return J({ error: "unauthorized" }, 401);
      const { ids = [], status } = await req.json().catch(() => ({}));
      if (!["new", "approved", "rejected", "uploaded"].includes(status)) return J({ error: "bad status" }, 400);
      const q = await getQueue(env); let n = 0;
      for (const c of q) if (ids.includes(c.id)) { c.status = status; c.updated = Date.now(); n++; }
      await putQueue(env, q);
      return J({ ok: true, changed: n });
    }

    // ---- app endpoints (session token) ----
    if (!(await okToken(env, bearer(req)))) return J({ error: "unauthorized" }, 401);
    if (p === "/api/clips" && req.method === "GET") return J(await getQueue(env));
    const m = p.match(/^\/api\/clips\/([\w-]+)$/);
    if (m && req.method === "PATCH") {
      const body = await req.json().catch(() => ({}));
      const q = await getQueue(env), c = q.find((x) => x.id === m[1]);
      if (!c) return J({ error: "not found" }, 404);
      for (const k of ["status", "title", "desc", "note"]) if (typeof body[k] === "string") c[k] = body[k].slice(0, 2000);
      c.updated = Date.now();
      await putQueue(env, q);
      return J(c);
    }
    if (p === "/api/notes" && req.method === "GET") return J((await env.KV.get("notes", "json")) || []);
    if (p === "/api/notes" && req.method === "POST") {
      const { text } = await req.json().catch(() => ({}));
      if (typeof text !== "string" || !text.trim()) return J({ error: "empty" }, 400);
      const notes = (await env.KV.get("notes", "json")) || [];
      notes.unshift({ id: crypto.randomUUID().slice(0, 8), text: text.trim().slice(0, 2000), at: Date.now(), done: false });
      await env.KV.put("notes", JSON.stringify(notes.slice(0, 100)));
      return J(notes);
    }
    return J({ error: "not found" }, 404);
  },
};
