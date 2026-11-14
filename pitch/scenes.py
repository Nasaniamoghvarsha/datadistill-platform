"""The storytelling scenes of the pitch: Disky the overstuffed hard drive, Dana the admin, the ghost of a leaver, and a tiny distillery.

A scene draws one frame from (local time t, duration D, beat windows B). B has one (start, end) per spoken beat, so each gag
and each number lands on the words that say it. Numbers come from metrics.json through `ctx`.
"""
import math
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

import art
import gfx
from art import BLUE, CORAL, INKC, LILAC, MINT, SUN, TEAL, WHITE, bubble, dana, disky, ghost, sticker, video_file
from gfx import clamp, ease_in, ease_io, ease_out, lerp, mix, rand, rrect_sprite, seg, spring, text_sprite

SHOTS = Path(__file__).resolve().parent / "shots"
SKIN_C = (246, 200, 160)


# ------------------------------------------------------------------ small helpers
def pop(t, t0, dur=0.5):
    return spring(seg(t, t0, t0 + dur) * (dur / 0.5)) if t >= t0 else 0.0


def fmt_bytes(b):
    if b >= 1e9:
        return f"{b / 1e9:.2f}".rstrip("0").rstrip(".") + " GB"
    if b >= 1e6:
        return f"{b / 1e6:.1f}".rstrip("0").rstrip(".") + " MB"
    return f"{b / 1e3:.0f} KB"


def gradient_text(g, txt, cx, cy, size, c0, c1, alpha=1.0, scale=1.0, kind="bold"):
    spr = text_sprite(txt, g.S(size), (255, 255, 255), kind).copy()
    t = np.linspace(0, 1, spr.shape[1])[None, :, None]
    spr[..., :3] = np.broadcast_to((np.array(c0) + (np.array(c1) - np.array(c0)) * t).astype(np.uint8), spr[..., :3].shape)
    sh = spr.copy()
    sh[..., :3] = INKC
    g.blit(sh, cx + 6, cy + 8, "c", alpha * 0.9, scale)
    g.blit(spr, cx, cy, "c", alpha, scale)


def title(g, txt, cx, cy, size, color=WHITE, ink=INKC, alpha=1.0, scale=1.0, kind="comic"):
    """Chunky cartoon lettering: a dark offset copy under a coloured one."""
    g.text(txt, cx + 5, cy + 7, size, ink, kind, alpha=alpha, scale=scale)
    g.text(txt, cx, cy, size, color, kind, alpha=alpha, scale=scale)


def confetti(g, cx, cy, t, seed=1, n=26, spread=420):
    if t < 0 or t > 1.6:
        return
    r_ = rand(seed)
    for i in range(n):
        ang = r_.random() * math.tau
        sp = (0.4 + r_.random()) * spread
        x = cx + math.cos(ang) * sp * ease_out(t / 1.6)
        y = cy + math.sin(ang) * sp * ease_out(t / 1.6) * 0.8 + 260 * (t / 1.6) ** 2
        col = [CORAL, SUN, TEAL, LILAC, BLUE, MINT][i % 6]
        art.rr(g, x, y, 16, 9, 3, col, rot=(t * 400 + i * 47) % 360, alpha=1 - seg(t, 1.0, 1.6))


@lru_cache(maxsize=16)
def _shot(name):
    im = cv2.imread(str(SHOTS / f"{name}.png"))
    if im is None:
        raise FileNotFoundError(f"{name}.png missing: run  python pitch/capture.py")
    return cv2.cvtColor(im, cv2.COLOR_BGR2RGB)


