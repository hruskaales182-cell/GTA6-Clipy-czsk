# Daily clip run (GitHub Actions or locally): finds the most-viewed clips of the last 24 h from sources.json, transcribes
# them, lets Claude pick the best moments (trim, hook, title, description) when ANTHROPIC_API_KEY is set, renders each
# as a 1080x1920 short (whole game frame full width + the streamer's cam under it + hook + word captions) and pushes
# them into Klipárna for approval on the phone.
#   env: KLIPARNA_URL, PIPELINE_TOKEN, [ANTHROPIC_API_KEY], [MAX_CLIPS=10], [PER_SOURCE=4], [WHISPER_MODEL=medium]
import json, os, re, subprocess, sys, urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).parent
WORK = HERE / "work"; OUT = HERE / "out"
BASE, TOKEN = os.environ.get("KLIPARNA_URL", "").rstrip("/"), os.environ.get("PIPELINE_TOKEN", "")
MAX_CLIPS, PER_SOURCE = int(os.environ.get("MAX_CLIPS", 10)), int(os.environ.get("PER_SOURCE", 4))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
W, H = 1080, 1920


def sh(*a, **k): return subprocess.run(list(a), check=True, **k)
def api(method, path, data=None, ctype="application/json"):
    r = urllib.request.Request(BASE + path, data=data, method=method, headers={"authorization": f"Bearer {TOKEN}", "content-type": ctype, "user-agent": "KlipanaPipeline/1.0 (+github actions)"})
    with urllib.request.urlopen(r, timeout=300) as resp: return json.loads(resp.read() or b"{}")


# ---------------- 1. candidates ----------------
def twitch_clips(slug):
    out = subprocess.run(["yt-dlp", "--no-update", "--encoding", "utf-8", "--flat-playlist", "--print", "%(view_count)s|%(duration)s|%(title)s|%(url)s",
                          f"https://www.twitch.tv/{slug}/clips?filter=clips&range=24hr"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout or ""
    res = []
    for line in out.splitlines():
        p = line.split("|", 3)
        if len(p) == 4: res.append({"views": int(p[0]) if p[0].isdigit() else 0, "dur": float(p[1] or 0) if p[1] not in ("", "NA") else 0, "title": p[2], "url": p[3]})
    return res


def kick_clips(slug):
    try:
        r = urllib.request.Request(f"https://kick.com/api/v2/channels/{slug}/clips?sort=view&time=day", headers={"user-agent": UA, "accept": "application/json"})
        d = json.loads(urllib.request.urlopen(r, timeout=30).read())
    except Exception as e:  # Kick often blocks datacenter IPs
        print(f"kick {slug}: {e}"); return []
    return [{"views": c.get("views") or c.get("view_count") or 0, "dur": c.get("duration") or 0, "title": c.get("title") or "",
             "url": f"https://kick.com/{slug}/clips/{c['id']}", "category": (c.get("category") or {}).get("name", "")} for c in d.get("clips") or []]


def candidates(sources, seen):
    picks = []
    for s in sources:
        lst = twitch_clips(s["slug"]) if s["platform"] == "twitch" else kick_clips(s["slug"])
        lst = [c for c in lst if 8 <= c["dur"] <= 62 and c["url"] not in seen]
        lst.sort(key=lambda c: -c["views"])
        for c in lst[:PER_SOURCE]: picks.append({**c, "src": s})
        print(f"{s['name']}: {len(lst)} candidates")
    return picks


# ---------------- 2. download + transcribe ----------------
def cid_of(c): return re.sub(r"[^A-Za-z0-9_-]", "", c["src"]["slug"][:6] + "_" + c["url"].rstrip("/").split("/")[-1][-14:])

_model = None
def transcribe(f, lang):
    global _model
    import numpy as np
    from faster_whisper import WhisperModel
    if _model is None: _model = WhisperModel(os.environ.get("WHISPER_MODEL", "medium"), device="cpu", compute_type="int8")
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(f), "-ar", "16000", "-ac", "1", "-f", "s16le", "-"], capture_output=True).stdout
    if not pcm: return []
    segs, _ = _model.transcribe(np.frombuffer(pcm, np.int16).astype(np.float32) / 32768, language=lang, word_timestamps=True, beam_size=4, vad_filter=True)
    return [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for sg in segs for w in sg.words if w.word.strip()]


