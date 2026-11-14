"""The active-speaker highlight on a participant's name label.

Several meeting apps mark who is speaking by lighting up that person's name label (a coloured chip behind the
name) instead of, or besides, a coloured border around the tile. The label is also the most reliable place to look:
it sits at a known position (where the name was read), and nothing else in the picture changes there.

Hue-agnostic: the colour of the lit chip is learned from the clearest cases of the recording itself (a label band
that lights up clearly against its own normal level), then every label band is measured against that colour.
Exactly one chip lit at a time is the signal; if several light up at once it is not trusted (see screen_names).
"""
import re
from collections import Counter, defaultdict

import cv2
import numpy as np


def slots(samples, tol_x=12, tol_y=8):
    """Label positions seen over the recording, with their most common name: [{x, y, h, w, name}]."""
    out = []
    for s in samples:
        for l in s["labels"]:
            if l.get("avatar") or "x" not in l:        # a camera-off circle with initials has a box, no name label to highlight
                continue
            for sl in out:
                if abs(sl["x"] - l["x"]) <= tol_x and abs(sl["y"] - l["y"]) <= tol_y:
                    sl["names"][l["name"]] += 1
                    sl["ws"].append(l["w"])
                    break
            else:
                out.append({"x": l["x"], "y": l["y"], "h": l["h"], "ws": [l["w"]], "names": Counter({l["name"]: 1})})
    return [{"x": s["x"], "y": s["y"], "h": s["h"], "w": float(np.median(s["ws"])), "name": s["names"].most_common(1)[0][0],
             "hits": sum(s["names"].values())} for s in out]


def _band(sl, shape):
    H, W = shape[:2]
    x0, x1 = max(0, int(sl["x"] - 6)), min(W, int(sl["x"] + max(70, sl["w"]) + 6))
    y0, y1 = max(0, int(sl["y"] - 3)), min(H, int(sl["y"] + sl["h"] + 3))
    return x0, x1, y0, y1


def _read_hsv(path):
    img = cv2.imread(str(path))
    return None if img is None else (img.shape, cv2.cvtColor(img, cv2.COLOR_BGR2HSV))