def shot_card(g, name, region, cx, cy, w, alpha=1.0, scale=1.0, zoom=None, hl=None, hl_p=1.0):
    """A real screenshot of the app in a cartoon browser window."""
    src = _shot(name)
    x0, y0, x1, y1 = region
    rw, rh = x1 - x0, y1 - y0
    h = w * rh / rw
    zx, zy, k = zoom or (0.5, 0.5, 1.0)
    cw_, ch_ = rw / k, rh / k
    sx0 = clamp(x0 + zx * rw - cw_ / 2, x0, x1 - cw_)
    sy0 = clamp(y0 + zy * rh - ch_ / 2, y0, y1 - ch_)
    crop = src[int(sy0):int(sy0 + ch_), int(sx0):int(sx0 + cw_)]
    pw, ph = g.S(w), g.S(h)
    img = cv2.resize(crop, (pw, ph), interpolation=cv2.INTER_AREA if crop.shape[1] > pw else cv2.INTER_CUBIC)
    for (bx0, by0, bx1, by1) in (hl or []):
        u0, v0 = (bx0 - sx0) / cw_ * pw, (by0 - sy0) / ch_ * ph
        u1, v1 = (bx1 - sx0) / cw_ * pw, (by1 - sy0) / ch_ * ph
        over = img.copy()
        cv2.rectangle(over, (int(u0), int(v0)), (int(u1), int(v1)), CORAL, max(4, g.S(7)), cv2.LINE_AA)
        img = cv2.addWeighted(over, clamp(hl_p), img, 1 - clamp(hl_p), 0)
    mask = rrect_sprite(pw, ph, g.S(26), (255, 255, 255))[..., 3]
    g.card(cx, cy, w + 22, h + 70, r=34, fill=(246, 248, 252), outline=INKC, ow=5, shadow=36, alpha=alpha, scale=scale)
    for i, c in enumerate((CORAL, SUN, MINT)):
        g.disc(cx - w / 2 + 24 + i * 30, cy - h / 2 - 22, 9, c, alpha=alpha, scale=scale)
    g.blit(np.dstack([img, mask]), cx, cy + 14, "c", alpha, scale)
    return h


# ------------------------------------------------------------------ scene base
class Scene:
    id = "scene"

    def __init__(self, ctx):
        self.ctx = ctx

    def draw(self, g, t, D, B):
        raise NotImplementedError

    def frame(self, g, t, D, B):
        self.draw(g, t, D, B)

    def cam(self, t, D, B):
        z = 1.0 + 0.03 * (t / max(D, 1e-3))
        for (a, b) in B:
            k = t - a
            if 0 <= k < 0.3:
                z += 0.035 * math.sin(math.pi * k / 0.3)
        return {"zoom": z, "cx": 0.5, "cy": 0.5, "rot": 0.0, "shake": (0, 0)}

    def sfx(self, D, B):
        return []