# ---------------- 3. Claude picks + writes ----------------
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["keep", "score", "t0", "t1", "hook", "title", "desc", "scene", "cam_present", "cam_x", "cam_y", "cam_w", "cam_h"],
          "properties": {"keep": {"type": "boolean"}, "score": {"type": "integer"}, "t0": {"type": "number"}, "t1": {"type": "number"},
                         "hook": {"type": "string"}, "title": {"type": "string"}, "desc": {"type": "string"},
                         "scene": {"type": "string", "enum": ["gameplay", "camera_only", "menu"]},
                         "cam_present": {"type": "boolean"}, "cam_x": {"type": "number"}, "cam_y": {"type": "number"},
                         "cam_w": {"type": "number"}, "cam_h": {"type": "number"}}}
SYSTEM = """You edit stream clips into YouTube Shorts for a clip channel. For each clip you get the streamer, the channel
language, the clip title and the transcript with timestamps. Decide whether it is worth posting (a funny, shocking or
hype reaction that works without context; skip clips where only game cutscene dialogue plays, nothing happens, or the
content is sexual, hateful or accuses a real person of a crime). Pick the best continuous window (t0..t1, 8-40 s,
start right before the moment, end right after the payoff). Write in the channel language: hook = the on-screen text
box at the top (max 6 words, upper case, one emoji at the end), title = YouTube title (max 70 chars, one or two
emojis), desc = one or two short lines plus 3-5 hashtags including #shorts. score = 0-100 how likely it goes viral.
Follow the user's wishes when given.
You also get one frame of the clip (960 x 540 px). scene = "gameplay" (the game fills the frame), "menu" (game menus,
loading or settings screens) or "camera_only" (the whole frame is the streamer's camera: IRL, just chatting, a car).
In gameplay and menu frames these streamers ALWAYS have a webcam overlay box somewhere, often small, dark or in a
corner: look carefully (you get the usual position as a hint) and give that box in cam_x, cam_y, cam_w, cam_h (the
frame's pixels, tight around the webcam picture only, no chat, alerts or game HUD) with cam_present = true. Only when
no webcam is visible at all, or scene = camera_only, set cam_present = false and the four numbers to 0."""


def frame_b64(d, dur):
    import base64
    f = d / "frame.jpg"
    sh("ffmpeg", "-y", "-loglevel", "error", "-ss", f"{dur * 0.5:.2f}", "-i", str(d / "src.mp4"), "-frames:v", "1", "-vf", "scale=960:540", "-q:v", "4", str(f))
    return base64.standard_b64encode(f.read_bytes()).decode()


