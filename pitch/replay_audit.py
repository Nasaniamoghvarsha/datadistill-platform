"""Audit sheet: random moments of the ORIGINAL videos next to the slide picture the replay shows at that second.

    python pitch/replay_audit.py   -> pitch/replay/audit.png (open it and count the pairs that show the same slide)

The count goes into metrics.json ("replay_audit"). Counting by eye is slower than a script, but every automatic way we tried
(picture hashes, template matching, OCR overlap) disagreed with what the eye sees, so the number in the video is the eye's.
The sheet contains faces from the original video: keep it out of the video and out of git.
"""
import random
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
from datadistill import package  # noqa: E402
from datadistill.config import DATA_DIR, FFMPEG  # noqa: E402

random.seed(7)
rows = []
for fid, step in ((5, 10), (3, 30)):
    d = package.load_recording(fid)
    src = DATA_DIR / d["file"]["path"]
    cand = []
    t = step / 2
    while t < d["manifest"]["source"]["duration_s"]:
        sh = next((x for x in d["screens"] if x["t_start"] <= t < x["t_end"] and x["kind"] != "non_slide" and x["image"]), None)
        if sh:
            cand.append((fid, t, src, sh))
        t += step
    rows += random.sample(cand, 10)
sheet = Image.new("RGB", (1920, 200 * 10))
tmp = HERE / "replay" / "_audit.png"
for n, (fid, t, src, sh) in enumerate(rows):
    subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", f"{t}", "-i", str(src), "-frames:v", "1", "-vf", "scale=1280:-1", str(tmp)], check=True)
    a = Image.open(tmp).convert("RGB")
    a = a.crop((0, 0, int(a.width * 0.872), a.height)).resize((356, 200))
    b = Image.open(ROOT / "output" / "recordings" / str(fid) / sh["image"]).convert("RGB").resize((356, 200))
    x0, y0 = (n % 2) * 960, (n // 2) * 200
    sheet.paste(a, (x0, y0))
    sheet.paste(b, (x0 + 360, y0))
    ImageDraw.Draw(sheet).text((x0 + 730, y0 + 10), f"#{n + 1} rec{fid} t={int(t)}s", fill=(255, 255, 0))
tmp.unlink(missing_ok=True)
sheet.save(HERE / "replay" / "audit.png")
print("pitch/replay/audit.png  (left: original frame, right: the picture the replay shows)")