# ------------------------------------------------------------------ 1. meet Disky
class Intro(Scene):
    id = "intro"

    def draw(self, g, t, D, B):
        b0, b1 = B[0], B[1]
        art.bg_server(g, t, alarm=ease_in(seg(t, b1[1] - 1.2, b1[1] - 0.4)), px=t * 12)
        drop = 1 - ease_out(seg(t, 0.0, 0.55), 3)
        sq = 1 + 0.16 * math.sin(clamp(seg(t, 0.5, 0.8)) * math.pi)
        fat = 0.15 + 0.85 * ease_io(seg(t, b1[0], b1[1] - 0.3))
        bar = 0.35 + 0.65 * ease_io(seg(t, b1[0], b1[1] - 0.3))
        disky(g, 640, 640 - 760 * drop, t, fat=fat, mood="worry" if t < b1[0] + 1.0 else "panic", look=(0.5, -0.2) if t < b1[0] else (0.8, -0.8),
              sx=1 - 0.1 * (sq - 1), sy=sq, bar=bar, sweat=1.0 if t > 0.8 else 0, arms="hips" if t < b1[0] else "up")
        # beat 0: a very long week
        if t >= b0[0]:
            q = t - b0[0]
            pc = pop(q, 0.2, 0.5)
            g.card(1400, 400, 470, 390, r=26, fill=WHITE, outline=INKC, ow=5, shadow=26, scale=max(0.01, pc), alpha=min(1, pc * 1.4))
            g.card(1400, 255, 470, 76, r=22, fill=CORAL, outline=INKC, ow=5, shadow=0, scale=max(0.01, pc), alpha=min(1, pc * 1.4))
            g.text("A VERY LONG WEEK", 1400, 255, 34, WHITE, "comic", scale=max(0.01, pc), alpha=min(1, pc * 1.4))
            for i, d in enumerate(("MON", "TUE", "WED", "THU", "FRI")):
                x = 1230 + i * 85
                g.text(d, x, 320, 22, INKC, "comic", alpha=min(1, pc))
                for j in range(3):
                    pj = pop(q, 0.6 + i * 0.2 + j * 0.08, 0.4)
                    if pj > 0:
                        video_file(g, x, 372 + j * 78, "", 64, 52, [BLUE, TEAL, LILAC][j], scale=max(0.01, pj), alpha=min(1, pj))
        # beat 1: files rain on Disky, names pile up
        if t >= b1[0]:
            q = t - b1[0]
            span = max(1.0, b1[1] - b1[0])
            names = ["final.mp4", "final_FINAL.mp4", "final_v3.mp4", "final_v3 (1).mp4", "copy of copy of final.mp4"]
            r_ = rand(8)
            for i in range(26):
                t0 = 0.1 + i * (span - 1.4) / 26
                if q < t0:
                    continue
                f = seg(q - t0, 0, 0.9)
                x = 1080 + r_.random() * 520
                y = lerp(-80, 820 - (i % 5) * 36, ease_in(f, 2))
                rot = (r_.random() - 0.5) * 70 + f * 90 * (1 if i % 2 else -1)
                video_file(g, x, y, "", 140, 98, [BLUE, TEAL, LILAC, CORAL][i % 4], rot=rot * (1 - ease_out(f)))
            for i, nm in enumerate(names):
                p = pop(q, 0.5 + i * (span - 1.4) / 5, 0.5)
                if p > 0:
                    sticker(g, 1390 + (i % 2) * 90, 220 + i * 100, nm, [SUN, MINT, CORAL, WHITE, SUN][i], size=30, rot=[-5, 4, -3, 5, -4][i], p=min(1, p))
            if q > span - 0.9:
                p = pop(q, span - 0.9, 0.5)
                title(g, "DISK FULL", 960, 540, 190, CORAL, INKC, alpha=min(1, p), scale=max(0.01, p) * (1 + 0.02 * math.sin(q * 40)))

    def cam(self, t, D, B):
        c = super().cam(t, D, B)
        if t < 0.6:
            c["zoom"] += 0.15 * (1 - ease_out(t / 0.6))
        if t > B[1][1] - 0.9:
            c["shake"] = (6 * math.sin(t * 90), 5 * math.cos(t * 77))
        return c

    def sfx(self, D, B):
        out = [(0.45, "thud", 0.8), (0.5, "boing", 0.5)]
        out += [(B[0][0] + 0.6 + i * 0.2, "pop", 0.35) for i in range(5)]
        out += [(B[1][0] + 0.1 + i * 0.2, "thud", 0.35) for i in range(12)]
        out += [(B[1][1] - 0.9, "boom", 0.9), (B[1][1] - 0.9, "alarm", 0.5)]
        return out


