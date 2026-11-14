"""Slide identification in (Teams) recordings. Spec §4.2.

step 0  shared-content region: pixels that vary over the recording but change rarely
        (webcam tiles change every frame, toolbars never change)
step 1  change gate: dHash of the region; analyse only frames where the region changed and settled
step 2-4 OCR + match against the document library (match.RefLibrary)
step 5  merge consecutive states (build animations, same page) into segments
step 6  unmatched: unknown_slide (text-rich) or non_slide (people/gallery)
"""
import re
from collections import Counter

import cv2
import imagehash
import numpy as np
from PIL import Image
from rapidfuzz import fuzz

from . import store
from .match import RefLibrary
from .textutil import clean_screen_text, hamming, normalize_text, ocr_image, phash_hex, text_quality

REDUCE = cv2.IMREAD_REDUCED_GRAYSCALE_8   # 1280x720 -> 160x90 for region statistics
ACTIVE_RATE = 0.10     # pixels changing in >= 10% of frame pairs are "live" (webcam tiles)
GATE_BITS = 10         # dHash(16x16) distance that starts a new state
STABLE_BITS = 6        # next frame must be this close (settled, not mid-transition)
MIN_STATE_S = 2
NON_SLIDE_MIN_CHARS = 12
LIVE_MOTION_BITS = 6   # mean dHash change per second inside a state -> live video, not a slide
UNREADABLE_MIN_CHARS = 150   # a shared screen this full of (unreadable) characters is a document, not a blank
SHARED_MOTION_BITS = 24  # inside a shared area (tile strip left out) change is mostly scrolling: only fast change is video
MIN_READABLE_WORDS = 5  # an unmatched shared screen needs >= 5 confidently read words...
MIN_OCR_CONF = 60       # ...and a reasonable mean OCR confidence (garbage from faces/rooms scores low)


def _dhash(img: Image.Image) -> imagehash.ImageHash:
    return imagehash.dhash(img.convert("L"), hash_size=16)


def _best_run(profile, mass, min_val=0.04, max_gap=1):
    """Longest-mass run of consecutive indices where profile > min_val (gaps <= max_gap bridged)."""
    active = profile > min_val
    runs, start, gap = [], None, 0
    for i, a in enumerate(active):
        if a:
            if start is None:
                start = i
            gap = 0
            end = i
        elif start is not None:
            gap += 1
            if gap > max_gap:
                runs.append((start, end + 1))
                start, gap = None, 0
    if start is not None:
        runs.append((start, end + 1))
    if not runs:
        return None
    return max(runs, key=lambda r: mass[r[0]:r[1]].sum())


