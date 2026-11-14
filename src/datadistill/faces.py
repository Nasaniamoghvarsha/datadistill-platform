"""Who is speaking, seen from the picture: faces and mouth movement in video-call tiles.

MediaPipe Face Landmarker (Google, Apache-2.0, local) finds a face in a tile and measures how far the jaw is open
("jawOpen" blendshape). Speech makes that value change several times a second; a face that merely moves, smiles or
looks around does not. The per-second mouth activity of every tile is later compared with when each voice speaks
(visual_speakers.py).

What a tile can give depends on what it shows, and this module says which kind it is:
  live      a camera picture with a readable face: mouth movement is usable
  photo     a still picture or initials (camera off): no mouth movement exists; names / highlight must decide
  room      one camera with several people in it (conference room): every face is followed on its own
  far       a camera picture whose faces are too small to read (a wide shot of a lecture hall)
  thumb     a tile too small to analyse (the strip beside a shared screen)

No face is ever recognised or stored: only a number per tile and second (mouth activity) is kept, never an image
or a face description, and nothing is compared between meetings (GDPR: that would be biometric data).
"""
import os
import subprocess
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

os.environ.setdefault("GLOG_minloglevel", "3")      # MediaPipe prints a line per thread otherwise
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

from .config import FFMPEG, ROOT  # noqa: E402
from .media import NO_WINDOW  # noqa: E402

MODEL = ROOT / ".models" / "mediapipe" / "face_landmarker.task"
FPS = 4                 # mouth samples per second: speech moves the jaw 3-6 times a second
MIN_TILE_W = 150        # tiles narrower than this (at 1280 px frame width) cannot show a readable mouth
MIN_FACE_PX = 36        # a face smaller than this (in the original picture) is not read
CROP_H = 384            # a tile is scaled so that its height is about this before the face is searched
MAX_FACES = 8
STILL_MOTION = 1.0      # mean pixel change per sample (0-255) below which a tile is a still picture

_local = threading.local()


def available():
    try:
        import mediapipe  # noqa: F401
        return MODEL.exists()
    except Exception:
        return False


def _landmarker(num_faces):
    cache = getattr(_local, "lm", None)
    if cache is None:
        cache = _local.lm = {}
    if num_faces not in cache:
        import mediapipe as mp  # noqa: F401
        from mediapipe.tasks.python import BaseOptions, vision
        opt = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODEL)), running_mode=vision.RunningMode.IMAGE,
            num_faces=num_faces, min_face_detection_confidence=0.35, min_face_presence_confidence=0.35,
            output_face_blendshapes=True)
        cache[num_faces] = vision.FaceLandmarker.create_from_options(opt)
    return cache[num_faces]


def _detect_window(bgr, num_faces):
    import mediapipe as mp
    h, w = bgr.shape[:2]
    f = float(np.clip(CROP_H / max(1, h), 1.0, 3.0))
    img = cv2.resize(bgr, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC) if f > 1.01 else bgr
    res = _landmarker(num_faces).detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
    out = []
    for k, lm in enumerate(res.face_landmarks):
        xs = [p.x for p in lm]
        ys = [p.y for p in lm]
        box = (min(xs) * img.shape[1] / f, min(ys) * img.shape[0] / f, max(xs) * img.shape[1] / f, max(ys) * img.shape[0] / f)
        jaw = 0.0
        if res.face_blendshapes and k < len(res.face_blendshapes):
            jaw = next((b.score for b in res.face_blendshapes[k] if b.category_name == "jawOpen"), 0.0)
        out.append({"box": box, "jaw": float(jaw), "h": box[3] - box[1]})
    return out