# ------------------------------------------------------------------ 2. Dana and the ghost
class Admin(Scene):
    id = "admin"

    def draw(self, g, t, D, B):
        b0, b1 = B[0], B[1]
        art.bg_server(g, t, alarm=1.0, px=t * 10)
        faint = ease_out(seg(t, b0[1] - 0.2, b0[1] + 0.35), 3)
        disky(g, 470, 650 + 70 * faint, t, fat=1.0, mood="dead" if faint > 0.4 else "panic", rot=-16 * faint, sy=1 - 0.22 * faint, bar=1.0, sweat=1.0 if faint < 0.4 else 0, look=(0.8, 0.0))
        wx = lerp(-250, 1090, ease_out(seg(t, 0.05, 1.0), 2)) if t < b1[0] else lerp(1090, 930, ease_io(seg(t, b1[0], b1[0] + 0.6)))
        walking = 1.0 if (t < 1.0 or (b1[0] <= t < b1[0] + 0.6)) else 0.0
        dana(g, wx, 930, t, mood="panic", walk=walking, mug=True, scale=1.0)
        if t >= b0[0] + 0.5:
            q = t - b0[0] - 0.5
            bubble(g, wx + 210, 330, "Have you tried deleting something?", tail=(-110, 120), p=ease_out(seg(q, 0, 0.35)), size=40, maxw=520)
            if faint > 0.1:
                for i, z in enumerate("Zzz"):
                    pz = pop(t, b0[1] + 0.4 + i * 0.25, 0.4)
                    if pz > 0:
                        g.text(z, 600 + i * 40, 600 - i * 70, 56 + i * 12, WHITE, "comic", alpha=min(1, pz), scale=max(0.01, pz))
        if t >= b1[0]:
            q = t - b1[0]
            span = max(1.0, b1[1] - b1[0])
            gx = lerp(2200, 1480, ease_out(seg(q, 0.3, 1.4), 3))
            ghost(g, gx, 470, t, mood="sad", scale=1.0)
            for i in range(5):
                a = q * 1.5 + i * 1.256
                video_file(g, gx + math.cos(a) * 230, 480 + math.sin(a) * 120, "", 120, 84, [BLUE, TEAL, LILAC, CORAL, SUN][i], rot=math.sin(a) * 14, alpha=ease_out(seg(q, 0.9, 1.4)))
            if 0.15 < q < 2.4:
                mx = lerp(1060, 860, 0.5 + 0.5 * math.sin(q * 5))
                g.text("?", mx + 60, 820 - 30 * abs(math.sin(q * 5)), 90, CORAL, "comic")
                art.ell(g, mx, 880, 44, 44, WHITE)
                art.ell(g, mx, 880, 44, 44, INKC, thick=10)
                art.line(g, (mx + 32, 912), (mx + 80, 960), INKC, 14)
            sticker(g, 1480, 760, "haunted OneDrive", SUN, size=44, rot=-5, p=pop(q, span - 2.0, 0.5))

    def sfx(self, D, B):
        return [(0.2, "steps", 0.4), (0.6, "steps", 0.4), (B[0][0] + 0.5, "pop", 0.5), (B[0][1] - 0.1, "wah", 0.8), (B[0][1] + 0.3, "thud", 0.7),
                (B[1][0] + 0.4, "whoosh", 0.5), (B[1][1] - 1.9, "scratch", 0.7), (B[1][1] - 1.9, "pop", 0.5)]


