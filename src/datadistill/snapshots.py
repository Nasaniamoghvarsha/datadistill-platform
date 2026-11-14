"""Images kept of a recording (all together <= BUDGET):
  - key frames: one per distinct shared screen that is not in an uploaded document (matched slides point to
    the document page image instead),
  - snapshots: a still where the speech needs the screen to be understood.

"as you can see here", "look at this chart", "on my screen", "this slide" ... -> one frame shortly after
the phrase. Near-identical frames are stored once. All snapshots of one recording fit in BUDGET bytes:
quality and size are lowered step by step until they fit (WebP), and only if that is not enough the most
redundant images are dropped.
"""
import io
import json
import re
import shutil
import subprocess

import imagehash
from PIL import Image

from . import db
from .config import DATA_DIR, FFMPEG, OUTPUT_DIR
from .media import NO_WINDOW

BUDGET = 2_000_000          # bytes per recording (all snapshots together)
LADDER = [(1280, 72), (1280, 60), (1120, 52), (960, 46), (800, 40), (720, 34), (640, 30)]  # (max width, WebP quality)
MIN_GAP_S = 8.0             # one snapshot per moment, not one per sentence
AFTER_S = 1.5               # the thing referred to is usually on screen just after the phrase
SAME_IMAGE = 4              # dHash distance: below this two frames are the same picture

_SCREEN = r"(?:screen|slide|slides|chart|graph|diagram|table|picture|image|figure|page|map|dashboard|graphic|deck|demo|spreadsheet)"
REF = re.compile("|".join([
    r"\b(?:see|look at|looking at|showing)\s+(?:on\s+)?(?:the|my|this|that|your|our)\s+screen\b",
    r"\bon\s+(?:the|my|this|your|our)\s+screen\b",
    r"\b(?:as|if|where|what)\s+you\s+(?:can\s+|will\s+|'ll\s+)?see\b",
    r"\byou\s+(?:can|could|should|will|'ll)\s+(?:now\s+)?see\b(?!\s+(?:me|us|him|her|them|why|how|if|whether|and hear|or hear)\b)",
    r"\bcan\s+(?:you|everyone|everybody|y'all)\s+(?:all\s+)?see\s+(?:my|the|this|that|it)\b",
    r"\b(?:let me|i'?ll|i will|i'?m going to|i'?m gonna|going to)\s+(?:quickly\s+|just\s+)?(?:show|share|pull up|bring up)\b",
    r"\b(?:i'?m|i am)\s+(?:now\s+)?(?:sharing|showing|presenting)\b",
    rf"\b(?:this|that|these|those|the next|next|the previous|previous|the last|the first)\s+{_SCREEN}\b",
    r"\b(?:look|looking)\s+at\s+(?:this|that|these|those|here)\b",
    r"\b(?:shown|as shown|displayed|highlighted)\s+(?:here|on|in)\b",
    r"\bhere\s+(?:you|we)\s+(?:can\s+)?see\b",
    r"\bover\s+here\b",
]), re.I)


def find_refs(turns):
    """Moments where the speaker refers to what is on screen: [{seg_idx, phrase, t}]."""
    out = []
    for t in turns:
        text = t.get("text") or ""
        m = REF.search(text)
        if not m:
            continue
        dur = max(0.0, (t["end"] or 0) - (t["start"] or 0))
        at = t["start"] + dur * m.start() / max(1, len(text))  # no word times in the transcript: estimate by position
        out.append({"seg_idx": t["idx"], "phrase": m.group(0), "t": min(at + AFTER_S, t["end"] + 2.0)})
    kept = []
    for r in out:
        if kept and r["t"] - kept[-1]["t"] < MIN_GAP_S:
            continue
        kept.append(r)
    return kept


SHARE_WAIT_S = 20.0  # "let me share my screen" -> the share usually appears within a few seconds