def _iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def detect(bgr, multi=False):
    """Faces in a picture: [{box, jaw, h}] with boxes in the picture's own pixels. A tile with one person is searched
    once; `multi` (a room camera) also searches overlapping quarters, so people at the back are found too."""
    h, w = bgr.shape[:2]
    found = _detect_window(bgr, MAX_FACES if multi else 1)
    if multi and w >= 400 and h >= 240:
        for fx in (0.0, 0.5):
            for fy in (0.0, 0.5):
                x0, y0 = int(fx * w * 0.9), int(fy * h * 0.9)
                sub = bgr[y0:y0 + int(h * 0.6), x0:x0 + int(w * 0.6)]
                for f in _detect_window(sub, 4):
                    b = f["box"]
                    f["box"] = (b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0)
                    if all(_iou(f["box"], g["box"]) < 0.35 for g in found):
                        found.append(f)
    return [f for f in found if f["h"] >= MIN_FACE_PX * 0.5]


# ------------------------------------------------------------------ tiles without name labels: grid from the gutters
def grid_cells(bgr, region=None, min_cell=140):
    """Cells of a tile grid, found from the dark/flat gutters between tiles. One cell = the whole region if there are
    no gutters (speaker view, a single camera)."""
    H, W = bgr.shape[:2]
    x0, y0, x1, y1 = region or (0, 0, W, H)
    g = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)

    def segments(profile_dark, size):
        runs, start = [], None
        for i, d in enumerate(list(profile_dark) + [True]):
            if not d and start is None:
                start = i
            elif d and start is not None:
                if i - start >= min_cell:
                    runs.append((start, i))
                start = None
        return runs or [(0, size)]

    col_dark = (g.mean(0) < 40) & (g.std(0) < 12)       # a gutter column: dark and flat from top to bottom
    row_dark = (g.mean(1) < 40) & (g.std(1) < 12)
    xs, ys = segments(col_dark, g.shape[1]), segments(row_dark, g.shape[0])
    return [(x0 + a, y0 + c, x0 + b, y0 + d) for c, d in ys for a, b in xs]


