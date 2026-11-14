"""Build the 2-minute video: 1 minute of a meeting rebuilt from the distilled output, then 1 minute that explains what is real,
what is rebuilt, how close it is and how small it is.

    python pitch/replay_prep.py       (once: audio excerpts, slide pictures, timeline from the DISTILLED output)
    python pitch/replay_measure.py    (once: how close the replay is to the original, measured)
    python pitch/build_replay.py [--preview | --stills | --voice-only]
"""
import argparse
import json
import subprocess
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build as B1  # noqa: E402  (voice synthesis and timeline helpers of the storytelling build)

OUT = HERE / "out"
FPS = 30
LEAD = 0.25            # picture before the real audio starts, and after it ends
MIN_P2 = {"twist": 10.0, "sidebyside": 12.5, "howclose": 13.5, "whyai": 13.0, "finale": 5.5}


def load():
    rp = json.loads((HERE / "replay" / "replay.json").read_text(encoding="utf-8"))
    M = json.loads((HERE / "metrics.json").read_text(encoding="utf-8"))
    au = M["replay_audit"]
    checked, same = au["checked"], au["same"]
    ms = {"5": {"original_bytes": 220480662, "audio_bytes": (HERE.parent / "output" / "recordings" / "5" / "audio.ogg").stat().st_size}}
    g = ms["5"]
    ctx = {"name": M["project"]["name"], "tagline": M["project"]["tagline"], "event": M["project"]["event"],
           "share": same / max(1, checked), "checked": checked, "same": same, "orig": g["original_bytes"], "audio": g["audio_bytes"],
           "slides": 424124, "text": 136064, "rep": g["audio_bytes"] + 424124 + 136064}
    story = {
        "twist": [("nar", "Plot twist. That meeting was never played back."),
                  ("nar", "It was rebuilt from three things: the real voices, a few slide pictures, and a list of timestamps.")],
        "sidebyside": [("nar", "Here is the original, next to the rebuild."),
                       ("nar", "Real: the voices, the words, the slides, the timing."),
                       ("nar", "Rebuilt: the window, the avatars, the motion between slides.")],
        "howclose": [("nar", f"How close? At {checked} random moments, checked by eye, the replay showed the same slide as the original in {same}."),
                     ("nar", f"And the size? {ctx['orig'] / 1e6:.0f} megabytes became {ctx['rep'] / 1e6:.1f}.")],
        "whyai": [("nar", "So why call it an AI video? The voices and the slides are real. The picture around them is generated. Faces became avatars on purpose, so nobody's face is ever stored. Think of it as the cooking show of meetings.")],
        "finale": [("nar", f"{ctx['name']}. {ctx['tagline']}")],
    }
    return rp, ctx, story, M


_state = {}