def place(refs, file_id):
    """Keep a moment only if there is something to see. In a video call (participant tiles were found)
    a frame of the gallery adds nothing: the moment needs a shared screen, either at that time or
    starting shortly after ("let me show you ..."). Recordings without tiles (room camera, a
    screen recording) keep every moment."""
    rec = db.q1("SELECT participants FROM recordings WHERE file_id=?", (file_id,))
    call = len(json.loads(rec["participants"] or "[]")) >= 2 if rec else False
    shared = [(s["t_start"], s["t_end"]) for s in db.q(
        "SELECT t_start, t_end FROM rec_slides WHERE file_id=? AND kind != 'non_slide' ORDER BY t_start", (file_id,))]
    if not call:
        return refs
    out = []
    for r in refs:
        if any(a <= r["t"] <= b for a, b in shared):
            out.append(r)
            continue
        nxt = next(((a, b) for a, b in shared if r["t"] < a <= r["t"] + SHARE_WAIT_S), None)
        if nxt:
            out.append({**r, "t": min(nxt[0] + AFTER_S, nxt[1])})
    kept = []
    for r in out:
        if kept and abs(r["t"] - kept[-1]["t"]) < MIN_GAP_S:
            continue
        kept.append(r)
    return kept


def grab(path, t):
    """One frame at t seconds straight from the video (fast seek), or None."""
    cmd = [FFMPEG, "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(path), "-frames:v", "1",
           "-vf", "scale='min(1280,iw)':-2", "-f", "image2pipe", "-vcodec", "png", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, check=True, creationflags=NO_WINDOW, timeout=60).stdout
        return Image.open(io.BytesIO(raw)).convert("RGB") if raw else None
    except (subprocess.SubprocessError, OSError):
        return None


def _encode(img, width, quality):
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=quality, method=4)
    return buf.getvalue(), img.size


def fit_budget(images, budget=BUDGET):
    """Encode all images so that together they stay under budget. Returns (encoded list, setting, dropped ids)."""
    keep = list(range(len(images)))
    dropped = []
    while keep:
        for width, q in LADDER:
            enc = {i: _encode(images[i]["img"], width, q) for i in keep}
            if sum(len(b) for b, _ in enc.values()) <= budget:
                return enc, {"max_width": width, "quality": q}, dropped
        # even the smallest setting is too big: drop the image most similar to another one
        if len(keep) == 1:
            dropped.append(keep.pop())
            break
        worst = min(keep, key=lambda i: min(images[i]["hash"] - images[j]["hash"] for j in keep if j != i))
        keep.remove(worst)
        dropped.append(worst)
    return {}, None, dropped