# ------------------------------------------------------------------ analysis
def _frames(video, a, b, width, height, fps=FPS):
    cmd = [FFMPEG, "-v", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", str(video), "-vf",
           f"fps={fps},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, creationflags=NO_WINDOW)
    n = width * height * 3
    k = 0
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            yield a + k / fps, np.frombuffer(buf, np.uint8).reshape(height, width, 3)
            k += 1
    finally:
        p.stdout.close()
        p.kill()
        p.wait()


def _labels_at(samples, t):
    best = None
    for s in samples:
        if s["t"] <= t:
            best = s
        else:
            break
    return (best or (samples[0] if samples else {"labels": []}))["labels"]


def analyse(video, duration, periods, samples, frame_size, progress=None, cancel=None, workers=4):
    """Mouth activity of every tile of the video, per second.
    periods: [(start, end, kind, region)] from slides.layout_timeline; samples: name labels with tile boxes.
    Returns {"tiles": {tile_id: {...}}, "stats": {...}}; tile ids are the participant's name, or 'cam<n>@<start>'
    for unlabelled tiles. Only periods where tiles are large enough to read (gallery / speaker view, or a big strip
    beside a share) are processed."""
    if not available():
        return {"tiles": {}, "stats": {"available": False}}
    W, H = frame_size
    tiles = {}

    def tile(tid, name):
        return tiles.setdefault(tid, {"name": name, "samples": defaultdict(list), "faces": defaultdict(list), "motion": defaultdict(list),
                                      "box_w": [], "n_frames": 0, "n_face": 0, "multi": 0})

    total = sum(max(0.0, b - a) for a, b, k, _ in periods if k in ("meeting", "share"))
    done = [0.0]
    pool = ThreadPoolExecutor(workers)
    any_labels = any(sm["labels"] for sm in samples)
    prev_gray = {}

    def analyse_tile(tid, name, crop, multi):
        fs = detect(crop, multi=multi)
        return tid, name, fs, crop

    for a, b, kind, region in periods:
        if kind not in ("meeting", "share"):
            continue
        for t, frame in _frames(video, a, b, W, H):
            if cancel and cancel():
                raise InterruptedError("cancelled")
            labs = [l for l in _labels_at(samples, t) if l["box"][2] - l["box"][0] >= MIN_TILE_W]
            jobs = []
            if labs:
                for l in labs:
                    x0, y0, x1, y1 = l["box"]
                    jobs.append((l["name"], l["name"], (x0, y0, x1, y1)))
            elif kind == "meeting" and not any_labels:      # no names anywhere in this recording: find tiles by their gutters
                for k, box in enumerate(grid_cells(frame)):
                    jobs.append((f"cam{k + 1}@{int(a)}", None, box))
            results = list(pool.map(lambda j: analyse_tile(j[0], j[1], frame[j[2][1]:j[2][3], j[2][0]:j[2][2]],
                                                           multi=(j[2][2] - j[2][0]) > 0.45 * W), jobs))
            for (tid, name, fs, crop), (_, _, box) in zip(results, jobs):
                tl = tile(tid, name)
                tl["n_frames"] += 1
                tl["box_w"].append(box[2] - box[0])
                g = cv2.cvtColor(cv2.resize(crop, (48, 27)), cv2.COLOR_BGR2GRAY).astype(np.float32)
                if tid in prev_gray:
                    tl["motion"][int(t)].append(float(np.abs(g - prev_gray[tid]).mean()))
                prev_gray[tid] = g
                if fs:
                    tl["n_face"] += 1
                    if len(fs) > 1:
                        tl["multi"] += 1
                    fs = sorted(fs, key=lambda f: f["box"][0])
                    tl["faces"][int(t)].append([(round((f["box"][0] + f["box"][2]) / 2 / max(1, crop.shape[1]), 3), round(f["jaw"], 3)) for f in fs])
                    tl["samples"][int(t)].append(max(f["jaw"] for f in fs) if len(fs) == 1 else None)
            if progress:
                done[0] = t - a + sum(max(0.0, bb - aa) for aa, bb, kk, _ in periods if kk in ("meeting", "share") and bb <= a)
                progress(min(1.0, done[0] / max(1.0, total)))
    pool.shutdown()
    return finish(tiles, duration)


def _activity(jaws):
    """Mouth activity of one second from its jawOpen samples: how much the jaw moved (speech opens and closes it
    several times a second), plus a little for how open it is on average."""
    v = [x for x in jaws if x is not None]
    if len(v) < 2:
        return None
    d = np.abs(np.diff(v))
    return float(d.sum() + 0.25 * np.mean(v))


def finish(tiles, duration):
    out = {}
    for tid, tl in tiles.items():
        n = max(1, tl["n_frames"])
        face_share = tl["n_face"] / n
        act = {}
        for sec, jaws in tl["samples"].items():
            a = _activity(jaws)
            if a is not None:
                act[sec] = round(a, 4)
        motion = float(np.mean([np.mean(v) for v in tl["motion"].values()])) if tl["motion"] else 0.0
        jaw_all = [x for v in tl["samples"].values() for x in v if x is not None]
        jaw_range = float(np.percentile(jaw_all, 95) - np.percentile(jaw_all, 5)) if len(jaw_all) > 10 else 0.0
        width = float(np.median(tl["box_w"])) if tl["box_w"] else 0.0
        if face_share >= 0.5 and tl["multi"] / max(1, tl["n_face"]) >= 0.4:
            kind = "room"
        elif face_share >= 0.4 and motion < STILL_MOTION:
            kind = "photo"                      # a still picture: the face detector's jaw reading jitters on a small photo,
                                                # the pixels do not move (a live camera tile moves at least ~1.5)
        elif face_share >= 0.5 and motion < 1.5 and jaw_range < 0.03:
            kind = "photo"
        elif face_share >= 0.4:
            kind = "live"
        elif motion >= STILL_MOTION:
            kind = "far"                        # moving picture, no readable face
        else:
            kind = "photo"
        out[tid] = {"name": tl["name"], "kind": kind, "face_share": round(face_share, 2), "motion": round(motion, 2),
                    "jaw_range": round(jaw_range, 3), "width": round(width), "frames": tl["n_frames"], "activity": act,
                    "faces": {s: v for s, v in tl["faces"].items()} if kind == "room" else {}}
    return {"tiles": out, "stats": {"available": True, "tiles": len(out),
                                    "kinds": {k: sum(1 for t in out.values() if t["kind"] == k)
                                              for k in ("live", "photo", "room", "far")}}}