def _init(ctx, scenes, rp, W, H):
    import story2 as S2
    import scenes as S1
    objs = {}
    prev = None
    replays = {}
    for e in rp["excerpts"]:
        r = S2.Replay(ctx, e, prev)
        objs[r.id] = r
        replays[e["i"]] = r
        prev = e["orig_start"] + e["dur"]
    objs["twist"] = S2.Twist(ctx)
    objs["sidebyside"] = S2.SideBySide(ctx, replays[1])
    objs["howclose"] = S2.HowClose(ctx)
    objs["whyai"] = S2.WhyAI(ctx)
    objs["finale"] = S2.Finale(ctx)
    _state.update(scenes=scenes, W=W, H=H, objs=objs)


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
    D, Bw = sc["D"], sc["B"]
    obj = _state["objs"][sc["id"]]
    g = gfx.Gfx(_state["W"], _state["H"])
    obj.frame(g, t, D, Bw)
    c = obj.cam(t, D, Bw)
    img = gfx.camera(g.img, c["zoom"], c["cx"], c["cy"], c["rot"], c["shake"])
    if sc["part"] == 2:
        colors = [(255, 110, 96), (255, 202, 70), (34, 176, 170)]
        tw = 0.36
        if t < tw:
            img = B1.wipe(img, B1.ease(t / tw), False, colors)
        if sc is not scenes[-1] and t > D - tw:
            img = B1.wipe(img, B1.ease((t - (D - tw)) / tw), True, colors)
        img = gfx.vignette(img, 0.22)
    return np.ascontiguousarray(img)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--stills", action="store_true")
    ap.add_argument("--voice-only", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    rp, ctx, story, M = load()
    narrator = M["style"].get("voice", "af_heart")
    keys, dur = B1.synth(story, narrator, 1.0)
    pads = sum(B1.LEAD + B1.TAIL + B1.GAP * (len(b) - 1) for b in story.values())
    speech = sum(dur.values())
    target = 60.0
    speed = 1.0
    if speech + pads > target:
        speed = min(1.12, round(speech / max(1.0, target - pads), 2))
        keys, dur = B1.synth(story, narrator, speed)
    B1.MIN_SCENE.update(MIN_P2)
    p2, p2_total = B1.timeline(story, keys, dur)

    scenes, t = [], 0.0
    voice_items, spans = [], []
    for e in rp["excerpts"]:
        D = e["dur"] + 2 * LEAD
        scenes.append({"id": f"replay{e['i']}", "start": t, "D": D, "B": [], "part": 1})
        voice_items.append((t + LEAD, e["audio"]))
        t += D
    part1 = t
    for s in p2:
        scenes.append({**s, "start": s["start"] + part1, "part": 2})
        voice_items += [(s["start"] + part1 + t0 - s["start"] if False else part1 + s["start"] + (t0 - s["start"]), str(OUT / "audio" / f"{name}.wav")) for t0, name in s["voice"]]
    total = part1 + p2_total
    print(f"part 1 (replay): {part1:.1f} s   part 2 (explained): {p2_total:.1f} s   total {total:.1f} s   voice speed {speed:.2f}")
    for s in scenes:
        print(f"  {s['id']:11s} {s['start']:6.1f}-{s['start'] + s['D']:6.1f}")
    (OUT / "timeline_replay.json").write_text(json.dumps({"total": total, "scenes": scenes}, indent=1), encoding="utf-8")
    if a.voice_only:
        return
    W, H = (960, 540) if a.preview else (1920, 1080)
    n_frames = int(total * FPS)

    if a.stills:
        import cv2
        sd = OUT / "stills2"
        sd.mkdir(exist_ok=True)
        _init(ctx, scenes, rp, W, H)
        for s in scenes:
            ts = [0.6, s["D"] * 0.5, s["D"] - 0.8] + [x for b in s["B"] for x in (b[0] + 0.5, b[1] - 0.3)]
            for k, tt in enumerate(sorted(set(round(x, 2) for x in ts if 0 <= x < s["D"] - 0.4))):
                f = render_frame(int((s["start"] + tt) * FPS))
                cv2.imwrite(str(sd / f"{s['id']}_{k:02d}_{tt:05.2f}.png"), f[..., ::-1])
        print("stills ->", sd)
        return

    import audio
    import story2 as S2
    import scenes as S1
    _init(ctx, scenes, rp, 1920, 1080)
    energy = {"twist": 0.9, "sidebyside": 0.7, "howclose": 1.0, "whyai": 0.9, "finale": 0.9}
    sfx = []
    for s in scenes:
        obj = _state["objs"][s["id"]]
        sfx += [(s["start"] + t0, k, g_) for t0, k, g_ in obj.sfx(s["D"], s["B"])]
        if s["part"] == 2:
            sfx.append((s["start"] + s["D"] - 0.34, "whoosh", 0.5))
            spans.append((s["start"], s["start"] + s["D"], energy[s["id"]]))
    wav = OUT / "mix_replay.wav"
    audio.mix(total + 0.3, voice_items, sfx, spans, str(wav))
    mp4 = OUT / ("datadistill-replay-preview.mp4" if a.preview else "datadistill-replay.mp4")
    cmd = [B1.ffmpeg_path(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(wav),
           "-c:v", "libx264", "-preset", "medium" if not a.preview else "veryfast", "-crf", "20" if not a.preview else "26", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(mp4)]
    t0 = time.time()
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool(a.workers, initializer=_init, initargs=(ctx, scenes, rp, W, H)) as pool:
        for i, fr in enumerate(pool.imap(render_frame, range(n_frames), chunksize=4)):
            p.stdin.write(fr.tobytes())
            if i % 120 == 0:
                print(f"  frame {i}/{n_frames}  {time.time() - t0:.0f}s", flush=True)
    p.stdin.close()
    p.wait()
    print(f"done: {mp4}  ({total:.1f} s, {mp4.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