def learn_colour(frames, sl_list, step=3):
    """HSV of the lit chip: the most common colour among label-band pixels in the seconds where a band lights up far
    above its own normal level. None if no band ever does (the app has no label highlight). Every `step`-th second is
    looked at, with several frames decoded at once (reading the frames is what takes the time)."""
    if not sl_list:
        return None
    from concurrent.futures import ThreadPoolExecutor
    idx = list(range(0, len(frames), step))
    energy = [[] for _ in sl_list]
    bands = []
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(lambda t: _read_hsv(frames[t]), idx):
            if res is None:
                continue
            shape, hsv = res
            e = (hsv[..., 1].astype(np.float32) / 255) * (hsv[..., 2].astype(np.float32) / 255)
            row = []
            for i, sl in enumerate(sl_list):
                x0, x1, y0, y1 = _band(sl, shape)
                energy[i].append(float(e[y0:y1, x0:x1].mean()) if x1 > x0 and y1 > y0 else 0.0)
                row.append(hsv[y0:y1, x0:x1].copy())
            bands.append(row)
    votes = Counter()
    pix = []
    for i, sl in enumerate(sl_list):
        e = np.array(energy[i])
        if len(e) < 5:
            continue
        base = float(np.median(e))
        mad = float(np.median(np.abs(e - base))) + 1e-3
        for k in np.flatnonzero(e > max(0.08, base + 6 * mad)):
            b = bands[k][i].reshape(-1, 3)
            b = b[(b[:, 1] >= 55) & (b[:, 2] >= 110)]
            if len(b) >= 30:
                pix.append(b)
                votes[int(np.median(b[:, 0]) // 6)] += 1
    if not pix or not votes:
        return None
    hue_bin = votes.most_common(1)[0][0]
    allp = np.concatenate([p for p in pix if int(np.median(p[:, 0]) // 6) == hue_bin])
    return (float(np.median(allp[:, 0])), float(np.median(allp[:, 1])), float(np.median(allp[:, 2])), len(pix))


def measure(frames, sl_list, colour, progress=None):
    """{name: {second: share of the label band that has the chip colour}}; a name may own several slots."""
    if not sl_list or colour is None:
        return {}
    from concurrent.futures import ThreadPoolExecutor
    h0, s0, v0, _ = colour
    out = defaultdict(dict)

    def one(t):
        img = cv2.imread(str(frames[t]))
        if img is None:
            return t, []
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        dh = np.abs(hsv[..., 0].astype(np.int16) - h0)
        match = (np.minimum(dh, 180 - dh) <= 10) & (hsv[..., 1] >= max(40, s0 - 55)) & (hsv[..., 2] >= max(90, v0 - 70))
        res = []
        for sl in sl_list:
            x0, x1, y0, y1 = _band(sl, img.shape)
            if x1 > x0 and y1 > y0:
                res.append((sl["name"], float(match[y0:y1, x0:x1].mean())))
        return t, res

    with ThreadPoolExecutor(4) as ex:
        for n, (t, res) in enumerate(ex.map(one, range(len(frames)))):
            for name, f in res:
                if f > out[name].get(t, 0.0):
                    out[name][t] = round(f, 3)
            if progress and n % 120 == 0:
                progress(n / max(1, len(frames)))
    return dict(out)


def plausible(series, seconds, min_runs=3, max_duty=0.7):
    """Is this really a speaker highlight? A highlight switches on and off, one label at a time, and no label is lit
    for most of the recording. Colour that is merely present in the picture (a yellow slide, a photo) fails this.
    -> (ok, reason)"""
    lit = defaultdict(set)
    for name, d in series.items():
        for t, f in d.items():
            if f >= 0.6:
                lit[name].add(t)
    if not lit:
        return False, "no label is ever lit"
    any_lit = set().union(*lit.values())
    if not (0.02 * seconds <= len(any_lit) <= 0.95 * seconds):
        return False, f"labels lit in {len(any_lit)} of {seconds} s"
    one = sum(1 for t in any_lit if sum(1 for v in lit.values() if t in v) == 1)
    if one < 0.8 * len(any_lit):
        return False, f"several labels lit at once in {len(any_lit) - one} of {len(any_lit)} seconds"
    togglers = 0
    for name, ts in lit.items():
        if len(ts) > max_duty * seconds:
            return False, f"{name} is lit for {len(ts) / seconds:.0%} of the recording"
        runs = sum(1 for t in ts if t - 1 not in ts)
        togglers += runs >= min_runs
    if togglers < 2:
        return False, "fewer than two labels switch on and off"
    return True, ""


def dominant_label(series, seconds, min_share=0.7, max_other=0.1):
    """One label lit for most of the recording while every other label is (almost) never lit: everything that is said
    comes through that one tile, a camera shared by the people speaking (a conference-room camera is labelled with the
    room's account). Not a speaker highlight in the usual sense (it never switches between labels), but evidence of
    its own. -> (name, {second}) or None. Whether it follows SPEECH, and so is not just a coloured background, is checked
    against the transcript (visual_speakers.shared_camera)."""
    lit = {n: {t for t, f in d.items() if f >= 0.6} for n, d in series.items()}
    lit = {n: ts for n, ts in lit.items() if ts}
    if not lit or seconds <= 0:
        return None
    top = max(lit, key=lambda n: len(lit[n]))
    if len(lit[top]) < min_share * seconds:
        return None
    if any(len(ts) > max_other * seconds for n, ts in lit.items() if n != top):
        return None
    return top, lit[top]


def active_speaker(series, min_share=0.18, max_others=0.08):
    """{second: name} where exactly one label is lit (clearly above `min_share`, all others below `max_others`)."""
    secs = defaultdict(dict)
    for name, d in series.items():
        for t, f in d.items():
            secs[t][name] = f
    out = {}
    for t, d in secs.items():
        ranked = sorted(d.items(), key=lambda kv: -kv[1])
        if ranked and ranked[0][1] >= min_share and (len(ranked) == 1 or ranked[1][1] <= max_others):
            out[t] = ranked[0][0]
    return out


def run(frames, samples, progress=None):
    """-> (series {name: {sec: share}}, info). Empty series if this app has no lit-label highlight, or if what was found
    does not behave like one (info["rejected"] says why)."""
    sl = slots(samples)
    colour = learn_colour(frames, sl)
    if colour is None:
        return {}, {"slots": len(sl), "colour": None}
    series = measure(frames, sl, colour, progress)
    # scale so that "lit" is about 1: the share of the band that has the chip colour depends on the name's length
    lit = [v for d in series.values() for v in d.values() if v >= 0.15]
    if lit:
        on = max(0.2, float(np.percentile(lit, 60)))
        series = {n: {t: round(min(1.2, v / on), 3) for t, v in d.items()} for n, d in series.items()}
    info = {"slots": len(sl), "colour": {"h": round(colour[0]), "s": round(colour[1]), "v": round(colour[2])}, "learned_from": int(colour[3])}
    ok, why = plausible(series, len(frames))
    if not ok:
        dom = dominant_label(series, len(frames))
        if dom:
            info["dominant"] = dom[0]
            info["dominant_series"] = {t: 1.0 for t in dom[1]}
        return {}, {**info, "colour": None, "rejected": f"{why} (colour h{info['colour']['h'] if info['colour'] else round(colour[0])} is not a speaker highlight)"}
    return series, info