# ------------------------------------------------------------------ 3. the distillery
class Distillery(Scene):
    id = "distillery"

    def draw(self, g, t, D, B):
        c = self.ctx
        b0, b1 = B[0], B[1]
        art.bg_lab(g, t)
        p = pop(t, 0.1, 0.8)
        k = max(0.01, p)
        heat = 0.6 + 0.7 * ease_in(seg(t, b1[0] + 0.8, b1[0] + 2.0))
        info = art.apparatus(g, 520, 930, t, heat=heat, fill=0.55 + 0.1 * math.sin(t), scale=0.92 * k)
        if t >= b0[0]:
            q = t - b0[0]
            pb = pop(q, 0.2, 0.7)
            g.card(1290, 130 + 40 * (1 - min(1, pb)), 560, 150, r=24, fill=SUN, outline=INKC, ow=6, shadow=24, alpha=min(1, pb * 1.4), scale=max(0.01, pb))
            gradient_text(g, c["name"], 1290, 130, 104, (230, 70, 80), (80, 60, 200), alpha=min(1, pb * 1.4), scale=max(0.01, pb))
            ta = ease_out(seg(q, 0.9, 1.4))
            g.card(1290, 222, 470, 46, r=23, fill=WHITE, outline=INKC, ow=4, shadow=0, alpha=ta)
            g.text("a tiny distillery for knowledge", 1290, 222, 27, INKC, "ink", alpha=ta)
        feed = seg(t, b1[0] + 0.3, b1[0] + 1.6)
        arm = "wave" if t < b1[0] else ("up" if 0 < feed < 1 else "hips")
        disky(g, 1560, 770, t, fat=0.35, mood="happy", look=(-0.7, -0.3), arms=arm, goggles=True, bar=0.4, scale=0.9)
        if t >= b1[0]:
            q = t - b1[0]
            if 0 < feed < 1:
                fx = lerp(1450, 520, ease_io(feed))
                fy = lerp(560, 380, ease_io(feed)) - 260 * math.sin(feed * math.pi)
                video_file(g, fx, fy, "", 170, 120, CORAL, rot=feed * 540, scale=lerp(1.0, 0.25, ease_in(feed)))
            if q > 1.5:
                art.steam(g, 520, info["top"][1] - 40, q, "1.2 GB", n=6, rise=420)
            if q > 2.0:
                art.drops(g, info["tip"], q, [BLUE, CORAL, LILAC], rate=1.1, fall=info["bottles"][0][1] - info["tip"][1] - 190)
            for i, (bx, by, lab, col) in enumerate(info["bottles"]):
                art.bottle(g, bx, by, lab, col, level=ease_out(seg(q, 2.4 + i * 1.3, 4.0 + i * 1.3)) * 0.8, scale=0.8)
            ps = pop(q, 5.2, 0.6)
            if ps > 0:
                g.card(1440, 500, 560, 170, r=24, fill=WHITE, outline=INKC, ow=6, shadow=24, alpha=min(1, ps), scale=max(0.01, ps))
                g.text(fmt_bytes(c["example_bytes"]), 1262, 490, 56, CORAL, "comic", alpha=min(1, ps), scale=max(0.01, ps))
                if ps > 0.9:
                    art.poly(g, [(1398, 472), (1463, 472), (1463, 452), (1503, 490), (1463, 528), (1463, 508), (1398, 508)], INKC)
                g.text(fmt_bytes(c["example_distilled_bytes"]), 1618, 490, 56, MINT, "comic", alpha=min(1, ps), scale=max(0.01, ps))
                g.text("same meeting, now", 1440, 552, 26, INKC, "ink", alpha=min(1, ps))

    def cam(self, t, D, B):
        c = super().cam(t, D, B)
        c["zoom"] += 0.10 * (1 - ease_out(seg(t, 0, 0.9), 3))
        c["cy"] = 0.55
        return c

    def sfx(self, D, B):
        out = [(0.1, "boing", 0.5), (0.18, "pop", 0.4), (B[0][0] + 0.3, "ding", 0.6), (B[1][0] + 0.3, "whoosh", 0.5), (B[1][0] + 1.6, "pop", 0.4), (B[1][0] + 1.9, "hiss", 0.4)]
        out += [(B[1][0] + 2.3 + i * 0.55, "drip", 0.35) for i in range(7)]
        return out + [(B[1][0] + 5.2, "ding", 0.6)]


