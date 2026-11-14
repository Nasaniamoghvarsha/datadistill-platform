"""Build the 60-second storytelling pitch video.

    python pitch/build.py                 full quality 1920x1080 -> pitch/out/datadistill-pitch.mp4 (+ captions .srt)
    python pitch/build.py --preview       960x540, faster, for checking
    python pitch/build.py --stills        only write a few still frames per scene to pitch/out/stills (no video)
    python pitch/build.py --voice-only    only the voice and the timing report

The numbers (percent saved, hours, words, tests ...) come from pitch/metrics.json. Change them there and run this again:
the narration, the signs and the timing follow.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "out"
FPS = 30
LEAD, GAP, TAIL = 0.35, 0.22, 0.55
MIN_SCENE = {"intro": 6.0, "distillery": 9.5, "leaver": 6.5, "exit": 5.0}
TTS_PY = HERE / ".venv-tts" / "Scripts" / "python.exe"
VOICES = {"nar": None, "dana": "am_michael"}          # None = the narrator voice from metrics.json


def ffmpeg_path():
    sys.path.insert(0, str(HERE.parent / "src"))
    try:
        from datadistill.config import FFMPEG
        return FFMPEG
    except Exception:
        return "ffmpeg"


# ------------------------------------------------------------------ metrics -> context + story
def load_story(path=HERE / "metrics.json"):
    M = json.loads(Path(path).read_text(encoding="utf-8"))
    proj, pr, pf = M["project"], M["problem"], M["proof"]
    ctx = {"name": proj["name"], "tagline": proj["tagline"], "event": proj["event"], "team": proj.get("team", ""),
           "example_bytes": pr["example_bytes"], "example_distilled_bytes": pr["example_distilled_bytes"],
           "hours": pf["hours"], "original_bytes": pf["original_bytes"], "distilled_bytes": pf["distilled_bytes"],
           "per_recording": pf["per_recording"], "scenarios": pf["scenarios"], "tests": pf["tests"], "words": pf["words"]}
    pct = pf["per_recording"]["value"]
    d = pf["distilled_bytes"]
    small = f"{d / 1e6:.1f} megabytes" if d >= 1e6 else f"{d / 1e3:.0f} kilobytes"
    N, Dn = "nar", "dana"
    story = {
        "intro": [(N, "Meet Disky. Disky has had a long week."),
                  (N, "Every meeting recorded. Every file over a gigabyte. All named final, final, copy one.")],
        "admin": [(Dn, "Have you tried deleting something?"),
                  (N, "Nobody can search them. When people leave, their files stay behind, like a haunted OneDrive.")],
        "distillery": [(N, f"So we built {proj['name']}, a tiny distillery for knowledge."),
                       (N, "Video goes in. Out drips who said what, and which slide was on screen. The gigabytes? Evaporated.")],
        "magic": [(N, "Five people, one camera? It knows who's talking. Duplicates kept once.")],
        "search": [(N, "Ask for jvm. It opens the exact slide, at the exact second.")],
        "leaver": [(N, "When someone leaves, files are verified, tucked away, and restorable. The ghost can finally rest.")],
        "results": [(N, f"The result? {pct:g} percent less storage."),
                    (N, f"{pf['hours']:g} hours of meetings in {small}. {pf['words']:,} searchable words."),
                    (N, f"All local, zero cost. {pf['tests']} tests, all green.")],
        "exit": [(N, f"{proj['name']}. {proj['tagline']}")],
    }
    return M, ctx, story


# ------------------------------------------------------------------ voice
def synth(story, narrator, speed):
    audio_dir = OUT / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    items, keys = [], {}
    for sid, bl in story.items():
        for i, (who, txt) in enumerate(bl):
            voice = VOICES[who] or narrator
            h = hashlib.sha1(f"{voice}|{speed:.3f}|{txt}".encode()).hexdigest()[:12]
            keys[(sid, i)] = f"{sid}_{i}_{h}"
            if not (audio_dir / f"{keys[(sid, i)]}.wav").exists():
                items.append({"id": keys[(sid, i)], "text": txt, "voice": voice})
    if items:
        print(f"voice: {len(items)} line(s) to synthesise at speed {speed:.2f} ...", flush=True)
        job = {"voice": narrator, "speed": speed, "items": items, "out": str(audio_dir)}
        r = subprocess.run([str(TTS_PY), str(HERE / "tts.py")], input=json.dumps(job), capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("voice failed:\n" + r.stderr[-2000:])
    import soundfile as sf
    dur = {}
    for k, name in keys.items():
        info = sf.info(str(audio_dir / f"{name}.wav"))
        dur[k] = info.frames / info.samplerate
    return keys, dur


def timeline(story, keys, dur):
    scenes, t = [], 0.0
    for sid, bl in story.items():
        cur, windows, voice = LEAD, [], []
        for i in range(len(bl)):
            d = dur[(sid, i)]
            windows.append((cur, cur + d))
            voice.append((t + cur, keys[(sid, i)]))
            cur += d + GAP
        D = max(cur - GAP + TAIL, MIN_SCENE.get(sid, 0))
        scenes.append({"id": sid, "start": t, "D": D, "B": windows, "voice": voice})
        t += D
    return scenes, t


def write_srt(story, scenes, path):
    def ts(x):
        h, m, s = int(x // 3600), int(x % 3600 // 60), x % 60
        return f"{h:02d}:{m:02d}:{int(s):02d},{int((s % 1) * 1000):03d}"
    lines, n = [], 1
    for sc in scenes:
        for (a, b), (who, txt) in zip(sc["B"], story[sc["id"]]):
            lines += [str(n), f"{ts(sc['start'] + a)} --> {ts(sc['start'] + b)}", txt, ""]
            n += 1
    Path(path).write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------ rendering
_state = {}


def _init(ctx, scenes, W, H):
    import scenes as S
    _state["scenes"], _state["W"], _state["H"] = scenes, W, H
    _state["objs"] = {c.id: c(ctx) for c in S.SCENES}


def ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def wipe(img, k, covering, colors):
    """Diagonal stripes sweeping across, k 0..1. covering: the picture is covered; otherwise uncovered in the same direction."""
    H, W = img.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    u = xx + 0.35 * yy
    X = -200 + (W + 0.35 * H + 400) * k
    band = 150.0 * W / 1920
    d = (X - u) if covering else (u - X)
    solid = d > band * len(colors)
    out = img.copy()
    stripe = (d > 0) & ~solid
    idx = np.clip((d // band).astype(np.int32), 0, len(colors) - 1)
    if not covering:
        idx = len(colors) - 1 - idx
    cols = np.array(colors, np.uint8)
    out[stripe] = cols[idx[stripe]]
    out[solid] = np.array((24, 32, 72), np.uint8)
    return out


def render_frame(i):
    import gfx
    T = i / FPS
    scenes = _state["scenes"]
    sc = scenes[-1]
    for s in scenes:
        if s["start"] <= T < s["start"] + s["D"]:
            sc = s
            break
    t = T - sc["start"]
    D, B = sc["D"], sc["B"]
    obj = _state["objs"][sc["id"]]
    g = gfx.Gfx(_state["W"], _state["H"])
    obj.frame(g, t, D, B)
    c = obj.cam(t, D, B)
    img = gfx.camera(g.img, c["zoom"], c["cx"], c["cy"], c["rot"], c["shake"])
    first, last = sc is scenes[0], sc is scenes[-1]
    colors = [(255, 110, 96), (255, 202, 70), (34, 176, 170)]
    tw = 0.36
    if not first and t < tw:
        img = wipe(img, ease(t / tw), False, colors)
    if not last and t > D - tw:
        img = wipe(img, ease((t - (D - tw)) / tw), True, colors)
    img = gfx.vignette(img, 0.22)
    return np.ascontiguousarray(img)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--stills", action="store_true")
    ap.add_argument("--voice-only", action="store_true")
    ap.add_argument("--metrics", default=str(HERE / "metrics.json"))
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    M, ctx, story = load_story(a.metrics)
    st = M.get("style", {})
    narrator, target, max_speed = st.get("voice", "af_heart"), float(st.get("target_seconds", 60)), float(st.get("max_speed", 1.15))

    keys, dur = synth(story, narrator, 1.0)
    pads = sum(LEAD + TAIL + GAP * (len(b) - 1) for b in story.values())
    speech = sum(dur.values())
    speed = 1.0
    if speech + pads > target:
        speed = min(max_speed, round(speech / max(1.0, target - pads), 2))
        keys, dur = synth(story, narrator, speed)
    scenes, total = timeline(story, keys, dur)
    words = sum(len(t.split()) for b in story.values() for _, t in b)
    print(f"voice {narrator}, speed {speed:.2f}: {words} words, {sum(dur.values()):.1f} s of speech, video {total:.1f} s (target {target:.0f} s)")
    for s in scenes:
        print(f"  {s['id']:11s} {s['start']:5.1f}-{s['start'] + s['D']:5.1f}  " + "  ".join(f"[{x:.1f}-{y:.1f}]" for x, y in s["B"]))
    if total > target + 4:
        print(f"WARNING: {total:.1f} s is {total - target:.1f} s over the target. Shorten a sentence in build.py or raise max_speed in metrics.json.")
    (OUT / "timeline.json").write_text(json.dumps({"total": total, "scenes": scenes, "speed": speed}, indent=1), encoding="utf-8")
    write_srt(story, scenes, OUT / "datadistill-pitch.srt")
    if a.voice_only:
        return

    W, H = (960, 540) if a.preview else (1920, 1080)
    n_frames = int(total * FPS)

    if a.stills:
        sd = OUT / "stills"
        sd.mkdir(exist_ok=True)
        import cv2
        _init(ctx, scenes, W, H)
        for s in scenes:
            ts = [0.2] + [x for b in s["B"] for x in (b[0] + 0.5, (b[0] + b[1]) / 2, b[1] - 0.2)]
            for k, tt in enumerate(sorted(set(round(x, 2) for x in ts if 0 <= x < s["D"] - 0.4))):
                f = render_frame(int((s["start"] + tt) * FPS))
                cv2.imwrite(str(sd / f"{s['id']}_{k:02d}_{tt:05.2f}.png"), f[..., ::-1])
        print("stills ->", sd)
        return

    import audio
    import scenes as S
    objs = {c.id: c(ctx) for c in S.SCENES}
    energy = {"intro": 0.6, "admin": 0.55, "distillery": 0.9, "magic": 1.0, "search": 1.0, "leaver": 0.85, "results": 1.1, "exit": 0.9}
    sfx, spans, voice_items = [], [], []
    for s in scenes:
        sfx += [(s["start"] + t0, k, g_) for t0, k, g_ in objs[s["id"]].sfx(s["D"], s["B"])]
        sfx.append((s["start"] + s["D"] - 0.34, "whoosh", 0.5))
        spans.append((s["start"], s["start"] + s["D"], energy[s["id"]]))
        voice_items += [(t0, str(OUT / "audio" / f"{name}.wav")) for t0, name in s["voice"]]
    wav = OUT / "mix.wav"
    audio.mix(total + 0.3, voice_items, sfx, spans, str(wav))

    mp4 = OUT / ("datadistill-pitch-preview.mp4" if a.preview else "datadistill-pitch.mp4")
    cmd = [ffmpeg_path(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(wav), "-c:v", "libx264", "-preset", "medium" if not a.preview else "veryfast", "-crf", "20" if not a.preview else "26",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(mp4)]
    t0 = time.time()
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool(a.workers, initializer=_init, initargs=(ctx, scenes, W, H)) as pool:
        for i, fr in enumerate(pool.imap(render_frame, range(n_frames), chunksize=4)):
            p.stdin.write(fr.tobytes())
            if i % 90 == 0:
                print(f"  frame {i}/{n_frames}  {time.time() - t0:.0f}s", flush=True)
    p.stdin.close()
    p.wait()
    print(f"done: {mp4}  ({total:.1f} s, {mp4.stat().st_size / 1e6:.1f} MB, {time.time() - t0:.0f}s to render)")


if __name__ == "__main__":
    main()
