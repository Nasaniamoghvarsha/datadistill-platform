"""Prepare the assets of the meeting replay from the DISTILLED output only: the audio track, the stored slide pictures, the
timeline (slide changes) and the transcript lines. Nothing is read from the original video.

    python pitch/replay_prep.py   -> pitch/replay/replay.json, replay/audio/*.wav, replay/slides/*.png
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
from datadistill import package  # noqa: E402
from datadistill.config import FFMPEG  # noqa: E402

# (recording id, title shown, original start second, original end second)
EXCERPTS = [
    (5, "GraalVM thesis presentation", 0.0, 8.0),
    (5, "GraalVM thesis presentation", 21.0, 31.0),
    (5, "GraalVM thesis presentation", 446.0, 454.0),
    (3, "Tech Concept Hack", 423.0, 431.0),
    (3, "Tech Concept Hack", 1752.0, 1762.0),
    (3, "Tech Concept Hack", 1913.5, 1922.5),
]
FPS = 30
# excerpts whose slide has no stored screen picture of its own: the key frame (snapshot) taken while that slide was shown
FORCED = {4: ("snapshots/024.webp", "Tarkiye 2023"), 5: ("snapshots/025.webp", "Caribbean 2017")}
LABEL = {"S0": "Speaker 1", "S1": "Speaker 1", "S2": "Speaker 2", "S3": "Speaker 3", "S4": "Speaker 1", "S8": "Speaker 2"}


def clean_slide(path, out):
    """The shared-screen picture without the strip of participant tiles or the presenter's name label at the edge."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    im = im.crop((0, 0, int(w * 0.975), int(h * 0.97)))
    if im.width > 1400:
        im = im.resize((1400, round(im.height * 1400 / im.width)), Image.LANCZOS)
    im.save(out)
    return im.size


def main():
    (HERE / "replay" / "audio").mkdir(parents=True, exist_ok=True)
    (HERE / "replay" / "slides").mkdir(parents=True, exist_ok=True)
    cache = {}
    data = {"fps": FPS, "excerpts": []}
    for k, (fid, title, a, b) in enumerate(EXCERPTS):
        if fid not in cache:
            cache[fid] = package.load_recording(fid)
        d = cache[fid]
        dur_total = d["manifest"]["source"]["duration_s"]
        wav = HERE / "replay" / "audio" / f"ex{k}.wav"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", f"{a}", "-t", f"{b - a}", "-i", str(ROOT / "output" / "recordings" / str(fid) / "audio.ogg"),
                        "-ac", "1", "-ar", "48000", str(wav)], check=True)
        x, sr = sf.read(wav, dtype="float32")
        hop = sr // FPS
        env = np.array([np.sqrt(np.mean(x[i:i + hop] ** 2)) for i in range(0, len(x) - hop, hop)])
        env = env / (np.percentile(env, 95) + 1e-6)
        # the slide pictures shown during the excerpt
        slides = []
        for s in d["screens"]:
            if s["t_end"] <= a or s["t_start"] >= b or not s["image"] or s["kind"] == "non_slide":
                continue
            name = f"ex{k}_{s['idx']:03d}.png"
            size = clean_slide(ROOT / "output" / "recordings" / str(fid) / s["image"], HERE / "replay" / "slides" / name)
            slides.append({"file": name, "start": max(0.0, s["t_start"] - a), "end": min(b - a, s["t_end"] - a), "size": size, "title": s["title"]})
        if not slides and k in FORCED:
            name = f"ex{k}_forced.png"
            size = clean_slide(ROOT / "output" / "recordings" / str(fid) / FORCED[k][0], HERE / "replay" / "slides" / name)
            slides.append({"file": name, "start": 0.0, "end": b - a, "size": size, "title": FORCED[k][1]})
        if slides:
            slides[0]["start"] = 0.0
            slides[-1]["end"] = b - a
        turns = []
        for t in d["turns"]:
            if t["end"] <= a or t["start"] >= b:
                continue
            turns.append({"start": max(0.0, t["start"] - a), "end": min(b - a, t["end"] - a), "who": LABEL.get(t["speaker"], "Speaker 3"), "text": t["text"]})
        data["excerpts"].append({"i": k, "rec": fid, "title": title, "orig_start": a, "orig_end": b, "dur": b - a, "total": dur_total,
                                 "audio": str(wav), "env": [round(float(v), 3) for v in np.clip(env, 0, 1.4)], "slides": slides, "turns": turns})
        print(k, fid, a, b, f"{b - a:.1f}s", len(slides), "slides", len(turns), "lines")
    (HERE / "replay" / "replay.json").write_text(json.dumps(data), encoding="utf-8")


if __name__ == "__main__":
    main()
