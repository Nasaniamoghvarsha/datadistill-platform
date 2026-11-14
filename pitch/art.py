"""Illustration kit for the storytelling pitch: flat vector characters, props and backgrounds drawn with anti-aliased shapes.

Characters: Disky (an overstuffed hard drive), Dana (the IT admin), the Ghost of a leaver. Props: the distillery apparatus,
files, speech bubbles, signs. Backgrounds: server room, distillery, library, sunny sky, dusk.
All coordinates are logical 1920x1080; g.s scales them. Colours are RGB.
"""
import math

import cv2
import numpy as np

from gfx import clamp, ease_out, lerp, mix, rand, seg

# ------------------------------------------------------------------ palette (friendly, not neon)
NAVY = (28, 38, 78)
INKC = (38, 44, 84)
BLUE = (66, 126, 240)
TEAL = (34, 176, 170)
CORAL = (255, 110, 96)
SUN = (255, 202, 70)
CREAM = (255, 245, 228)
LILAC = (152, 124, 240)
MINT = (112, 214, 162)
STEEL = (118, 146, 186)
STEEL_D = (84, 108, 148)
WHITE = (255, 255, 255)
GREYBG = (222, 228, 240)
WOOD = (176, 122, 80)
COPPER = (214, 130, 84)
SKIN = (246, 200, 160)

SH = 4                                # fixed-point bits for sub-pixel drawing


def _p(g, x, y):
    return (int(round(x * g.s * (1 << SH))), int(round(y * g.s * (1 << SH))))


def ell(g, cx, cy, rx, ry, col, rot=0.0, thick=-1):
    th = -1 if thick < 0 else max(1, int(thick * g.s))
    cv2.ellipse(g.img, _p(g, cx, cy), (int(rx * g.s * (1 << SH)), int(ry * g.s * (1 << SH))), rot, 0, 360, col, th, cv2.LINE_AA, SH)


def arc(g, cx, cy, rx, ry, a0, a1, col, w=6, rot=0.0):
    cv2.ellipse(g.img, _p(g, cx, cy), (int(rx * g.s * (1 << SH)), int(ry * g.s * (1 << SH))), rot, a0, a1, col, max(1, int(w * g.s)), cv2.LINE_AA, SH)


def line(g, p0, p1, col, w=6, caps=True):
    cv2.line(g.img, _p(g, *p0), _p(g, *p1), col, max(1, int(w * g.s)), cv2.LINE_AA, SH)
    if caps:
        for q in (p0, p1):
            ell(g, q[0], q[1], w / 2, w / 2, col)


def poly(g, pts, col):
    P = np.array([_p(g, x, y) for x, y in pts], np.int32)
    cv2.fillPoly(g.img, [P], col, cv2.LINE_AA, SH)


def polyline(g, pts, col, w=6, closed=False):
    P = np.array([_p(g, x, y) for x, y in pts], np.int32)
    cv2.polylines(g.img, [P], closed, col, max(1, int(w * g.s)), cv2.LINE_AA, SH)
    if not closed:
        for q in (pts[0], pts[-1]):
            ell(g, q[0], q[1], w / 2, w / 2, col)


def rr(g, cx, cy, w, h, r, col, rot=0.0, outline=None, ow=0, alpha=1.0):
    g.card(cx, cy, w, h, r=r, fill=col, outline=outline, ow=ow, shadow=0, alpha=alpha, rot=rot)


def shadow(g, cx, cy, rx, ry, alpha=0.18):
    spr = np.zeros((int(ry * 2 * g.s) + 8, int(rx * 2 * g.s) + 8, 4), np.uint8)
    cv2.ellipse(spr, (spr.shape[1] // 2, spr.shape[0] // 2), (int(rx * g.s), int(ry * g.s)), 0, 0, 360, (20, 24, 50, 255), -1, cv2.LINE_AA)
    spr[..., 3] = cv2.GaussianBlur(spr[..., 3], (0, 0), max(2, ry * g.s * 0.35))
    g.blit(spr, cx, cy, "c", alpha)


def bez(p0, p1, p2, n=18):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0], (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1])
            for t in [i / n for i in range(n + 1)]]