# ------------------------------------------------------------------ 4. who is talking, and one copy only
class Magic(Scene):
    id = "magic"

    def draw(self, g, t, D, B):
        art.bg_lab(g, t)
        b0 = B[0]
        q = t - b0[0]
        span = max(1.0, b0[1] - b0[0])
        pl = pop(t, 0.1, 0.6)
        g.card(560, 520, 840, 620, r=36, fill=(34, 44, 84), outline=INKC, ow=6, shadow=34, scale=max(0.01, pl), alpha=min(1, pl * 1.4))
        g.text("ONE CAMERA", 560, 250, 34, SUN, "comic", alpha=min(1, pl))
        talk = int((max(0, q) / max(1.0, span * 0.45)) * 5) % 5 if q > 0 else 0
        pos = [(330, 400), (560, 400), (790, 400), (440, 600), (680, 600)]
        cols = [CORAL, MINT, SUN, LILAC, BLUE]
        for i, (x, y) in enumerate(pos):
            pp = pop(t, 0.3 + i * 0.1, 0.5)
            if pp <= 0:
                continue
            sp = (i == talk) and 0.1 < q < span * 0.5
            art.ell(g, x, y, 62 * pp, 62 * pp, mix(cols[i], WHITE, 0.25))
            art.ell(g, x, y - 12 * pp, 30 * pp, 30 * pp, SKIN_C)
            art.ell(g, x, y + 56 * pp, 44 * pp, 30 * pp, cols[i])
            if sp:
                art.ell(g, x, y, 72, 72, SUN, thick=8)
                sticker(g, x, y - 100, f"Speaker {i + 1}", cols[i], size=28, rot=-3)
        g.text("…and it knows who is talking", 560, 790, 30, WHITE, "ink", alpha=ease_out(seg(q, 0.8, 1.3)))
        if q > span * 0.45:
            r = q - span * 0.45
            a = ease_out(seg(r, 0, 0.3))
            g.card(1400, 520, 760, 620, r=36, fill=WHITE, outline=INKC, ow=6, shadow=34, alpha=a)
            g.text("SAME DECK, THREE FOLDERS", 1400, 250, 32, INKC, "comic", alpha=a)
            conv = ease_io(seg(r, 0.6, 1.5))
            for k in range(3):
                x = lerp(1180 + k * 220, 1400, conv)
                y = lerp(450 + (k % 2) * 90, 520, conv)
                art.rr(g, x, y, 190, 130, 14, (244, 247, 255), outline=INKC, ow=4, alpha=a)
                art.rr(g, x - 40, y - 28, 70, 26, 6, mix(BLUE, WHITE, 0.4), alpha=a)
                art.rr(g, x + 24, y + 16, 90, 40, 8, mix(CORAL, WHITE, 0.5), alpha=a)
            if conv > 0.95:
                sticker(g, 1400, 660, "KEPT ONCE", MINT, size=60, rot=-6, p=pop(r, 1.5, 0.5))
                g.text("duplicates found automatically", 1400, 760, 28, INKC, "ink", alpha=ease_out(seg(r, 1.8, 2.3)))

    def sfx(self, D, B):
        b0 = B[0]
        span = max(1.0, b0[1] - b0[0])
        out = [(0.1, "pop", 0.5)] + [(0.3 + i * 0.1, "pop", 0.25) for i in range(5)]
        out += [(b0[0] + 0.1 + i * span * 0.09, "tick", 0.4) for i in range(5)]
        return out + [(b0[0] + span * 0.45 + 1.3, "whoosh", 0.5), (b0[0] + span * 0.45 + 2.1, "stamp", 0.8)]


# ------------------------------------------------------------------ 5. ask, and it opens the slide
class Search(Scene):
    id = "search"

    def draw(self, g, t, D, B):
        b0 = B[0]
        art.bg_library(g, t)
        p = pop(t, 0.1, 0.6)
        k = 1.0 + 0.5 * ease_io(seg(t, b0[0] + 1.4, b0[1] - 0.3))
        shot_card(g, "search", (448, 200, 1472, 575), 1150, 470, 1260, alpha=min(1, p * 1.4), scale=max(0.01, p), zoom=(0.17, 0.55, k),
                  hl=[(465, 365, 1460, 470)], hl_p=ease_out(seg(t, b0[0] + 1.2, b0[0] + 1.7)))
        disky(g, 330, 700, t, fat=0.3, mood="happy", look=(0.9, -0.5), arms="point", bar=0.3, scale=0.85)
        cx = lerp(900, 640, ease_io(seg(t, b0[0] + 0.5, b0[0] + 1.3)))
        cy = lerp(300, 520, ease_io(seg(t, b0[0] + 0.5, b0[0] + 1.3)))
        pts = [(cx, cy), (cx + 24, cy + 70), (cx + 38, cy + 46), (cx + 66, cy + 40)]
        art.poly(g, pts, WHITE)
        art.polyline(g, pts, INKC, 5, closed=True)
        if t > b0[0] + 1.8:
            sticker(g, 1480, 880, "the exact second", MINT, size=44, rot=-4, p=pop(t, b0[0] + 1.8, 0.5))
            confetti(g, 1480, 880, t - b0[0] - 1.8, seed=3, n=18, spread=260)
        bubble(g, 330, 330, "jvm?  Found it!", tail=(20, 190), p=pop(t, b0[0] + 1.0, 0.4), size=38, maxw=420)

    def sfx(self, D, B):
        b0 = B[0]
        return [(0.1, "pop", 0.5)] + [(b0[0] + 0.15 + i * 0.12, "click", 0.4) for i in range(3)] + [(b0[0] + 1.3, "click", 0.7), (b0[0] + 1.8, "ding", 0.7), (b0[0] + 1.8, "whoosh", 0.4)]


