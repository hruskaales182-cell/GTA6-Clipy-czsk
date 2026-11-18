# Push finished clips into Klipárna: uploads <id>.mp4 + a thumbnail to /api/upload, then registers the records.
#   usage: python push.py <base url> <clips.json>     (env PIPELINE_TOKEN)
#   clips.json: [{id, file, lang, streamer, title, hook, desc, tags, views, source}]
import json, os, subprocess, sys, tempfile, urllib.request

BASE, TOKEN = sys.argv[1].rstrip("/"), os.environ["PIPELINE_TOKEN"]


def req(method, path, data=None, ctype="application/json"):
    r = urllib.request.Request(BASE + path, data=data, method=method, headers={"authorization": f"Bearer {TOKEN}", "content-type": ctype})
    with urllib.request.urlopen(r, timeout=300) as resp: return json.loads(resp.read() or b"{}")


def dur(f):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f], capture_output=True, text=True).stdout or 0)


def main():
    items = json.load(open(sys.argv[2], encoding="utf-8"))
    recs = []
    for c in items:
        f = c.pop("file")
        thumb = os.path.join(tempfile.gettempdir(), f"{c['id']}_thumb.jpg")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.0", "-i", f, "-frames:v", "1", "-vf", "scale=540:960", "-q:v", "4", thumb], check=True)
        vk, tk = f"clips/{c['id']}.mp4", f"thumbs/{c['id']}.jpg"
        req("PUT", "/api/upload/" + vk, open(f, "rb").read(), "video/mp4")
        req("PUT", "/api/upload/" + tk, open(thumb, "rb").read(), "image/jpeg")
        recs.append({**c, "video": vk, "thumb": tk, "dur": round(dur(f), 1)})
        print("uploaded", c["id"], flush=True)
    print(req("POST", "/api/pipeline/clips", json.dumps(recs).encode()))


if __name__ == "__main__":
    main()