# ------------------------------------------------------------------ speech bubble
def bubble(g, cx, cy, text, tail=(0, 80), w=None, size=40, p=1.0, color=WHITE, ink=INKC, kind="comic", maxw=640):
    """A comic speech bubble popping in (p 0..1). tail = offset (dx, dy) of the pointed end from the bubble centre."""
    if p <= 0:
        return
    words, lines, cur = text.split(), [], ""
    for wd in words:
        t_ = (cur + " " + wd).strip()
        if g.text_width(t_, size, kind) <= maxw or not cur:
            cur = t_
        else:
            lines.append(cur)
            cur = wd
    lines.append(cur)
    bw = max(g.text_width(ln, size, kind) for ln in lines) + 70
    bh = len(lines) * size * 1.25 + 50
    k = max(0.01, 1 + 0.12 * math.sin(p * math.pi) if p < 1 else 1.0) * min(1.0, p * 1.6)
    x0, y0 = cx, cy
    tx, ty = cx + tail[0], cy + tail[1]
    # tail
    ang = math.atan2(ty - cy, tx - cx)
    bx, by = cx + math.cos(ang) * min(bw / 2 - 30, 60) * 0, cy + bh / 2 - 6
    poly(g, [(cx - 34 * k, cy + bh / 2 * k - 8), (cx + 34 * k, cy + bh / 2 * k - 8), (cx + (tx - cx) * k, cy + (ty - cy) * k)], ink)
    poly(g, [(cx - 26 * k, cy + bh / 2 * k - 8), (cx + 26 * k, cy + bh / 2 * k - 8), (cx + (tx - cx) * k, cy + (ty - cy) * k - 6)], color)
    g.card(cx, cy, bw, bh, r=36, fill=color, outline=ink, ow=5, shadow=0, scale=k)
    for i, ln in enumerate(lines):
        g.text(ln, cx, cy - (len(lines) - 1) * size * 0.625 + i * size * 1.25, size, ink, kind, alpha=min(1, p * 2), scale=k)


def sticker(g, cx, cy, text, color=SUN, ink=INKC, size=34, rot=-4.0, p=1.0, kind="comic"):
    """A tilted label slapped on the picture."""
    if p <= 0:
        return
    w = g.text_width(text, size, kind) + 50
    k = min(1.0, p * 1.8) * (1 + 0.10 * math.sin(clamp(p) * math.pi))
    g.card(cx, cy, w, size * 1.9, r=14, fill=color, outline=ink, ow=4, shadow=14, scale=k, rot=rot)
    g.text(text, cx, cy, size, ink, kind, scale=k, rot=rot, alpha=min(1, p * 2))


# ------------------------------------------------------------------ props
def video_file(g, cx, cy, label="", w=170, h=120, color=BLUE, rot=0.0, scale=1.0, alpha=1.0, play=True):
    k = scale
    g.card(cx, cy, w, h, r=18, fill=WHITE, outline=INKC, ow=4, shadow=16, alpha=alpha, scale=k, rot=rot)
    if play:
        r_ = h * 0.2 * k
        ell(g, cx - (0 if not rot else 0), cy - h * 0.1 * k, r_, r_, color)
        poly(g, [(cx - r_ * 0.35, cy - h * 0.1 * k - r_ * 0.5), (cx - r_ * 0.35, cy - h * 0.1 * k + r_ * 0.5), (cx + r_ * 0.55, cy - h * 0.1 * k)], WHITE)
    if label:
        g.text(label, cx, cy + h * 0.30 * k, 17, INKC, "mono", alpha=alpha, scale=k, rot=rot)


def clouds(g, T, y0=200, n=5, col=WHITE, speed=14, seed=3):
    r_ = rand(seed)
    for i in range(n):
        x = (r_.random() * 2300 + T * speed * (0.6 + r_.random())) % 2300 - 200
        y = y0 + r_.random() * 260
        s = 0.7 + r_.random() * 0.8
        for dx, dy, rr_ in ((0, 0, 60), (55, -22, 48), (-55, -10, 42), (105, 6, 38), (-100, 10, 34)):
            ell(g, x + dx * s, y + dy * s, rr_ * s, rr_ * s * 0.85, col)