# ------------------------------------------------------------------ 6. when someone leaves
class Leaver(Scene):
    id = "leaver"

    def draw(self, g, t, D, B):
        b0 = B[0]
        art.bg_library(g, t)
        q = t - b0[0]
        span = max(3.0, b0[1] - b0[0])
        lockp = seg(q, span * 0.45, span * 0.62)
        art.shadow(g, 1480, 880, 230, 28, 0.25)
        art.rr(g, 1480, 760, 420, 240, 30, (122, 90, 200), outline=INKC, ow=6)
        art.rr(g, 1480, 650, 420, 90, 30, (146, 112, 224), outline=INKC, ow=6)
        art.rr(g, 1480, 700, 70, 80, 14, SUN, outline=INKC, ow=5)
        if lockp > 0:
            gfx.icon(g, "shield", 1480, 540, 130, MINT, alpha=ease_out(lockp))
        happy = q > span * 0.72
        gx = 760 + (120 * ease_out(seg(q, span * 0.72, span * 0.9)) if happy else 0)
        ghost(g, gx, 470, t, mood="content" if happy else "sad", scale=1.0, wave=not happy)
        if happy:
            art.ell(g, gx, 330, 90, 24, SUN, thick=8)
            for i, z in enumerate("Zzz"):
                pz = pop(q, span * 0.8 + i * 0.25, 0.4)
                g.text(z, gx + 150 + i * 36, 380 - i * 60, 52 + i * 12, WHITE, "comic", alpha=min(1, pz), scale=max(0.01, pz))
        for i in range(6):
            t0 = span * (0.12 + i * 0.05)
            f = seg(q, t0, t0 + 1.0)
            if f <= 0 or f >= 1:
                continue
            x = lerp(760 + (i % 3) * 120, 1480, ease_io(f))
            y = lerp(380 + (i % 2) * 90, 720, ease_io(f)) - 200 * math.sin(f * math.pi)
            video_file(g, x, y, "", 130, 92, [BLUE, TEAL, LILAC, CORAL, SUN, MINT][i], rot=f * 200, scale=lerp(1.0, 0.4, f))
        for i, (txt, col, t0) in enumerate((("verified", TEAL, 0.5), ("tucked away", LILAC, 0.62), ("restorable", MINT, 0.78))):
            sticker(g, 1480, 240 + i * 88, txt, col, size=40, rot=[-4, 3, -3][i], p=pop(q, span * t0, 0.5))
        dana(g, 300, 930, t, mood="calm", mug=False, thumbs=q > span * 0.75, scale=0.95)

    def sfx(self, D, B):
        span = max(3.0, B[0][1] - B[0][0])
        b = B[0][0]
        out = [(0.15, "pop", 0.4)] + [(b + span * (0.12 + i * 0.05) + 0.9, "thud", 0.35) for i in range(6)]
        return out + [(b + span * 0.5, "click", 0.9), (b + span * 0.52, "ding", 0.7), (b + span * 0.74, "ding", 0.6), (b + span * 0.74, "sparkle", 0.5)]


