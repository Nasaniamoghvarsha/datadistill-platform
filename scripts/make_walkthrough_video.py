"""Make a short walkthrough video of the platform from the pictures in docs/images (no recordings, no network).

    python scripts/make_walkthrough_video.py            ->  docs/video/datadistill-walkthrough.mp4 (+ docs/images/walkthrough-poster.png)

Each page picture is shown with a caption and scrolled slowly. Needs Pillow and ffmpeg on PATH. Uses Calibri from Windows
when present, otherwise Pillow's default font.
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "docs" / "images"
OUT = ROOT / "docs" / "video" / "datadistill-walkthrough.mp4"
W, H, FPS = 1280, 720, 15
BG, INK, MUTED, ACCENT = (244, 246, 249), (20, 24, 31), (91, 101, 114), (10, 102, 230)

SCENES = [
    ("dashboard.png", "1  Overview", "A recording becomes 99.7% smaller. What is freed, what is ready to free, what is kept."),
    ("storage.png", "2  Storage", "Every byte accounted for, and editable what-if scenarios."),
    ("meeting.png", "3  Meetings", "Checked summary, action items, speakers, and each slide shown where it comes on screen."),
    ("search.png", "4  Search", "Words and meaning. Each hit opens the exact line and the slide that was on screen."),
    ("duplicates.png", "5  Duplicates", "Copies become shortcuts. Same recording, other file: caught before transcribing."),
    ("offboarding.png", "6  Offboarding", "Dry run, named safety checks, quarantine first, deleted later, everything logged."),
    ("report.png", "7  Reports", "One PDF per file: the proof, with the whole JSON printed at the end."),
]


def font(size, bold=False):
    for name in (("calibrib.ttf" if bold else "calibri.ttf"), ("segoeuib.ttf" if bold else "segoeui.ttf")):
        try:
            return ImageFont.truetype("C:/Windows/Fonts/" + name, size)
        except OSError:
            pass
    return ImageFont.load_default()


F_T, F_S, F_B = font(34, True), font(21), font(60, True)


def card(title, sub):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((W // 2, H // 2 - 30), title, font=F_B, fill=INK, anchor="mm")
    d.text((W // 2, H // 2 + 40), sub, font=F_S, fill=MUTED, anchor="mm")
    d.rectangle((W // 2 - 60, H // 2 + 80, W // 2 + 60, H // 2 + 84), fill=ACCENT)
    return im


def frames_for(scene, seconds):
    name, title, sub = scene
    shot = Image.open(IMG / name).convert("RGB")
    vw, vh = 1120, 540                      # the window the page is seen through
    scale = min(vw / shot.width, 1.0) if shot.height > vh else min(vw / shot.width, vh / shot.height)
    if shot.width * scale < 600:            # narrow pictures (the PDF page): fit the window's width instead
        scale = 600 / shot.width
    shot = shot.resize((int(shot.width * scale), int(shot.height * scale)), Image.LANCZOS)
    n = int(seconds * FPS)
    x0 = (W - min(shot.width, vw)) // 2
    for i in range(n):
        t = i / max(1, n - 1)
        ease = t * t * (3 - 2 * t)
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        d.text((80, 38), title, font=F_T, fill=INK)
        d.text((80, 86), sub, font=F_S, fill=MUTED)
        top = int(ease * max(0, shot.height - vh))
        crop = shot.crop((max(0, (shot.width - vw) // 2), top, max(0, (shot.width - vw) // 2) + min(shot.width, vw), top + min(shot.height, vh)))
        d.rounded_rectangle((x0 - 3, 135, x0 + crop.width + 3, 135 + crop.height + 3), 10, outline=(214, 220, 228), width=2)
        im.paste(crop, (x0, 137))
        if i == 0 and name == "dashboard.png":
            im.save(IMG / "walkthrough-poster.png", optimize=True)
        yield im


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                           "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUT)],
                          stdin=subprocess.PIPE)
    def push(im, n=1):
        for _ in range(n):
            ff.stdin.write(im.tobytes())
    push(card("DataDistill", "Keep the knowledge, not the gigabytes."), 3 * FPS)
    for sc in SCENES:
        for im in frames_for(sc, 6):
            push(im)
    push(card("Everything runs on one laptop", "Free software, permissive licences, no data leaves the machine."), 3 * FPS)
    ff.stdin.close()
    ff.wait()
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