# ------------------------------------------------------------------ Disky, the hard drive
def disky(g, cx, cy, t, fat=0.0, mood="worry", look=(0.0, 0.0), sx=1.0, sy=1.0, rot=0.0, led=None, bar=None, glasses=False, goggles=False,
          sweat=0.0, arms="down", blink=True, scale=1.0, hat=False):
    """cx, cy = centre of the body. fat 0..1 = how stuffed. mood: worry, panic, burp, happy, dead, proud, cool. bar = belly storage meter 0..1."""
    S = scale
    w = (250 + 120 * fat) * sx * S
    h = (310 - 20 * fat) * sy * S
    body = STEEL if fat < 0.95 else mix(STEEL, CORAL, 0.18)
    shadow(g, cx, cy + h / 2 + 18 * S, w * 0.6, 20 * S, 0.22)
    bob = math.sin(t * 5.0) * 4 * S
    cy = cy + bob * (0.3 if mood in ("dead",) else 1)
    # legs
    for sgn in (-1, 1):
        rr(g, cx + sgn * w * 0.24, cy + h / 2 + 8 * S, 70 * S, 40 * S, 18, INKC)
    # arms: angle in degrees below the horizontal, outward from the body (negative lifts the arm)
    pose = {"down": (38, 38), "up": (-70, -70), "wave": (38, -62 + 26 * math.sin(t * 9)), "hips": (12, 12), "point": (38, -32), "cheer": (-60, -60)}.get(arms, (38, 38))
    for sgn, a_ in zip((-1, 1), pose):
        sx_, sy_ = cx + sgn * w / 2, cy + h * 0.05
        th = math.radians(a_)
        L = 95 * S
        ex, ey = sx_ + sgn * math.cos(th) * L, sy_ + math.sin(th) * L
        line(g, (sx_, sy_), (ex, ey), STEEL_D, 26 * S)
        ell(g, ex, ey, 20 * S, 20 * S, WHITE)
    # body: shell, lighter top, side shade
    g.card(cx, cy, w, h, r=int(56 * S), fill=body, outline=INKC, ow=5, shadow=0, rot=rot)
    if not rot:
        g.card(cx, cy - h * 0.40, w * 0.9, h * 0.14, r=int(26 * S), fill=mix(body, WHITE, 0.35), outline=None, ow=0, shadow=0)
    # belly storage meter
    if bar is not None:
        bw_ = w * 0.66
        rr(g, cx, cy + h * 0.30, bw_, 34 * S, 17, mix(INKC, WHITE, 0.15))
        fw = max(18 * S, bw_ * clamp(bar))
        c_ = mix(MINT, CORAL, clamp((bar - 0.5) / 0.5))
        rr(g, cx - bw_ / 2 + fw / 2, cy + h * 0.30, fw, 26 * S, 13, c_)
    # LED
    lc = led or (CORAL if mood in ("worry", "panic", "burp") else MINT)
    on = 1.0 if (mood not in ("worry", "panic") or int(t * 4) % 2 == 0) else 0.35
    ell(g, cx + w * 0.38, cy - h * 0.40, 12 * S, 12 * S, mix(INKC, lc, on))
    # face
    ey = cy - h * 0.13
    ex_ = w * 0.22
    big = {"panic": 1.25, "burp": 1.0, "dead": 1.0}.get(mood, 1.0)
    for sgn in (-1, 1):
        x_ = cx + sgn * ex_
        if mood == "dead":
            line(g, (x_ - 16 * S, ey - 16 * S), (x_ + 16 * S, ey + 16 * S), INKC, 7 * S)
            line(g, (x_ + 16 * S, ey - 16 * S), (x_ - 16 * S, ey + 16 * S), INKC, 7 * S)
            continue
        lid = 1.0
        if blink and (t % 3.2) < 0.12:
            lid = 0.1
        ell(g, x_, ey, 36 * S * big, 36 * S * big * lid, WHITE)
        ell(g, x_, ey, 36 * S * big, 36 * S * big * lid, INKC, thick=4)
        if lid > 0.5:
            px_, py_ = look[0] * 12 * S, look[1] * 10 * S
            ell(g, x_ + px_, ey + py_, 15 * S, 15 * S, INKC)
            ell(g, x_ + px_ - 5 * S, ey + py_ - 5 * S, 4.5 * S, 4.5 * S, WHITE)
    if glasses:                                                     # sunglasses
        for sgn in (-1, 1):
            rr(g, cx + sgn * ex_, ey, 82 * S, 58 * S, 20, (24, 28, 52))
        line(g, (cx - ex_ + 30 * S, ey), (cx + ex_ - 30 * S, ey), (24, 28, 52), 8 * S)
        for sgn in (-1, 1):
            line(g, (cx + sgn * ex_ - 18 * S, ey - 12 * S), (cx + sgn * ex_ - 4 * S, ey - 20 * S), (110, 120, 160), 5 * S)
    if goggles:
        for sgn in (-1, 1):
            ell(g, cx + sgn * ex_, ey, 50 * S, 50 * S, COPPER, thick=10)
            ell(g, cx + sgn * ex_, ey, 44 * S, 44 * S, (255, 255, 255), thick=3)
        line(g, (cx - ex_ + 44 * S, ey), (cx + ex_ - 44 * S, ey), COPPER, 9 * S)
    # brows
    if mood in ("worry", "panic"):
        for sgn in (-1, 1):
            line(g, (cx + sgn * ex_ + sgn * 26 * S, ey - 58 * S), (cx + sgn * ex_ - sgn * 20 * S, ey - 74 * S), INKC, 7 * S)
    # mouth
    my = cy + h * 0.06
    if mood == "worry":
        pts = [(cx - 38 * S + i * 76 * S / 8, my + math.sin(i * 1.6 + t * 12) * 5 * S) for i in range(9)]
        polyline(g, pts, INKC, 7 * S)
    elif mood == "panic":
        ell(g, cx, my + 8 * S, 28 * S, 36 * S, INKC)
        ell(g, cx, my + 24 * S, 16 * S, 14 * S, CORAL)
    elif mood == "burp":
        ell(g, cx, my + 10 * S, 34 * S, 40 * S, INKC)
        ell(g, cx, my + 28 * S, 20 * S, 16 * S, CORAL)
    elif mood in ("happy", "proud", "cool"):
        arc(g, cx, my - 6 * S, 46 * S, 34 * S, 15, 165, INKC, 8 * S)
        if mood == "happy":
            ell(g, cx, my + 26 * S, 22 * S, 12 * S, CORAL)
    elif mood == "dead":
        line(g, (cx - 30 * S, my + 8 * S), (cx + 30 * S, my + 8 * S), INKC, 7 * S)
    # cheeks
    if mood in ("happy", "proud", "cool"):
        for sgn in (-1, 1):
            ell(g, cx + sgn * w * 0.34, my - 2 * S, 20 * S, 12 * S, mix(body, CORAL, 0.55))
    # sweat drops
    if sweat > 0:
        for i in range(3):
            ph = (t * 1.6 + i * 0.37) % 1.0
            sx2 = cx + (-1) ** i * (w * 0.5 + 6 * S) + i * 6
            sy2 = cy - h * 0.30 + ph * 140 * S
            poly(g, [(sx2, sy2 - 24 * S), (sx2 - 12 * S, sy2 + 4 * S), (sx2 + 12 * S, sy2 + 4 * S)], (130, 200, 255))
            ell(g, sx2, sy2 + 4 * S, 12 * S, 12 * S, (130, 200, 255))
    if hat:                                                         # tiny lab cap
        rr(g, cx, cy - h / 2 - 8 * S, w * 0.7, 40 * S, 18, WHITE, outline=INKC, ow=4)