def plan_cc(user, d, dur):
    """the same decision through Claude Code on the user's subscription (CLAUDE_CODE_OAUTH_TOKEN): print mode, the frame
    as a file it reads, structured output by --json-schema"""
    frame_b64(d, dur)  # writes d/frame.jpg
    prompt = (SYSTEM + "\n\nFirst read the frame image frame.jpg in the current folder, then decide. Answer only with the JSON.\n\n" + user)
    try:
        r = subprocess.run(["claude", "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
                            "--allowedTools", "Read", "--max-turns", "4"], cwd=d, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        out = json.loads(r.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        print(f"claude code failed: {e}")
        try: print("  rc:", r.returncode, "stdout:", (r.stdout or "")[:400].replace(chr(10), " | "), "stderr:", (r.stderr or "")[:400].replace(chr(10), " | "))
        except Exception: pass
        return None
    if out.get("is_error"): print("claude code error:", str(out.get("result"))[:200]); return None
    p = out.get("structured_output")
    if not isinstance(p, dict):
        m = re.search(r"\{.*\}", str(out.get("result", "")), re.S)
        try: p = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError: p = None
    if not isinstance(p, dict) or not all(k in p for k in ("keep", "t0", "t1", "hook", "title", "desc")): return None
    p.setdefault("score", 50); p.setdefault("cam_present", False)
    return p


def plan(c, words, dur, notes, d):
    s = c["src"]
    fallback = {"keep": True, "score": min(99, c["views"] // 5), "t0": 0.0, "t1": dur, "hook": f"{s['name'].upper()} V GTA 6 😭" if s["lang"] == "cs" else f"{s['name'].upper()} IN GTA 6 😭",
                "title": f"{s['name']} v GTA 6 😭" if s["lang"] == "cs" else f"{s['name']} in GTA 6 😭", "desc": "#gta6 #shorts", "cam_present": False}
    tr = " ".join(f"[{w['s']:.1f}]{w['w']}" for w in words) or "(no speech)"
    wish = "\n".join(f"- {n['text']}" for n in notes if not n.get("done"))[:2000]
    hint = ""
    if s.get("cam"):
        q = 960 / s["ref"][0]
        hint = "Usual webcam box of this streamer in the 960x540 frame: x={:.0f} y={:.0f} w={:.0f} h={:.0f}\n".format(*[v * q for v in s["cam"]])
    user = hint + (f"Streamer: {s['name']}\nChannel language: {'Czech' if s['lang'] == 'cs' else 'English'}\nClip title: {c['title']}\nViews: {c['views']}\n"
            f"Clip length: {dur:.1f} s\nTranscript:\n{tr}\n" + (f"\nUser's wishes:\n{wish}\n" if wish else ""))
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CODE"):
        p = plan_cc(user, d, dur)
        if p is None: return fallback
        p["t0"] = max(0.0, min(float(p["t0"]), dur - 3)); p["t1"] = max(p["t0"] + 3, min(float(p["t1"]), dur))
        return p
    if not os.environ.get("ANTHROPIC_API_KEY"): return fallback
    import anthropic
    client = anthropic.Anthropic()
    try:
        r = client.beta.messages.create(model="claude-opus-5-5", max_tokens=4000, system=SYSTEM, messages=[{"role": "user", "content": [
                                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64(d, dur)}},
                                            {"type": "text", "text": user}]}],
                                        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                                        betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    except anthropic.APIStatusError as e:
        print(f"claude error {e.status_code}: falling back"); return fallback
    except anthropic.APIConnectionError:
        print("claude unreachable: falling back"); return fallback
    if r.stop_reason == "refusal": return {**fallback, "keep": False}
    txt = next((b.text for b in r.content if b.type == "text"), "")
    try: p = json.loads(txt)
    except json.JSONDecodeError: return fallback
    p["t0"] = max(0.0, min(float(p["t0"]), dur - 3)); p["t1"] = max(p["t0"] + 3, min(float(p["t1"]), dur))
    return p


# ---------------- 4. render ----------------
BAD = re.compile(r"(?i)\b(f+u+c+k\w*|sh+i+t+\w*|bitch\w*|n+i+g+g+\w*|motherf\w*|kurv\w*|pič\w*|píč\w*|čurák\w*|kokot\w*|zmrd\w*|najeb\w*|jeb\w*)")
censor = lambda w: BAD.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), w)
def ts(t): t = max(0, t); return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def subs(hook, words, t0, t1, capY):
    out = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "", "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
           "Style: Cap,Arial Black,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,1,0,1,8,5,5,40,40,0,1",
           "Style: Hook,Arial Black,62,&H00111111,&H00111111,&H00FFFFFF,&H00FFFFFF,1,0,0,0,100,100,0,0,3,22,0,8,70,70,150,1",
           "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
           f"Dialogue: 2,{ts(0)},{ts(t1 - t0)},Hook,,0,0,0,,{{\\fad(120,0)}}{hook}"]
    ws = [dict(w, w=censor(w["w"]).upper()) for w in words if t0 - 0.05 <= w["s"] and w["e"] <= t1 + 0.05 and w["w"].strip(" .,!?…-")]
    cards, cur = [], []
    for w in ws:
        if cur and (len(cur) >= 3 or w["s"] - cur[-1]["e"] > 0.45 or re.search(r"[.!?…]$", cur[-1]["w"])): cards.append(cur); cur = []
        cur.append(w)
    if cur: cards.append(cur)
    for ci, c in enumerate(cards):
        nxt = cards[ci + 1][0]["s"] if ci + 1 < len(cards) else c[-1]["e"] + 0.6
        for i, w in enumerate(c):
            a, b = w["s"] - t0, (c[i + 1]["s"] if i + 1 < len(c) else min(nxt, c[-1]["e"] + 0.6)) - t0
            txt = " ".join(("{\\c&H3DD8FF&}" + x["w"].strip(",") + "{\\c&HFFFFFF&}") if j == i else x["w"].strip(",") for j, x in enumerate(c))
            pop = "{\\fscx88\\fscy88\\t(0,90,\\fscx106\\fscy106)\\t(90,160,\\fscx100\\fscy100)}" if i == 0 else ""
            out.append(f"Dialogue: 1,{ts(a)},{ts(b)},Cap,,0,0,0,,{{\\an5\\pos({W // 2},{capY})}}{pop}{txt}")
    return "\n".join(out) + "\n"