def build(file_id, frames=None):
    """(Re)create the snapshots of one recording. frames: 1 fps frames of the pipeline (frame i at t=i s);
    without them the frames are read from the original video. Returns a summary, or None if the
    original video is gone (existing snapshots are then kept)."""
    f = db.file_row(file_id)
    path = DATA_DIR / f["path"]
    if not frames and not path.exists():
        return None
    rec = db.q1("SELECT manifest FROM recordings WHERE file_id=?", (file_id,))
    man = json.loads(rec["manifest"]) if rec else {}
    dur = man.get("source", {}).get("duration_s")
    region = (man.get("screen") or {}).get("region") or [0, 0, 0, 0]
    layouts = (man.get("screen") or {}).get("layouts") or []     # [start, end, kind, region] per layout period
    turns = db.q("SELECT idx, start, end, text FROM segments WHERE file_id=? ORDER BY idx", (file_id,))
    refs = place(find_refs(turns), file_id)
    screens = db.q("SELECT idx, t_start, t_end, kind, slide_uid FROM rec_slides WHERE file_id=? AND kind != 'non_slide' ORDER BY idx",
                   (file_id,))

    def frame_at(t):
        t = min(t, dur - 0.5) if dur else t
        if frames:
            return Image.open(frames[min(len(frames) - 1, max(0, int(round(t))))]).convert("RGB")
        return grab(path, t)

    def crop(img, t):
        """the shared-screen area only (a slide is easier to read and smaller without the participant tiles);
        the area of the layout at that moment (Zoom / Teams change layout during a meeting)"""
        x, y, w, h = next((r for a, b, k, r in layouts if a <= t < b and r), None) or region
        if w > 0 and h > 0 and (w < img.width or h < img.height):
            return img.crop((x, y, min(img.width, x + w), min(img.height, y + h)))
        return img

    images = []

    def add(img):
        h = imagehash.dhash(img, 8)
        same = next((k for k, im in enumerate(images) if h - im["hash"] <= SAME_IMAGE), None)
        if same is None:
            images.append({"img": img, "hash": h})
            same = len(images) - 1
        return same

    # 1. key frame: one image per distinct shared screen that is NOT in an uploaded document
    #    (a matched slide points to its document page instead: the single source of truth, no extra bytes)
    keyframes, by_uid = [], {}
    for s in screens:
        if s["kind"] != "unknown_slide":
            continue
        if s["slide_uid"] and s["slide_uid"] in by_uid:  # the same slide shown again
            keyframes.append({"screen_idx": s["idx"], "image_id": by_uid[s["slide_uid"]]})
            continue
        at = s["t_start"] + min(3.0, max(0.0, (s["t_end"] - s["t_start"]) / 2))
        img = frame_at(at)
        if img is None:
            continue
        iid = add(crop(img, at))
        by_uid[s["slide_uid"]] = iid
        keyframes.append({"screen_idx": s["idx"], "image_id": iid})

    # 2. snapshots where the speech refers to the screen (cropped to the shared screen while one is shown)
    entries = []
    for r in refs:
        img = frame_at(r["t"])
        if img is None:
            continue
        if any(s["t_start"] <= r["t"] <= s["t_end"] for s in screens):
            img = crop(img, r["t"])
        entries.append({**r, "t": round(min(r["t"], dur - 0.5) if dur else r["t"], 1), "image_id": add(img)})

    enc, setting, dropped = fit_budget(images)
    if dropped:  # moments whose picture was dropped use the closest remaining picture if it is similar enough
        kept = [i for i in range(len(images)) if i not in dropped]
        for e in entries + keyframes:
            if e["image_id"] in dropped:
                near = min(kept, key=lambda j: images[j]["hash"] - images[e["image_id"]]["hash"]) if kept else None
                e["image_id"] = near if near is not None and images[near]["hash"] - images[e["image_id"]]["hash"] <= 12 else None
        entries = [e for e in entries if e["image_id"] is not None]

    out_dir = OUTPUT_DIR / "recordings" / str(file_id) / "snapshots"
    shutil.rmtree(out_dir, ignore_errors=True)
    names = {}
    if enc:
        out_dir.mkdir(parents=True, exist_ok=True)
        for n, (i, (data, size)) in enumerate(sorted(enc.items())):
            name = f"snapshots/{n + 1:03d}.webp"
            (out_dir / f"{n + 1:03d}.webp").write_bytes(data)
            names[i] = (name, len(data), size)
    with db.tx() as c:
        c.execute("DELETE FROM snapshots WHERE file_id=?", (file_id,))
        c.executemany("INSERT INTO snapshots(file_id, idx, t, seg_idx, phrase, image, bytes, width, height) VALUES (?,?,?,?,?,?,?,?,?)",
                      [(file_id, k, e["t"], e["seg_idx"], e["phrase"], names[e["image_id"]][0], names[e["image_id"]][1],
                        names[e["image_id"]][2][0], names[e["image_id"]][2][1]) for k, e in enumerate(entries) if e["image_id"] in names])
        c.execute("UPDATE rec_slides SET image=NULL WHERE file_id=?", (file_id,))
        c.executemany("UPDATE rec_slides SET image=? WHERE file_id=? AND idx=?",
                      [(names[k["image_id"]][0], file_id, k["screen_idx"]) for k in keyframes if k["image_id"] in names])
    total = sum(v[1] for v in names.values())
    return {"moments": len(refs), "snapshots": len([e for e in entries if e["image_id"] in names]),
            "keyframes": len([k for k in keyframes if k["image_id"] in names]), "images": len(names),
            "bytes": total, "setting": setting, "dropped": len(dropped)}