# ------------------------------------------------------------------ Dana, the admin
def dana(g, cx, cy, t, mood="panic", walk=0.0, mug=True, scale=1.0, flip=1, thumbs=False, rot=0.0):
    """cx, cy = feet position. walk > 0 animates the legs. mood: panic, calm, proud."""
    S = scale
    f = flip
    sw = math.sin(t * 9) * walk
    shadow(g, cx, cy + 4, 90 * S, 16 * S, 0.2)
    # legs
    for sgn in (-1, 1):
        a = sw * sgn
        line(g, (cx + sgn * 26 * S * f, cy - 120 * S), (cx + sgn * 26 * S * f + a * 30 * S, cy - 8 * S), (46, 60, 110), 38 * S)
        rr(g, cx + sgn * 26 * S * f + a * 30 * S + 10 * S * f, cy - 2 * S, 66 * S, 26 * S, 12, WHITE, outline=INKC, ow=3)
    # torso (hoodie)
    bounce = abs(sw) * 6 * S
    ty = cy - 250 * S - bounce
    rr(g, cx, ty, 170 * S, 230 * S, 50, CORAL, rot=rot)
    rr(g, cx, ty + 70 * S, 90 * S, 50 * S, 18, mix(CORAL, INKC, 0.25))
    # arms
    ax, ay = cx - 88 * S * f, ty - 60 * S
    if mood == "panic":
        line(g, (ax, ay), (ax - 40 * S * f, ay - 120 * S + math.sin(t * 14) * 8 * S), CORAL, 34 * S)
        ell(g, ax - 40 * S * f, ay - 128 * S + math.sin(t * 14) * 8 * S, 24 * S, 24 * S, SKIN)
    else:
        line(g, (ax, ay), (ax - 30 * S * f, ay + 100 * S), CORAL, 34 * S)
        ell(g, ax - 30 * S * f, ay + 104 * S, 24 * S, 24 * S, SKIN)
    bx, by = cx + 88 * S * f, ty - 60 * S
    if thumbs:
        line(g, (bx, by), (bx + 60 * S * f, by - 60 * S), CORAL, 34 * S)
        ell(g, bx + 62 * S * f, by - 70 * S, 26 * S, 26 * S, SKIN)
        rr(g, bx + 66 * S * f, by - 108 * S, 16 * S, 34 * S, 8, SKIN)
    else:
        line(g, (bx, by), (bx + 50 * S * f, by + 60 * S), CORAL, 34 * S)
        ell(g, bx + 50 * S * f, by + 66 * S, 24 * S, 24 * S, SKIN)
        if mug:
            rr(g, bx + 50 * S * f, by + 30 * S, 54 * S, 62 * S, 10, WHITE, outline=INKC, ow=3)
            arc(g, bx + 80 * S * f, by + 36 * S, 16 * S, 18 * S, -90, 90, INKC, 5 * S)
            for i in range(3):
                ph = (t * 0.9 + i * 0.33) % 1.0
                ell(g, bx + 50 * S * f + math.sin(ph * 6 + i) * 8 * S, by - 14 * S - ph * 60 * S, 8 * S, 8 * S, mix(WHITE, GREYBG, ph))
    # head
    hy = ty - 190 * S - bounce
    ell(g, cx, hy, 66 * S, 72 * S, SKIN)
    ell(g, cx, hy - 34 * S, 70 * S, 46 * S, (60, 40, 40))             # hair
    ell(g, cx - 44 * S * f, hy - 6 * S, 22 * S, 38 * S, (60, 40, 40))
    for sgn in (-1, 1):
        ell(g, cx + sgn * 26 * S, hy + 2 * S, 22 * S, 22 * S, WHITE)
        ell(g, cx + sgn * 26 * S, hy + 2 * S, 22 * S, 22 * S, INKC, thick=4)
        ell(g, cx + sgn * 26 * S + 3 * S * f, hy + 2 * S, 7 * S, 7 * S, INKC)
    line(g, (cx - 4 * S, hy + 2 * S), (cx + 4 * S, hy + 2 * S), INKC, 4 * S)
    if mood == "panic":
        ell(g, cx, hy + 42 * S, 18 * S, 22 * S, INKC)
        for sgn in (-1, 1):
            line(g, (cx + sgn * 40 * S, hy - 28 * S), (cx + sgn * 14 * S, hy - 20 * S), INKC, 5 * S)
    else:
        arc(g, cx, hy + 30 * S, 26 * S, 18 * S, 20, 160, INKC, 5 * S)