def render(c, d, p, words):
    s = c["src"]
    srcw, srch = map(int, subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(d / "src.mp4")],
                                         capture_output=True, text=True).stdout.strip().split(","))
    k = srcw / s["ref"][0]
    gx, gy, gw, gh = [round(v * k) for v in s["game"]]
    gH = round(W * gh / gw / 2) * 2
    # layout: hook box on top, the streamer's CAM full width under it, the WHOLE game frame full width under the cam
    # (never cropped, only scaled); without a cam the game sits in the middle. Cam = Claude's box from a frame, or the
    # source's template when no API key is set.
    if p.get("scene") == "camera_only":
        return render_irl(c, d, p, words, srcw, srch)
    cam = None
    if p.get("cam_present") and p.get("cam_w", 0) > 20 and p.get("cam_h", 0) > 20:
        fx = srcw / 960
        cam = [max(0, round(p[k2] * fx)) for k2 in ("cam_x", "cam_y", "cam_w", "cam_h")]
    elif s.get("cam"):                               # no box from Claude: the streamer's usual webcam position
        cam = [round(v * k) for v in s["cam"]]
    camH = 0
    if cam:
        cx, cy, cw, ch = cam
        cw, ch = min(cw, srcw - cx), min(ch, srch - cy)
        camW = W
        camH = round(camW * ch / cw / 2) * 2
        if camH > 760: camH = 760; camW = round(760 * cw / ch / 2) * 2     # very tall cams: keep the game on screen
    top = max(280, (H - gH - camH - 14) // 2 - 60)   # below the hook box, the block roughly centred
    if camH:
        cy0 = top; gy0 = top + camH + 14
        if gy0 + gH > H - 120: gy0 = H - 120 - gH; cy0 = gy0 - 14 - camH
    else:
        gy0 = (H - gH) // 2
    f = ["[0:v]split=3[a][b][c]",
         f"[a]crop={gw}:{gh}:{gx}:{gy},scale=-2:{H},crop={W}:{H},boxblur=28:3,eq=brightness=-0.18[bg]",
         f"[b]crop={gw}:{gh}:{gx}:{gy},scale={W}:{gH}:flags=lanczos[g]", f"[bg][g]overlay=0:{gy0}[v1]"]
    if camH:
        f += [f"[c]crop={cw}:{ch}:{cx}:{cy},scale={camW}:{camH}:flags=lanczos[cam]",
              f"[v1][cam]overlay={(W - camW) // 2}:{cy0}[v2]"]
        capY = cy0 + camH - 70                       # captions on the lower edge of the cam, the game stays clean
    else:
        f += ["[c]nullsink", "[v1]null[v2]"]
        capY = gy0 + gH + 110
    (d / "subs.ass").write_text(subs(p["hook"], words, p["t0"], p["t1"], capY), encoding="utf-8")
    f.append("[v2]setsar=1,ass=subs.ass[v]")
    out = OUT / f"{cid_of(c)}.mp4"; OUT.mkdir(exist_ok=True)
    sh("ffmpeg", "-y", "-loglevel", "error", "-ss", f"{p['t0']:.2f}", "-t", f"{p['t1'] - p['t0']:.2f}", "-i", "src.mp4", "-filter_complex", ";".join(f),
       "-map", "[v]", "-map", "0:a:0", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out), cwd=d)
    return out


def render_irl(c, d, p, words, srcw, srch):
    """the whole frame is the camera (IRL / in the car): a 4:5 crop around the centre, burned-in subtitles cut off"""
    h = round(srch * 0.88); w = round(h * 0.8 / 2) * 2; x = (srcw - w) // 2
    fH = round(W / 0.8 / 2) * 2; y0 = 300
    f = ["[0:v]split=2[a][b]", f"[a]crop={w}:{h}:{x}:0,scale=-2:{H},crop={W}:{H},boxblur=28:3,eq=brightness=-0.18[bg]",
         f"[b]crop={w}:{h}:{x}:0,scale={W}:{fH}:flags=lanczos[g]", f"[bg][g]overlay=0:{y0},setsar=1,ass=subs.ass[v]"]
    (d / "subs.ass").write_text(subs(p["hook"], words, p["t0"], p["t1"], y0 + fH - 110), encoding="utf-8")
    out = OUT / f"{cid_of(c)}.mp4"; OUT.mkdir(exist_ok=True)
    sh("ffmpeg", "-y", "-loglevel", "error", "-ss", f"{p['t0']:.2f}", "-t", f"{p['t1'] - p['t0']:.2f}", "-i", "src.mp4", "-filter_complex", ";".join(f),
       "-map", "[v]", "-map", "0:a:0", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out), cwd=d)
    return out


