# One-off: re-render today's hand-made clips (clips/raw) in the new layout (webcam on top, whole game under it) with
# Claude Code finding the scene + webcam box, keep the hand-picked windows / hooks, and replace them in Klipárna.
#   env: USE_CLAUDE_CODE=1, KLIPARNA_URL, PIPELINE_TOKEN
import json, os, shutil, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
import daily

RAW = Path(__file__).parents[2] / "clips" / "raw"
SRC = {s["slug"]: s for s in json.load(open(Path(__file__).parent / "sources.json", encoding="utf-8"))}
JOBS = [  # id, streamer slug, t0, t1, hook
    ("hd_car", "herdyn", 0.0, 60.0, "HERDYN HRAJE GTA 6 V AUTĚ 💀"),
    ("hd_crash", "herdyn", 1.2, 24.5, "TAXÍK ZA 200 DOLARŮ 😤"),
    ("hd_merica", "herdyn", 0.0, 28.6, "JE TOHLE VŮBEC AMERIKA? 😭"),
    ("hd_swat", "herdyn", 0.0, 25.0, "PRVNÍ SWAT V GTA 6 😭"),
    ("hd_bramb", "herdyn", 0.0, 42.0, "SNĚDL CELÝ OBCHOD 🍟"),
    ("kai_baby", "kaicenat", 13.6, 34.0, "KAI FOUND THE FIRST BABY IN GTA 👶"),
    ("spd_here", "ishowspeed", 21.0, 33.5, "SPEED'S GTA 6 COUNTDOWN 😭"),
    ("spd_rob", "ishowspeed", 0.0, 26.5, "SPEED'S FIRST ROBBERY IN GTA 6 💀"),
    ("kai_gotin", "kaicenat", 0.0, 24.5, "KAI'S FIRST SECONDS IN GTA 6 😱"),
    ("oh_fps", "ohnepixel", 0.0, 27.0, "OHNEPIXEL'S GTA 6 SETTINGS 💀"),
]
only = sys.argv[1:]
for cid, slug, t0, t1, hook in JOBS:
    if only and cid not in only: continue
    d = daily.WORK / ("re_" + cid); d.mkdir(parents=True, exist_ok=True)
    if not (d / "src.mp4").exists(): shutil.copy(RAW / cid / "src.mp4", d / "src.mp4")
    words = json.load(open(RAW / cid / "words.json", encoding="utf-8"))["words"]
    s = SRC[slug]
    mid = (t0 + t1) / 2
    daily.sh("ffmpeg", "-y", "-loglevel", "error", "-ss", f"{mid:.2f}", "-i", str(d / "src.mp4"), "-frames:v", "1", "-vf", "scale=960:540", "-q:v", "4", str(d / "frame.jpg"))
    q = 960 / s["ref"][0]
    hint = ("Usual webcam box of this streamer in the 960x540 frame: x={:.0f} y={:.0f} w={:.0f} h={:.0f}\n".format(*[v * q for v in s["cam"]])) if s.get("cam") else ""
    ask = (hint + "Only look at the frame: set scene, cam_present and the webcam box. For the other fields put keep=true, score=50, "
           "t0=0, t1=1 and empty strings.")
    r = daily.plan_cc.__wrapped__(ask, d) if hasattr(daily.plan_cc, "__wrapped__") else None
    import subprocess
    out = subprocess.run(["claude", "-p", daily.SYSTEM + "\n\nFirst read the frame image frame.jpg in the current folder. Answer only with the JSON.\n\n" + ask,
                          "--output-format", "json", "--json-schema", json.dumps(daily.SCHEMA), "--allowedTools", "Read", "--max-turns", "4"],
                         cwd=d, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    v = json.loads(out.stdout or "{}").get("structured_output") or {}
    p = {"keep": True, "t0": t0, "t1": t1, "hook": hook, "scene": "camera_only" if cid == "hd_car" else v.get("scene", "gameplay"), "cam_present": v.get("cam_present", False),
         **{k: v.get(k, 0) for k in ("cam_x", "cam_y", "cam_w", "cam_h")}}
    print(cid, p["scene"], p["cam_present"], [p[k] for k in ("cam_x", "cam_y", "cam_w", "cam_h")], flush=True)
    c = {"src": s, "url": "https://re/" + cid, "views": 0}
    f = daily.render(c, d, p, words)
    final = daily.OUT / f"{cid}.mp4"; shutil.move(f, final)
    print("  ->", final, flush=True)