# ------------------------------------------------------------------ the ghost of a leaver
def ghost(g, cx, cy, t, mood="sad", scale=1.0, alpha=1.0, badge=True, wave=True):
    S = scale
    bob = math.sin(t * 2.4) * 16 * S
    cy = cy + bob
    col = (246, 248, 255)
    over = g.img.copy() if alpha < 1.0 else None
    # body
    ell(g, cx, cy - 40 * S, 100 * S, 110 * S, col)
    pts = [(cx - 100 * S, cy - 40 * S)]
    n = 6
    for i in range(n + 1):
        x_ = cx - 100 * S + i * 200 * S / n
        y_ = cy + 100 * S + (22 * S if i % 2 == 0 else -6 * S) + math.sin(t * 5 + i) * 6 * S
        pts.append((x_, y_))
    pts.append((cx + 100 * S, cy - 40 * S))
    poly(g, pts, col)
    ell(g, cx, cy - 40 * S, 100 * S, 110 * S, INKC, thick=0) if False else None
    # arms
    w_ = math.sin(t * 8) * 14 * S if wave else 0
    line(g, (cx - 92 * S, cy + 10 * S), (cx - 150 * S, cy - 70 * S + w_), col, 30 * S)
    line(g, (cx + 92 * S, cy + 10 * S), (cx + 130 * S, cy + 60 * S), col, 30 * S)
    # face
    if mood == "sad":
        for sgn in (-1, 1):
            ell(g, cx + sgn * 38 * S, cy - 50 * S, 13 * S, 17 * S, INKC)
        arc(g, cx, cy - 4 * S, 24 * S, 16 * S, 200, 340, INKC, 6 * S)
        poly(g, [(cx + 64 * S, cy - 36 * S), (cx + 56 * S, cy - 6 * S), (cx + 74 * S, cy - 6 * S)], (130, 200, 255))
    else:
        for sgn in (-1, 1):
            arc(g, cx + sgn * 38 * S, cy - 50 * S, 15 * S, 11 * S, 200, 340, INKC, 6 * S)
        arc(g, cx, cy - 20 * S, 26 * S, 18 * S, 20, 160, INKC, 6 * S)
        for sgn in (-1, 1):
            ell(g, cx + sgn * 62 * S, cy - 26 * S, 16 * S, 10 * S, (255, 190, 200))
    if badge:                                                       # lanyard badge
        line(g, (cx - 78 * S, cy - 4 * S), (cx, cy + 36 * S), BLUE, 6 * S)
        line(g, (cx + 78 * S, cy - 4 * S), (cx, cy + 36 * S), BLUE, 6 * S)
        rr(g, cx, cy + 66 * S, 70 * S, 52 * S, 8, WHITE, outline=INKC, ow=3)
        line(g, (cx - 22 * S, cy + 60 * S), (cx + 22 * S, cy + 60 * S), STEEL, 5 * S)
        line(g, (cx - 22 * S, cy + 74 * S), (cx + 10 * S, cy + 74 * S), STEEL, 5 * S)
    if over is not None:
        g.img = cv2.addWeighted(g.img, alpha, over, 1 - alpha, 0)