# ------------------------------------------------------------------ 7. results
class Results(Scene):
    id = "results"

    def draw(self, g, t, D, B):
        c = self.ctx
        art.bg_sky(g, t)
        ba, bb, bc = B[0], B[1], B[2]
        slim = ease_out(seg(t, 0.1, 1.2))
        disky(g, 520, 700, t, fat=0.35 * (1 - slim), mood="cool", glasses=True, arms="cheer" if t > ba[0] else "hips", bar=lerp(1.0, 0.03, slim), sx=1 - 0.12 * slim, scale=1.05)
        if t > 0.9:
            sticker(g, 520, 420, "disk usage: 3%", MINT, size=36, rot=-4, p=pop(t, 0.9, 0.5))
        rows = [
            (ba[0], 400, "pct", "less storage per recording", CORAL),
            (bb[0], 580, f"{c['hours']} h in {fmt_bytes(c['distilled_bytes'])}", "of meetings, searchable", TEAL),
            (bb[0] + 1.6, 760, f"{c['words']:,}", "words, found in a blink", LILAC),
        ]
        for i, (t0, y, big, small, col) in enumerate(rows):
            p = pop(t, t0, 0.6)
            if p <= 0:
                continue
            x = 1350
            art.shadow(g, x, y + 70, 380, 16, 0.18)
            g.card(x, y, 780, 150, r=34, fill=WHITE, outline=INKC, ow=6, shadow=0, alpha=min(1, p * 1.4), scale=max(0.01, p))
            g.card(x - 376, y, 28, 150, r=14, fill=col, outline=None, ow=0, shadow=0, alpha=min(1, p * 1.4), scale=max(0.01, p))
            val = f"{c['per_recording']['value'] * ease_out(seg(t, t0, t0 + 1.2)):.1f}%" if big == "pct" else big
            g.text(val, x - 30, y - 18, 72, col, "comic", alpha=min(1, p), scale=max(0.01, p))
            g.text(small, x - 30, y + 46, 30, INKC, "ink", alpha=min(1, p), scale=max(0.01, p))
            if i == 0:
                confetti(g, x, y, t - t0 - 0.3, seed=5)
        for j, (txt, col) in enumerate((("$0", SUN), ("100% LOCAL", TEAL), (f"{c['tests']} TESTS PASS", MINT))):
            p = pop(t, bc[0] + 0.1 + j * 0.35, 0.5)
            if p > 0:
                sticker(g, 1030 + j * 300, 930, txt, col, size=34, rot=[-4, 3, -3][j], p=min(1, p))

    def sfx(self, D, B):
        return [(0.1, "boing", 0.5), (B[0][0] + 0.05, "ding", 0.8), (B[0][0] + 0.2, "sparkle", 0.6), (B[1][0] + 0.05, "ding", 0.7), (B[1][0] + 0.2, "sparkle", 0.5),
                (B[1][0] + 1.65, "ding", 0.7), (B[2][0] + 0.1, "pop", 0.6), (B[2][0] + 0.45, "pop", 0.6), (B[2][0] + 0.8, "pop", 0.6)]


# ------------------------------------------------------------------ 8. exit
class Exit(Scene):
    id = "exit"

    def draw(self, g, t, D, B):
        c = self.ctx
        b0 = B[0]
        art.bg_sky(g, t, sunset=ease_out(seg(t, 0, 2.5)))
        ghost(g, 1520, 640, t, mood="content", scale=0.8, badge=True, wave=True)
        dana(g, 1000, 960, t, mood="calm", mug=True, scale=0.9, thumbs=True)
        disky(g, 520, 760, t, fat=0.0, mood="happy", arms="wave", glasses=True, bar=0.03, scale=1.0)
        p = pop(t, b0[0] + 0.1, 0.7)
        gradient_text(g, c["name"], 960, 260, 190, (255, 120, 90), (120, 80, 230), alpha=min(1, p * 1.4), scale=max(0.01, p))
        title(g, c["tagline"], 960, 410, 54, WHITE, INKC, alpha=ease_out(seg(t, b0[0] + 0.9, b0[0] + 1.5)))
        sticker(g, 470, 560, "Disk usage: 3%.  Mood: 100%.", SUN, size=36, rot=-3, p=pop(t, b0[0] + 2.4, 0.5))
        g.text(c["event"], 960, 1040, 28, WHITE, "ink", alpha=ease_out(seg(t, b0[0] + 3.0, b0[0] + 3.6)) * 0.9)
        fade = ease_in(seg(t, D - 0.7, D), 2)
        if fade > 0:
            g.fill_rect(0, 0, 1920, 1080, (0, 0, 0), fade)

    def cam(self, t, D, B):
        c = super().cam(t, D, B)
        c["zoom"] = 1.0 + 0.04 * (t / max(D, 1e-3)) + 0.2 * (1 - ease_out(seg(t, 0, 0.8), 4))
        return c

    def sfx(self, D, B):
        return [(0.0, "whoosh", 0.5), (B[0][0] + 0.15, "boing", 0.6), (B[0][0] + 0.25, "ding", 0.8), (B[0][0] + 2.4, "pop", 0.5)]


SCENES = [Intro, Admin, Distillery, Magic, Search, Leaver, Results, Exit]