# ---------------- 5. push ----------------
def push(c, p, out):
    cid = cid_of(c)
    thumb = out.with_suffix(".jpg")
    sh("ffmpeg", "-y", "-loglevel", "error", "-ss", "1.0", "-i", str(out), "-frames:v", "1", "-vf", "scale=540:960", "-q:v", "4", str(thumb))
    vk, tk = f"clips/{cid}.mp4", f"thumbs/{cid}.jpg"
    api("PUT", "/api/upload/" + vk, out.read_bytes(), "video/mp4"); api("PUT", "/api/upload/" + tk, thumb.read_bytes(), "image/jpeg")
    tags = " ".join(w for w in p["desc"].split() if w.startswith("#")); desc = " ".join(w for w in p["desc"].split() if not w.startswith("#"))
    return {"id": cid, "lang": c["src"]["lang"], "streamer": c["src"]["name"], "title": p["title"], "hook": p["hook"].title() if p["hook"].isupper() else p["hook"],
            "desc": desc, "tags": tags, "views": c["views"], "source": c["url"], "score": p.get("score", 0), "video": vk, "thumb": tk, "dur": round(p["t1"] - p["t0"], 1)}


class Tee:
    """everything printed also goes to the run log that is sent to Klipárna at the end"""
    def __init__(self, s): self.s, self.buf = s, []
    def write(self, x): self.s.write(x); self.buf.append(x)
    def flush(self): self.s.flush()


def selfcheck():
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CODE")): print("AI: none (no CLAUDE_CODE_OAUTH_TOKEN)"); return
    try:
        r = subprocess.run(["claude", "-p", "Reply with the single word OK.", "--output-format", "json"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        print(f"AI self-check rc={r.returncode} out={(r.stdout or '')[:300]!r} err={(r.stderr or '')[:300]!r}")
    except Exception as e:
        print(f"AI self-check failed: {e}")


def main():
    tee = Tee(sys.stdout); sys.stdout = tee
    try: run()
    except Exception as e:
        import traceback; traceback.print_exc(file=sys.stdout); raise
    finally:
        sys.stdout = tee.s
        if BASE:
            try: api("POST", "/api/pipeline/log", "".join(tee.buf).encode("utf-8"), "text/plain; charset=utf-8")
            except Exception as e: print("log upload failed:", e)


def run():
    selfcheck()
    sources = json.load(open(HERE / "sources.json", encoding="utf-8"))
    if os.environ.get("ONLY"): sources = [s for s in sources if s["slug"] in os.environ["ONLY"].split(",")]
    state = api("GET", "/api/pipeline/state") if BASE else {"seen": [], "notes": []}
    cands = candidates(sources, set(state.get("seen", [])))
    done = []
    for c in cands:
        cid = cid_of(c); d = WORK / cid; d.mkdir(parents=True, exist_ok=True)
        try:
            if not (d / "src.mp4").exists():
                sh("yt-dlp", "--no-update", "-q", "--remux-video", "mp4", "-o", str(d / "src.%(ext)s"), c["url"])
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(d / "src.mp4")], capture_output=True, text=True).stdout)
            words = transcribe(d / "src.mp4", c["src"]["lang"])
            p = plan(c, words, dur, state.get("notes", []), d)
            print(f"{cid}: keep={p['keep']} score={p['score']} {p['hook']}")
            if p["keep"]: done.append((p["score"], c, p, words, d))
        except Exception as e:
            print(f"{cid}: skipped ({e})")
    # half of the day's clips per channel language (the Czech channel must not lose to the bigger EN streamers)
    done.sort(key=lambda x: -x[0])
    langs = sorted({x[1]["src"]["lang"] for x in done})
    pick = []
    for lg in langs: pick += [x for x in done if x[1]["src"]["lang"] == lg][: max(1, MAX_CLIPS // max(1, len(langs)))]
    pick += [x for x in done if x not in pick][: max(0, MAX_CLIPS - len(pick))]
    recs = []
    for score, c, p, words, d in pick:
        try:
            out = render(c, d, p, words)
            if BASE: recs.append(push(c, p, out))
            print("rendered", out.name)
        except Exception as e:
            print(f"{cid_of(c)}: render failed ({e})")
    if BASE and recs: print(api("POST", "/api/pipeline/clips", json.dumps(recs).encode()))
    print(f"done: {len(recs)} clips")


if __name__ == "__main__":
    main()