# ------------------------------------------------------------------ the distillery apparatus
def apparatus(g, cx, cy, t, heat=1.0, fill=0.6, scale=1.0, drip=1.0, labels=True):
    """Flask on a burner (centre cx, bottom cy), a pipe to a condenser, and three bottles. Returns the three bottle positions."""
    S = scale
    # burner + flame
    rr(g, cx, cy - 14 * S, 220 * S, 28 * S, 12, STEEL_D)
    for i in range(3):
        fl = (0.7 + 0.3 * math.sin(t * 13 + i * 2)) * heat
        x_ = cx + (i - 1) * 46 * S
        poly(g, [(x_ - 20 * S, cy - 26 * S), (x_, cy - 26 * S - 70 * S * fl), (x_ + 20 * S, cy - 26 * S)], SUN)
        poly(g, [(x_ - 10 * S, cy - 26 * S), (x_, cy - 26 * S - 38 * S * fl), (x_ + 10 * S, cy - 26 * S)], CORAL)
    # flask
    fy = cy - 160 * S
    ell(g, cx, fy, 150 * S, 140 * S, (230, 244, 255))
    # liquid
    liq = mix(TEAL, SUN, 0.25 + 0.25 * math.sin(t))
    mask = np.zeros((g.H, g.W), np.uint8)
    cv2.ellipse(mask, (int(cx * g.s), int(fy * g.s)), (int(142 * S * g.s), int(132 * S * g.s)), 0, 0, 360, 255, -1, cv2.LINE_AA)
    top = fy + 132 * S - 264 * S * fill
    yy = np.arange(g.H)[:, None]
    xx = np.arange(g.W)[None, :]
    wave = np.sin((xx / g.s) * 0.045 + t * 3.0) * 7 * S
    liqmask = ((yy / g.s) > (top + wave)) & (mask > 0)
    g.img[liqmask] = (0.88 * np.array(liq) + 0.12 * g.img[liqmask]).astype(np.uint8)
    ell(g, cx, fy, 150 * S, 140 * S, INKC, thick=6)
    # neck
    rr(g, cx, fy - 190 * S, 90 * S, 150 * S, 16, (230, 244, 255), outline=INKC, ow=6)
    rr(g, cx, fy - 276 * S, 110 * S, 40 * S, 14, COPPER, outline=INKC, ow=4)
    # bubbles
    r_ = rand(4)
    for i in range(9):
        ph = (t * (0.5 + 0.5 * r_.random()) * heat + r_.random()) % 1.0
        bx_ = cx + (r_.random() - 0.5) * 200 * S
        by_ = fy + 110 * S - ph * 230 * S * fill
        rad = (6 + 10 * r_.random()) * S
        ell(g, bx_, by_, rad, rad, (255, 255, 255), thick=3)
    # pipe up and over to the condenser
    pipe = [(cx, fy - 296 * S), (cx, fy - 360 * S), (cx + 160 * S, fy - 380 * S), (cx + 380 * S, fy - 300 * S)]
    polyline(g, pipe, COPPER, 26 * S)
    polyline(g, pipe, (240, 180, 140), 10 * S)
    # condenser
    cxx, cyy = cx + 520 * S, fy - 240 * S
    rr(g, cxx, cyy, 300 * S, 90 * S, 30, (200, 232, 250), rot=-28, outline=INKC, ow=5)
    for k in range(5):
        a = math.radians(-28)
        px_, py_ = cxx + (k - 2) * 52 * S * math.cos(a), cyy + (k - 2) * 52 * S * math.sin(a)
        line(g, (px_, py_ - 34 * S), (px_, py_ + 34 * S), TEAL, 8 * S)
    # drip tip
    tipx, tipy = cxx + 130 * S, cyy + 120 * S
    bottles = []
    for i, (lab, col) in enumerate((("TRANSCRIPT", BLUE), ("SLIDES", CORAL), ("VOICES", LILAC))):
        bx2 = cxx - 120 * S + i * 150 * S
        by2 = cy - 20 * S
        bottles.append((bx2, by2, lab, col))
    return {"tip": (tipx, tipy), "bottles": bottles, "flask": (cx, fy), "top": (cx, fy - 296 * S), "cond": (cxx, cyy)}