def detect_region(frames):
    """Shared-screen region (x, y, w, h) in frame coordinates. Falls back to the full frame.

    Signals (spec §4.2 step 0):
      variation   the shared screen shows different slides -> high p2..p98 range over time
      small_rate  webcam tiles change in small, unrelated events; slides change in big events
      projection  column/row profiles split the slide area from tiles via the static gap between them
    """
    first = cv2.imread(str(frames[0]))
    H, W = first.shape[:2]
    small = []
    fp = []
    for p in frames:
        g = cv2.imread(str(p), REDUCE)
        if g is not None:
            small.append(g)
            fp.append(_small_dhash(g))
    A = np.stack(small)
    sh, sw = A.shape[1:]
    region, method = (0, 0, W, H), "full_frame"
    dbg = {"fingerprint": fp}
    if len(A) < 3:
        return region, {**dbg, "method": method}
    D = np.abs(np.diff(A.astype(np.int16), axis=0)) > 6
    frac = D.mean(axis=(1, 2))
    big = frac > 0.3   # layout switches (gallery <-> share, share start/stop)
    # split into periods at layout switches; keep "share-like" periods (most of the screen is NOT live video)
    cuts = [0] + [i + 1 for i in np.flatnonzero(big)] + [len(A)]
    keep = np.zeros(len(A), bool)
    periods = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b - a < 3:
            continue
        rate = D[a:b - 1].mean(axis=0)
        live_frac = float((rate >= ACTIVE_RATE).mean())
        periods.append({"start": a, "end": b, "live_frac": round(live_frac, 3)})
        if live_frac < 0.4:
            keep[a:b] = True
    if not keep.any():
        keep[:] = True
    pair_keep = keep[1:] & keep[:-1] & ~big
    small_rate = D[pair_keep].mean(axis=0) if pair_keep.any() else np.zeros((sh, sw))
    A = A[keep]
    dbg["periods"] = periods
    sub = A[:: max(1, len(A) // 1500)]
    # seed: pixels that change between slides (text) but are not live video
    rng = np.percentile(sub, 99, axis=0) - np.percentile(sub, 1, axis=0)
    live = small_rate >= ACTIVE_RATE
    mask = ((rng > 30) & ~live).astype(np.uint8)
    k = max(3, sw // 22)
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    closed[live] = 0
    closed = cv2.morphologyEx(closed, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    num, labels, stats, _ = cv2.connectedComponentsWithStats(closed)
    if num > 1:
        i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        ys, xs = np.nonzero((labels == i) & (mask > 0))  # real changed pixels only (closing grows the box)
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
        fx, fy = W / sw, H / sh
        seed = (int(x0 * fx), int(y0 * fy), int(x1 * fx), int(y1 * fy))
        # grow the seed to the slide border: a straight edge in the median share frame
        idx = np.flatnonzero(keep)
        med = np.median(np.stack([cv2.imread(str(frames[j]))
                                  for j in idx[:: max(1, len(idx) // 60)]]), axis=0).astype(np.uint8)
        x0, y0, x1, y1 = _grow_to_edges(med, seed, live_full=cv2.resize(live.astype(np.uint8), (W, H)) > 0)
        if (x1 - x0) * (y1 - y0) > 0.12 * W * H:
            region, method = (x0, y0, x1 - x0, y1 - y0), "auto"
            dbg["seed"] = seed
    dbg.update(method=method, layout_switches=int(big.sum()))
    return region, dbg


# ------------------------------------------------------------------ layout over time (Zoom / Teams screen share)
# Meeting apps switch layout during a call: gallery or speaker view, then a shared screen with a strip of video
# tiles beside it, then back. One shared-screen area for the whole recording cannot describe that: with the
# whole frame as "area", the moving webcam strip makes every slide look like live video. So the layout is found
# per moment, and every period gets its own area (or none, for gallery / speaker view).
GW, GH = 80, 45          # analysis grid
LIVE_WIN = 8             # +- seconds for the live-pixel map
LIVE_RATE = 0.10         # a pixel changing in >= 10% of the seconds around t is live video
STRIP_MAX = 0.36         # the tile strip covers at most this share of the width (or height)
MIN_PERIOD_S = 15


def _live_masks(G, win=LIVE_WIN, thr=6, rate=LIVE_RATE):
    D = (np.abs(np.diff(G, axis=0)) > thr).astype(np.uint16)
    D = np.concatenate([D[:1], D])
    cs = np.concatenate([np.zeros((1,) + D.shape[1:], np.uint32), np.cumsum(D, axis=0, dtype=np.uint32)])
    n = len(G)
    i = np.arange(n)
    a, b = np.clip(i - win, 0, n), np.clip(i + win + 1, 0, n)
    return ((cs[b] - cs[a]) / (b - a)[:, None, None]) >= rate


def _strip(L, tail=0.9, still=0.004):
    """('share', side, cells) if the movement sits in a strip along one edge, ('still',..) if nothing moves,
    else ('meeting',..): movement spread over the frame (gallery / speaker view / video playing)."""
    total = L.sum()
    if total <= still * L.size:
        return ("still", None, 0)
    best = None
    for side, prof in (("right", L.sum(0)[::-1]), ("left", L.sum(0)), ("bottom", L.sum(1)[::-1]), ("top", L.sum(1))):
        k = int(np.searchsorted(np.cumsum(prof), tail * total)) + 1
        if best is None or k / len(prof) < best[2]:
            best = (side, k, k / len(prof))
    return ("share", best[0], best[1]) if best[2] <= STRIP_MAX else ("meeting", None, 0)


def _otsu(v):
    v = np.sort(np.asarray(v, float))
    if len(v) < 4:
        return float(v.mean()) if len(v) else 0.0
    best, thr = -1.0, float(v.mean())
    for c in np.unique(np.percentile(v, np.linspace(5, 95, 37))):
        a, b = v[v <= c], v[v > c]
        if len(a) and len(b):
            s = len(a) * len(b) * (a.mean() - b.mean()) ** 2
            if s > best:
                best, thr = s, float(c)
    return thr


def _content_box(side, cells, W, H):
    if side == "right":
        return (0, 0, int(W * (GW - cells - 1) / GW), H)
    if side == "left":
        return (int(W * (cells + 1) / GW), 0, W, H)
    if side == "bottom":
        return (0, 0, W, int(H * (GH - cells - 1) / GH))
    return (0, int(H * (cells + 1) / GH), W, H)


def _trim(med, box, flat=6.0):
    """Remove empty margins (black bars, app background) around the shared content inside box."""
    x0, y0, x1, y1 = box
    g = med[y0:y1, x0:x1].astype(np.float32)
    if g.size == 0:
        return box
    cols, rows = g.std(axis=0), g.std(axis=1)
    xs, ys = np.flatnonzero(cols > flat), np.flatnonzero(rows > flat)
    if len(xs) < 10 or len(ys) < 10:
        return box
    return (x0 + int(xs[0]), y0 + int(ys[0]), x0 + int(xs[-1]) + 1, y0 + int(ys[-1]) + 1)


def layout_timeline(frames):
    """[(start, end, kind, region)] with kind 'share' (region = the shared content beside the tile strip),
    'full' (nothing moves: the shared screen fills the frame) or 'meeting' (gallery / speaker view: no region).
    Also returns the 1 fps fingerprint used to find re-encoded copies, and debug figures."""
    first = cv2.imread(str(frames[0]))
    H, W = first.shape[:2]
    small, fp = [], []
    for p in frames:
        g = cv2.imread(str(p), REDUCE)
        if g is None:
            g = small[-1] if small else np.zeros((H // 8, W // 8), np.uint8)
        fp.append(_small_dhash(g))
        small.append(cv2.resize(g, (GW, GH), interpolation=cv2.INTER_AREA))
    G = np.stack(small).astype(np.float32)
    dbg = {"fingerprint": fp}
    if len(G) < 3:
        return [(0.0, float(len(frames)), "full", (0, 0, W, H))], {**dbg, "method": "too_short"}
    raw = [_strip(L) for L in _live_masks(G)]
    # each share layout (side + strip width) seen long enough gets an appearance template of its strip: the strip of
    # thumbnails looks the same while the shared document is scrolled (which makes the content area move too)
    shares = Counter((s, (e + 2) // 4) for k, s, e in raw if k == "share")
    layouts = []
    for (side, eb), n in shares.most_common(3):
        if n < 20:
            continue
        idx = [i for i, (k, s, e) in enumerate(raw) if k == "share" and s == side and (e + 2) // 4 == eb]
        cells = int(np.median([raw[i][2] for i in idx]))
        sl = {"right": (slice(None), slice(GW - cells, GW)), "left": (slice(None), slice(0, cells)),
              "bottom": (slice(GH - cells, GH), slice(None)), "top": (slice(0, cells), slice(None))}[side]
        tmpl = np.median(G[idx][:, sl[0], sl[1]], axis=0)
        dist = np.abs(G[:, sl[0], sl[1]] - tmpl).mean(axis=(1, 2))
        layouts.append({"side": side, "cells": cells, "dist": dist, "frames": n})
    thr = None
    if layouts:
        thr = float(np.clip(_otsu(np.min([lay["dist"] for lay in layouts], axis=0)), 25, 45))
    keys = []
    for i, (k, s, e) in enumerate(raw):
        best = min(range(len(layouts)), key=lambda j: layouts[j]["dist"][i]) if layouts else None
        if best is not None and (k == "share" or layouts[best]["dist"][i] < thr):
            keys.append(("share", best))
        elif k == "still":
            keys.append(("full", None))
        else:
            keys.append(("meeting", None))
    r = 10                                   # majority over +-10 s: no flicker from a single odd second
    keys = [Counter(keys[max(0, i - r):i + r + 1]).most_common(1)[0][0] for i in range(len(keys))]
    runs = []
    for i, key in enumerate(keys):
        if runs and runs[-1][2] == key:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, key])
    merged = []                              # periods shorter than MIN_PERIOD_S join the one before
    for a, b, key in runs:
        if merged and (b - a < MIN_PERIOD_S or merged[-1][2] == key):
            merged[-1][1] = b
        else:
            merged.append([a, b, key])
    out = []
    for a, b, (kind, j) in merged:
        if kind == "share":
            lay = layouts[j]
            box = _content_box(lay["side"], lay["cells"], W, H)
            pick = np.linspace(a, b - 1, min(25, b - a)).astype(int)
            med = np.median(np.stack([cv2.imread(str(frames[i]), cv2.IMREAD_GRAYSCALE) for i in pick]), axis=0)
            x0, y0, x1, y1 = _trim(med, box)
            region = (x0, y0, x1 - x0, y1 - y0)
        elif kind == "full":
            region = (0, 0, W, H)
        else:
            region = None
        out.append((float(a), float(b), kind, region))
    dbg.update(method="layout_timeline", threshold=thr,
               layouts=[{k: v for k, v in lay.items() if k != "dist"} for lay in layouts],
               periods=[[a, b, k, list(r) if r else None] for a, b, k, r in out])
    return out, dbg


def region_at(periods, t):
    for a, b, kind, region in periods:
        if a <= t < b:
            return region
    return periods[-1][3] if periods else None


def main_region(periods, W=0, H=0):
    """The shared-content area used longest (for display and older callers)."""
    best = Counter()
    for a, b, kind, region in periods:
        if region:
            best[tuple(region)] += b - a
    return list(best.most_common(1)[0][0]) if best else [0, 0, W, H]


# Interface words of meeting apps and their window menus: a shared screen that shows the meeting app itself
# (someone shares their whole screen, Teams inside Teams) is a picture of the call, not shared content.
APP_UI = ("microsoft", "teams", "zoom", "webex", "edit", "view", "window", "help", "chat", "people", "raise", "react",
          "rooms", "apps", "more", "camera", "mic", "share", "leave", "recording", "started", "participants", "mute",
          "unmute", "gallery", "together", "layout", "breakout", "reactions", "meeting", "join", "notes")


def is_app_window(ocr: str, min_hits=5) -> bool:
    """True when OCR of a shared screen is mostly a meeting app's interface ("Crm Peogle Rama React view Rooms
    Camera", "Microsoft Teama Edit View Window Help"). Misspellings are matched loosely."""
    words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", ocr or "")]
    if not words:
        return False
    hits = {u for w in words for u in APP_UI if w == u or (len(w) >= 4 and len(u) >= 4 and fuzz.ratio(w, u) >= 80)}
    named = any(fuzz.ratio(w, "teams") >= 80 or w == "zoom" for w in words) and any(w.startswith("micros") for w in words)
    return len(hits) >= min_hits or (named and len(hits) >= 3)


def find_states_timeline(frames, periods, cancel=None):
    """States per layout period: meeting views are one non-slide state; shared periods are change-gated
    inside their own area (the tile strip left out)."""
    out = []
    for a, b, kind, region in periods:
        a, b = int(a), int(b)
        if region is None:
            out.append({"start": float(a), "end": float(b), "first": a, "last": max(a, b - 1), "motion": 0.0,
                         "region": None, "meeting_view": True})
            continue
        for s in find_states(frames[a:b], region, cancel=cancel):
            out.append({**s, "start": s["start"] + a, "end": s["end"] + a, "first": s["first"] + a,
                        "last": s["last"] + a, "region": region, "shared_period": True})
    return out


def _grow_to_edges(med, seed, live_full, limit=0.4, density=0.6):
    """Move each side of the seed box outward to the first straight edge (slide border).
    Edges from every colour channel: a navy slide on a dark-grey Teams background has no luminance edge."""
    H, W = med.shape[:2]
    blur = cv2.GaussianBlur(med, (3, 3), 0)
    edges = np.zeros((H, W), bool)
    for ch in cv2.split(blur):
        edges |= cv2.Canny(ch, 20, 60) > 0
    x0, y0, x1, y1 = seed
    lx, ly = int(W * limit), int(H * limit)

    # a border can fall between two pixel rows/cols after scaling + JPEG: use a 3-pixel band
    def col_d(x, a, b):
        x = max(1, min(W - 2, x))
        return edges[a:b, x - 1:x + 2].any(axis=1).mean() if b > a else 0

    def row_d(y, a, b):
        y = max(1, min(H - 2, y))
        return edges[y - 1:y + 2, a:b].any(axis=0).mean() if b > a else 0

    def scan(start, stop, step, dens, stop_at_live):
        best = None
        for v in range(start, stop, step):
            if stop_at_live(v):
                break
            if dens(v) >= density:
                best = v
                break
        return best

    nx0 = scan(x0, max(-1, x0 - lx), -1, lambda x: col_d(x, y0, y1), lambda x: live_full[y0:y1, x].mean() > 0.3)
    nx1 = scan(x1 - 1, min(W, x1 + lx), 1, lambda x: col_d(x, y0, y1), lambda x: live_full[y0:y1, x].mean() > 0.3)
    x0 = nx0 if nx0 is not None else x0
    x1 = (nx1 + 1) if nx1 is not None else x1
    ny0 = scan(y0, max(-1, y0 - ly), -1, lambda y: row_d(y, x0, x1), lambda y: live_full[y, x0:x1].mean() > 0.3)
    ny1 = scan(y1 - 1, min(H, y1 + ly), 1, lambda y: row_d(y, x0, x1), lambda y: live_full[y, x0:x1].mean() > 0.3)
    y0 = ny0 if ny0 is not None else y0
    y1 = (ny1 + 1) if ny1 is not None else y1
    return x0, y0, x1, y1


def _small_dhash(g):
    s = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (s[:, 1:] > s[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def _crop(path, region):
    x, y, w, h = region
    return Image.open(path).convert("RGB").crop((x, y, x + w, y + h))


def find_states(frames, region, cancel=None):
    """Change-gated states: list of dicts {start, end, frame_idx (last settled frame)}."""
    states = []
    cur_hash = None
    hashes = [None] * len(frames)

    def h(i):
        if hashes[i] is None:
            hashes[i] = _dhash(_crop(frames[i], region))
        return hashes[i]

    for i in range(len(frames)):
        if cancel and cancel():
            raise InterruptedError("cancelled")
        hi = h(i)
        if cur_hash is None:
            states.append({"start": float(i), "first": i})
            cur_hash = hi
            continue
        if hi - cur_hash > GATE_BITS:
            nxt = h(i + 1) if i + 1 < len(frames) else hi
            if nxt - hi <= STABLE_BITS:  # settled -> new state
                states.append({"start": float(i), "first": i})
                cur_hash = hi
    for k, s in enumerate(states):
        s["end"] = states[k + 1]["start"] if k + 1 < len(states) else float(len(frames))
        s["last"] = int(s["end"]) - 1
        hs = [h(i) for i in range(s["first"], s["last"] + 1)]
        # live video in the shared area (gallery view, webcam, playing video) keeps changing; slides do not
        s["motion"] = float(np.mean([b - a for a, b in zip(hs[:-1], hs[1:])])) if len(hs) > 1 else 0.0
    # drop very short states (transitions) by merging into the previous one
    out = []
    for s in states:
        if out and s["end"] - s["start"] < MIN_STATE_S:
            out[-1]["end"] = s["end"]
            out[-1]["last"] = s["last"]
            out[-1]["motion"] = max(out[-1]["motion"], s["motion"])
        else:
            out.append(s)
    return out


def analyse_states(frames, region, states, lib: RefLibrary, progress=None, cancel=None, meeting_view=None):
    # a slide shown again (going back, a long workshop) is analysed once: same picture by two fingerprints
    # (64-bit pHash and the finer 256-bit dHash, so two slides of one template are not mixed up)
    seen = []
    for k, s in enumerate(states):
        if cancel and cancel():
            raise InterruptedError("cancelled")
        limit = SHARED_MOTION_BITS if s.get("shared_period") else LIVE_MOTION_BITS
        live = s.get("motion", 0) > limit  # live video (webcam): not a slide
        gallery = s.get("meeting_view") or (meeting_view is not None and meeting_view((s["start"] + s["end"]) / 2))
        if live or gallery:
            s.update(ocr="", phash="", match=None, kind="non_slide")
            if progress:
                progress((k + 1) / len(states))
            continue
        img = _crop(frames[s["last"]], s.get("region") or region)
        ph, dh = phash_hex(img), _dhash(img)
        again = next((r for p, d, r in seen if hamming(ph, p) <= 4 and (dh - d) <= 10), None)
        if again is not None:
            s.update({key: again[key] for key in ("ocr", "ocr_raw", "match", "kind", "text_quality") if key in again}, phash=ph)
            if progress:
                progress((k + 1) / len(states))
            continue
        seen.append((ph, dh, s))
        s["ocr"] = ocr_image(img)
        s["ocr_raw"] = s["ocr"]  # uncleaned: used to recognise slide text that OCR mistook for a name
        s["phash"] = phash_hex(img)
        s["match"] = lib.match(img, s["ocr"], s["phash"])
        alnum = sum(ch.isalnum() for ch in s["ocr"])
        if s["match"]:
            s["kind"] = "slide"
        elif live or alnum < NON_SLIDE_MIN_CHARS:
            s["kind"] = "non_slide"
        elif is_app_window(s["ocr"]):
            s["kind"], s["app_window"] = "non_slide", True     # the meeting app itself was shared: no content
        else:
            # a shared screen nobody uploaded must still carry readable text to count as context
            words, conf = text_quality(img)
            s["text_quality"] = {"words": words, "mean_conf": round(conf, 1)}
            s["kind"] = "unknown_slide" if words >= MIN_READABLE_WORDS and conf >= MIN_OCR_CONF else "non_slide"
            if s["kind"] == "unknown_slide":
                # store only text that reads like language; garbage would mislead the AI using the export
                s["ocr"] = clean_screen_text(s["ocr"])
            elif s.get("shared_period") and alnum >= UNREADABLE_MIN_CHARS:
                # clearly a shared document or page, but its text is too small or blurred to read in this video:
                # keep it as a shared screen (a key frame image is kept) without storing OCR garbage
                s["kind"], s["ocr"], s["unreadable"] = "unknown_slide", "", True
        if progress:
            progress((k + 1) / len(states))
    return states


def merge_states(states):
    segs = []
    for s in states:
        p = segs[-1] if segs else None
        same = False
        if p and p["kind"] == s["kind"]:
            if s["kind"] == "slide":
                same = p["match"]["slide_uid"] == s["match"]["slide_uid"]
            elif s["kind"] == "unknown_slide" and not p["ocr"] and not s["ocr"]:
                same = hamming(p["phash"], s["phash"]) <= 12      # unreadable screens: same picture
            elif s["kind"] == "unknown_slide":
                same = fuzz.token_set_ratio(normalize_text(p["ocr"]), normalize_text(s["ocr"])) >= 85
            else:
                same = True
        if same:
            p["end"], p["last"] = s["end"], s["last"]
            if s["kind"] == "slide" and s["match"]["score"] >= p["match"]["score"]:
                p["match"] = s["match"]
            if s["kind"] != "slide":
                p["ocr"], p["phash"] = s["ocr"], s["phash"]
        else:
            segs.append(dict(s))
    return segs


def prefer_consistent_docs(segs):
    """Identical slides exist in several decks; attribute each to the deck used most in this recording."""
    from . import db
    from .config import system_rank
    from .textutil import normalize_name

    def alts(uid):
        return db.q("SELECT p.id, p.file_id, p.page, f.sha256, f.system, f.name FROM pages p JOIN files f ON f.id=p.file_id "
                    "WHERE p.slide_uid=? AND f.status NOT IN ('quarantined','removed')", (uid,))

    # how many of this recording's slides each deck (identical copies counted together) contains
    counts = {}
    for s in segs:
        if s["kind"] == "slide":
            for sha in {a["sha256"] for a in alts(s["match"]["slide_uid"])}:
                counts[sha] = counts.get(sha, 0) + 1
    for s in segs:
        if s["kind"] != "slide":
            continue
        cand = alts(s["match"]["slide_uid"])
        if cand:
            # the deck that explains most of the talk; among identical copies prefer the shared, original one
            best = max(cand, key=lambda a: (counts.get(a["sha256"], 0), -system_rank(a["system"]),
                                            normalize_name(a["name"]) == a["name"].lower(), -a["file_id"]))
            s["match"].update(page_id=best["id"], file_id=best["file_id"], page=best["page"])
    return segs


def save_outputs(file_id, frames, region, segs):
    """Text only: no screen images are kept. Matched slides point to their source page;
    unknown shared screens keep their OCR text and get a content id in the slide store."""
    for s in segs:
        s["image"] = None
        if s["kind"] == "slide":
            s["slide_uid"] = s["match"]["slide_uid"]
        elif s["kind"] == "unknown_slide":
            s["slide_uid"] = store.assign_uid(normalize_text(s["ocr"]), s["phash"], None, "video")
        else:
            s["slide_uid"] = None
    return segs