def bottle(g, cx, cy, label, col, level=0.0, scale=1.0, wobble=0.0):
    S = scale
    rr(g, cx, cy - 60 * S, 100 * S, 120 * S, 24, (232, 244, 255), outline=INKC, ow=5, rot=wobble)
    rr(g, cx, cy - 150 * S, 44 * S, 70 * S, 12, (232, 244, 255), outline=INKC, ow=5, rot=wobble)
    rr(g, cx, cy - 190 * S, 56 * S, 24 * S, 8, COPPER, outline=INKC, ow=4, rot=wobble)
    if level > 0:
        h = 104 * S * clamp(level)
        rr(g, cx, cy - 8 * S - h / 2 + 0 * S, 86 * S, max(6, h), 16, col)
    g.text(label, cx, cy + 36 * S, 22 * S, INKC, "comic", scale=1.0)


def drops(g, tip, t, col_list, rate=1.0, fall=330):
    """Droplets falling from the condenser tip; returns nothing. Drops are small teardrops in the given colours."""
    for i, c in enumerate(col_list):
        ph = (t * rate * 0.9 + i * 0.33) % 1.0
        x_, y_ = tip[0], tip[1] + ph * fall
        poly(g, [(x_, y_ - 22), (x_ - 11, y_ + 4), (x_ + 11, y_ + 4)], c)
        ell(g, x_, y_ + 4, 11, 11, c)


def steam(g, x, y, t, label="GB", n=6, rise=520, col=WHITE):
    """Puffs rising and fading, each carrying a little 'GB' (the bytes that evaporate)."""
    for i in range(n):
        ph = (t * 0.42 + i / n) % 1.0
        px_ = x + math.sin(ph * 5 + i * 1.7) * 60 + i * 12
        py_ = y - ph * rise
        r_ = 34 + 38 * ph
        a = (1 - ph) * clamp(ph * 8)
        g.disc(px_, py_, r_, col, alpha=min(1, a * 1.1))
        g.text(label, px_, py_, 24 + 16 * ph, CORAL, "comic", alpha=min(1, a * 1.2))


# ------------------------------------------------------------------ backgrounds
def _grad(g, top, bottom, y0=0, y1=None):
    y1 = g.H if y1 is None else y1
    t = np.linspace(0, 1, y1 - y0)[:, None, None]
    g.img[y0:y1] = (np.array(top) + (np.array(bottom) - np.array(top)) * t).astype(np.uint8)


def bg_server(g, T, alarm=0.0, px=0.0):
    """A server room that is, frankly, full: tall racks with blinking lights, a red alarm lamp, cables."""
    _grad(g, (46, 74, 112), (24, 38, 70))
    # back wall tiles
    for x in range(0, 1920, 120):
        line(g, (x - px * 0.2 % 120, 0), (x - px * 0.2 % 120, 760), (60, 90, 130), 2, caps=False)
    # floor
    rr(g, 960, 960, 2100, 280, 0, (30, 44, 78))
    for x in range(-200, 2200, 220):
        line(g, (x - px * 0.6 % 220, 830), (x - px * 0.6 % 220 + (x - 960) * 0.3, 1100), (42, 58, 98), 3, caps=False)
    r_ = rand(11)
    # racks
    for i in range(8):
        x = 120 + i * 250 - (px * 0.4) % 250 if False else 120 + i * 250
        if 520 < x < 1260:                                                  # leave the middle clear for the characters
            continue
        h = 560 + (i % 3) * 60
        rr(g, x, 800 - h / 2, 170, h, 14, (38, 52, 92), outline=(78, 104, 160), ow=3)
        for j in range(int(h / 56)):
            yj = 800 - h + 40 + j * 56
            rr(g, x, yj, 142, 38, 6, (26, 36, 68))
            for k in range(5):
                on = (math.sin(T * (1.5 + r_.random() * 3) + r_.random() * 9) > 0.1)
                col = (CORAL if alarm > 0.5 and (j + k + i) % 4 == 0 else (MINT if (j + k) % 3 else SUN))
                ell(g, x - 54 + k * 18, yj, 5, 5, mix((40, 54, 90), col, 1.0 if on else 0.15))
    # cables
    polyline(g, bez((0, 300), (300, 520), (560, 380)), (20, 28, 56), 9)
    polyline(g, bez((1920, 260), (1600, 500), (1300, 340)), (20, 28, 56), 9)
    # alarm lamp
    ax, ay = 960, 150
    rr(g, ax, ay + 24, 90, 30, 8, (70, 80, 110))
    blink = 0.5 + 0.5 * math.sin(T * 9) if alarm > 0 else 0.0
    ell(g, ax, ay, 36, 36, mix((90, 50, 60), CORAL, blink))
    if alarm > 0:
        g.glow_disc(ax, ay, 100, CORAL, 0.35 * blink * alarm)


def bg_lab(g, T, px=0.0):
    """A warm distillery: wooden shelves of bottles, copper pipes, a round window, a hanging lamp."""
    _grad(g, (255, 236, 204), (240, 196, 150))
    # wall planks
    for x in range(0, 1920, 160):
        line(g, (x, 0), (x, 800), (232, 186, 140), 3, caps=False)
    # window
    ell(g, 330, 330, 150, 150, (150, 210, 250))
    clouds(g, T, 270, 2, WHITE, 6)
    ell(g, 330, 330, 150, 150, WOOD, thick=16)
    line(g, (330, 180), (330, 480), WOOD, 10, caps=False)
    line(g, (180, 330), (480, 330), WOOD, 10, caps=False)
    # shelves with bottles
    r_ = rand(21)
    for sy in (250, 430):
        rr(g, 1500, sy + 60, 760, 22, 6, WOOD)
        for i in range(10):
            x = 1180 + i * 70
            col = [CORAL, TEAL, SUN, LILAC, BLUE][int(r_.random() * 5)]
            h = 70 + r_.random() * 40
            rr(g, x, sy + 50 - h / 2, 38, h, 12, col)
            rr(g, x, sy + 50 - h - 12, 16, 24, 5, col)
    # pipes
    polyline(g, [(0, 120), (700, 120), (700, 60), (1920, 60)], COPPER, 24)
    polyline(g, [(0, 120), (700, 120), (700, 60), (1920, 60)], (240, 180, 130), 8)
    # floor
    rr(g, 960, 960, 2100, 280, 0, (190, 138, 96))
    for x in range(-100, 2100, 200):
        line(g, (x, 830), (x, 1100), (170, 120, 82), 4, caps=False)
    # lamp
    line(g, (960, 0), (960, 120), INKC, 6, caps=False)
    poly(g, [(900, 190), (1020, 190), (990, 120), (930, 120)], SUN)
    g.glow_disc(960, 200, 320, SUN, 0.26)


def bg_library(g, T, px=0.0):
    """A cosy library: tall shelves of coloured books, a tall window, floating dust."""
    _grad(g, (60, 52, 110), (34, 30, 76))
    r_ = rand(31)
    for col_i in range(6):
        x0 = 40 + col_i * 330
        rr(g, x0 + 130, 470, 260, 880, 10, (86, 60, 52))
        for row in range(5):
            y0 = 120 + row * 168
            rr(g, x0 + 130, y0 + 150, 252, 14, 4, (120, 84, 64))
            x = x0 + 14
            while x < x0 + 244:
                w = 18 + r_.random() * 20
                h = 90 + r_.random() * 56
                c = [CORAL, TEAL, SUN, LILAC, BLUE, MINT][int(r_.random() * 6)]
                rr(g, x + w / 2, y0 + 150 - h / 2, w, h, 4, mix(c, (60, 40, 40), 0.25))
                x += w + 3
    rr(g, 960, 960, 2100, 280, 0, (44, 36, 70))
    # warm light cone
    g.glow_disc(960, 380, 520, SUN, 0.16)
    for i in range(30):
        x = (r_.random() * 1920 + T * (6 + 14 * r_.random())) % 1920
        y = (r_.random() * 1000 - T * (4 + 10 * r_.random())) % 1000
        g.disc(x, y, 2 + 3 * r_.random(), WHITE, alpha=0.35 + 0.3 * math.sin(T * 2 + i))


def bg_sky(g, T, sunset=0.0):
    """Bright day (or dusk for the ending): sky, sun, drifting clouds, rolling hills."""
    top = mix((110, 188, 255), (255, 150, 120), sunset)
    bottom = mix((220, 244, 255), (255, 214, 150), sunset)
    _grad(g, top, bottom)
    sx, sy = 1480, 250 + 60 * sunset
    ell(g, sx, sy, 110, 110, mix(SUN, (255, 140, 90), sunset))
    g.glow_disc(sx, sy, 300, SUN, 0.35)
    clouds(g, T, 120, 6, WHITE, 14)
    # hills
    pts = [(x, 800 + 50 * math.sin(x * 0.0036 + 1.0) + 30 * math.sin(x * 0.011)) for x in range(-20, 1960, 20)]
    poly(g, pts + [(1960, 1100), (-20, 1100)], mix((120, 206, 150), (120, 150, 130), sunset))
    pts = [(x, 900 + 40 * math.sin(x * 0.005 + 3.0) + 20 * math.sin(x * 0.013)) for x in range(-20, 1960, 20)]
    poly(g, pts + [(1960, 1100), (-20, 1100)], mix((86, 180, 124), (90, 120, 110), sunset))
