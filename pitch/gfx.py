"""Small 2D drawing toolkit for the pitch video: sprites with alpha, soft glows, rounded cards, text, easing, camera.

All coordinates are logical 1920x1080; Gfx scales them to the real frame size (so a 1280x720 preview looks the same).
Colours are RGB tuples. The frame is a uint8 RGB numpy array.
"""
import math
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W0, H0 = 1920, 1080
FONT_DIR = "C:/Windows/Fonts/"

# ------------------------------------------------------------------ palette
BG0 = (8, 12, 28)
BG1 = (14, 22, 52)
INK = (240, 244, 255)
MUTED = (150, 164, 196)
BLUE = (46, 128, 255)
CYAN = (40, 220, 235)
VIOLET = (140, 100, 255)
GREEN = (52, 215, 140)
AMBER = (255, 184, 64)
RED = (255, 84, 96)
PINK = (255, 90, 170)
CARD = (20, 30, 62)
CARD2 = (28, 42, 84)
LINE = (58, 78, 130)


# ------------------------------------------------------------------ easing
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def lerp(a, b, t):
    return a + (b - a) * t


def ease_out(t, p=3):
    t = clamp(t)
    return 1 - (1 - t) ** p


def ease_in(t, p=3):
    return clamp(t) ** p


def ease_io(t):
    t = clamp(t)
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_back(t, s=1.70158):
    t = clamp(t)
    return 1 + (s + 1) * (t - 1) ** 3 + s * (t - 1) ** 2


def spring(t, damp=7.0, freq=11.0):
    """0 -> 1 with an overshoot that settles (for pops)."""
    t = clamp(t, 0, 4)
    return 1 - math.exp(-damp * t) * math.cos(freq * t)


def seg(t, a, b):
    """progress 0..1 of t inside [a, b]"""
    return clamp((t - a) / max(1e-9, b - a))


def mix(c0, c1, t):
    return tuple(int(lerp(a, b, clamp(t))) for a, b in zip(c0, c1))


def rand(seed):
    return np.random.default_rng(seed)


# ------------------------------------------------------------------ fonts
@lru_cache(maxsize=256)
def font(size, kind="bold"):
    size = max(6, int(round(size)))
    if kind in ("bold", "semi", "light", "cond"):
        f = ImageFont.truetype(FONT_DIR + "bahnschrift.ttf", size)
        w = {"bold": 700, "semi": 600, "light": 330, "cond": 650}[kind]
        f.set_variation_by_axes([w, 75 if kind == "cond" else 100])
        return f
    if kind == "body":
        return ImageFont.truetype(FONT_DIR + "segoeui.ttf", size)
    if kind == "bodyb":
        return ImageFont.truetype(FONT_DIR + "segoeuib.ttf", size)
    if kind == "comic":
        return ImageFont.truetype(FONT_DIR + "comicbd.ttf", size)
    if kind == "ink":
        return ImageFont.truetype(FONT_DIR + "Inkfree.ttf", size)
    if kind == "mono":
        return ImageFont.truetype(FONT_DIR + "consola.ttf", size)
    raise ValueError(kind)


# ------------------------------------------------------------------ sprites (RGBA uint8)
@lru_cache(maxsize=1024)
def text_sprite(txt, size, color, kind="bold", spacing=0, stroke=0, stroke_color=(0, 0, 0)):
    f = font(size, kind)
    pad = int(size * 0.25) + stroke
    if spacing:
        widths = [f.getlength(c) + spacing for c in txt]
        w = int(sum(widths)) + 2 * pad
    else:
        w = int(f.getlength(txt)) + 2 * pad
    asc, desc = f.getmetrics()
    h = asc + desc + 2 * pad
    im = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if spacing:
        x = pad
        for c, cw in zip(txt, widths):
            d.text((x, pad), c, font=f, fill=color + (255,), stroke_width=stroke, stroke_fill=stroke_color + (255,))
            x += cw
    else:
        d.text((pad, pad), txt, font=f, fill=color + (255,), stroke_width=stroke, stroke_fill=stroke_color + (255,))
    return np.asarray(im)


@lru_cache(maxsize=512)
def rrect_sprite(w, h, r, fill, outline=None, ow=0, grad=None, shadow=0):
    """Anti-aliased rounded rectangle (4x supersampled). grad = (top_color, bottom_color). shadow = blur px (adds room around)."""
    ss = 3
    w, h = max(2, int(w)), max(2, int(h))
    r = max(0, min(int(r), w // 2, h // 2))             # a shape that is still growing from nothing must not break the drawing
    m = int(shadow * 2.2) if shadow else 0
    W, H = w + 2 * m, h + 2 * m
    big = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    box = [m * ss, m * ss, (m + w) * ss - 1, (m + h) * ss - 1]
    if grad:
        gradimg = Image.new("RGBA", (w * ss, h * ss))
        arr = np.zeros((h * ss, w * ss, 4), np.uint8)
        t = np.linspace(0, 1, h * ss)[:, None, None]
        c0, c1 = np.array(grad[0]), np.array(grad[1])
        arr[..., :3] = (c0 + (c1 - c0) * t).astype(np.uint8)
        arr[..., 3] = 255
        gradimg = Image.fromarray(arr)
        mask = Image.new("L", (w * ss, h * ss), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius=r * ss, fill=255)
        big.paste(gradimg, (m * ss, m * ss), mask)
    else:
        d.rounded_rectangle(box, radius=r * ss, fill=fill + (255,) if fill else None)
    if outline and ow:
        d.rounded_rectangle(box, radius=r * ss, outline=outline + (255,), width=ow * ss)
    im = big.resize((W, H), Image.LANCZOS)
    arr = np.asarray(im).copy()
    if shadow:
        a = np.zeros((H, W), np.uint8)
        a[m + shadow // 2: m + h + shadow // 2, m: m + w] = 150
        a = cv2.GaussianBlur(a, (0, 0), shadow * 0.55)
        sh = np.zeros((H, W, 4), np.uint8)
        sh[..., 3] = a
        # card over shadow
        al = arr[..., 3:4].astype(np.float32) / 255
        out = sh.astype(np.float32)
        out[..., :3] = out[..., :3] * (1 - al) + arr[..., :3] * al
        out[..., 3] = np.maximum(out[..., 3], arr[..., 3])
        arr = out.astype(np.uint8)
    return arr


@lru_cache(maxsize=256)
def disc_sprite(r, color, ring=0, ring_color=None):
    ss = 4
    S = 2 * r + 4
    big = Image.new("RGBA", (S * ss, S * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    c = S * ss / 2
    if color:
        d.ellipse([c - r * ss, c - r * ss, c + r * ss, c + r * ss], fill=color + (255,))
    if ring:
        d.ellipse([c - r * ss, c - r * ss, c + r * ss, c + r * ss], outline=(ring_color or color) + (255,), width=ring * ss)
    return np.asarray(big.resize((S, S), Image.LANCZOS))


# ------------------------------------------------------------------ the canvas
class Gfx:
    def __init__(self, W, H):
        self.W, self.H = W, H
        self.s = W / W0
        self.img = np.zeros((H, W, 3), np.uint8)

    # -- coordinates
    def S(self, v):
        return int(round(v * self.s))

    # -- compositing of an RGBA sprite at (x, y) pixel position (top-left), alpha multiplier
    def _paste(self, spr, x, y, alpha=1.0, additive=False):
        h, w = spr.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.W, x + w), min(self.H, y + h)
        if x1 <= x0 or y1 <= y0 or alpha <= 0:
            return
        s = spr[y0 - y: y1 - y, x0 - x: x1 - x]
        a = (s[..., 3:4].astype(np.float32) / 255.0) * alpha
        roi = self.img[y0:y1, x0:x1].astype(np.float32)
        if additive:
            roi = roi + s[..., :3].astype(np.float32) * a
        else:
            roi = roi * (1 - a) + s[..., :3].astype(np.float32) * a
        self.img[y0:y1, x0:x1] = np.clip(roi, 0, 255).astype(np.uint8)

    def blit(self, spr, cx, cy, anchor="c", alpha=1.0, scale=1.0, rot=0.0, additive=False, native=False):
        """Draw sprite so its anchor is at logical (cx, cy). Sprites made at screen size (native=True) are not rescaled by self.s."""
        k = scale * (1.0 if native else 1.0)
        if k != 1.0 or rot:
            h, w = spr.shape[:2]
            nw, nh = max(1, int(w * k)), max(1, int(h * k))
            if rot:
                M = cv2.getRotationMatrix2D((w / 2, h / 2), rot, k)
                diag = int(math.hypot(w, h) * k) + 4
                M[0, 2] += (diag - w) / 2
                M[1, 2] += (diag - h) / 2
                spr = cv2.warpAffine(spr, M, (diag, diag), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
            else:
                spr = cv2.resize(spr, (nw, nh), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR)
        h, w = spr.shape[:2]
        px, py = self.S(cx), self.S(cy)
        ax = {"c": 0.5, "l": 0.0, "r": 1.0}[anchor[0] if anchor[0] in "clr" else "c"]
        ay = {"c": 0.5, "t": 0.0, "b": 1.0}[anchor[1] if len(anchor) > 1 else "c"]
        self._paste(spr, int(px - w * ax), int(py - h * ay), alpha, additive)

    # -- text
    def text(self, txt, cx, cy, size, color=INK, kind="bold", anchor="c", alpha=1.0, scale=1.0, rot=0.0, spacing=0, glow=0.0, stroke=0):
        spr = text_sprite(txt, self.S(size), color, kind, self.S(spacing) if spacing else 0, stroke)
        if glow:
            self.glow_sprite(spr, cx, cy, anchor, color, glow, alpha, scale)
        self.blit(spr, cx, cy, anchor, alpha, scale, rot)

    def text_width(self, txt, size, kind="bold", spacing=0):
        return text_sprite(txt, self.S(size), INK, kind, self.S(spacing) if spacing else 0).shape[1] / self.s

    def glow_sprite(self, spr, cx, cy, anchor, color, strength, alpha=1.0, scale=1.0):
        pad = int(spr.shape[0] * 0.9)
        a = np.pad(spr[..., 3], pad)
        blur = cv2.GaussianBlur(a, (0, 0), max(2, spr.shape[0] * 0.18))
        g = np.zeros((a.shape[0], a.shape[1], 4), np.uint8)
        g[..., :3] = color
        g[..., 3] = np.clip(blur.astype(np.float32) * strength, 0, 255).astype(np.uint8)
        self.blit(g, cx, cy, anchor, alpha, scale, additive=True)

    # -- shapes
    def card(self, cx, cy, w, h, r=28, fill=CARD, outline=LINE, ow=2, grad=None, shadow=34, alpha=1.0, scale=1.0, anchor="c", rot=0.0):
        spr = rrect_sprite(self.S(w), self.S(h), self.S(r), fill, outline, max(1, self.S(ow)) if ow else 0, grad, self.S(shadow) if shadow else 0)
        self.blit(spr, cx, cy, anchor, alpha, scale, rot)

    def disc(self, cx, cy, r, color, alpha=1.0, ring=0, ring_color=None, scale=1.0):
        spr = disc_sprite(self.S(r), color, self.S(ring) if ring else 0, ring_color)
        self.blit(spr, cx, cy, "c", alpha, scale)

    def glow_disc(self, cx, cy, r, color, strength=0.6):
        S = self.S(r * 3)
        yy, xx = np.mgrid[-S:S + 1, -S:S + 1]
        d = np.sqrt(xx ** 2 + yy ** 2) / S
        a = np.clip(1 - d, 0, 1) ** 2.2
        spr = np.zeros((2 * S + 1, 2 * S + 1, 4), np.uint8)
        spr[..., :3] = color
        spr[..., 3] = (a * 255 * strength).astype(np.uint8)
        self.blit(spr, cx, cy, "c", 1.0, 1.0, additive=True)

    def line(self, p0, p1, color=LINE, width=3, alpha=1.0):
        x0, y0, x1, y1 = [self.S(v) for v in (*p0, *p1)]
        pad = self.S(width) + 4
        bx0, by0 = max(0, min(x0, x1) - pad), max(0, min(y0, y1) - pad)
        bx1, by1 = min(self.W, max(x0, x1) + pad), min(self.H, max(y0, y1) + pad)
        if bx1 <= bx0 or by1 <= by0:
            return
        roi = self.img[by0:by1, bx0:bx1]
        over = roi.copy()
        cv2.line(over, (x0 - bx0, y0 - by0), (x1 - bx0, y1 - by0), color, max(1, self.S(width)), cv2.LINE_AA)
        self.img[by0:by1, bx0:bx1] = cv2.addWeighted(over, alpha, roi, 1 - alpha, 0)

    def arc(self, cx, cy, r, a0, a1, color, width=14, alpha=1.0):
        """Ring segment (degrees, 0 = 3 o'clock, clockwise) drawn smoothly via supersampled polyline."""
        n = max(6, int(abs(a1 - a0) / 3))
        pts = [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
        P = np.array([[self.S(x), self.S(y)] for x, y in pts], np.int32)
        over = self.img.copy()
        cv2.polylines(over, [P], False, color, max(1, self.S(width)), cv2.LINE_AA)
        # round caps
        for p in (P[0], P[-1]):
            cv2.circle(over, tuple(int(v) for v in p), max(1, self.S(width) // 2), color, -1, cv2.LINE_AA)
        self.img[:] = cv2.addWeighted(over, alpha, self.img, 1 - alpha, 0)

    def poly(self, pts, color, alpha=1.0):
        P = np.array([[self.S(x), self.S(y)] for x, y in pts], np.int32)
        over = self.img.copy()
        cv2.fillPoly(over, [P], color, cv2.LINE_AA)
        self.img[:] = cv2.addWeighted(over, alpha, self.img, 1 - alpha, 0)

    def bar(self, x, y, w, h, frac, color, track=CARD2, r=None):
        r = r if r is not None else h / 2
        self.card(x + w / 2, y + h / 2, w, h, r=r, fill=track, outline=None, ow=0, shadow=0)
        if frac > 0:
            ww = max(h, w * frac)
            self.card(x + ww / 2, y + h / 2, ww, h, r=r, fill=color, outline=None, ow=0, shadow=0)

    def fill_rect(self, x, y, w, h, color, alpha=1.0):
        x0, y0, x1, y1 = self.S(x), self.S(y), self.S(x + w), self.S(y + h)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(self.W, x1), min(self.H, y1)
        if x1 <= x0 or y1 <= y0:
            return
        roi = self.img[y0:y1, x0:x1].astype(np.float32)
        self.img[y0:y1, x0:x1] = np.clip(roi * (1 - alpha) + np.array(color, np.float32) * alpha, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ icons (drawn once into sprites, reused)
@lru_cache(maxsize=128)
def icon_sprite(name, size, color, bg=None):
    ss = 4
    S = size * ss
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = color + (255,)
    u = S / 100.0
    lw = int(7 * u)
    if name == "play":
        d.rounded_rectangle([8 * u, 18 * u, 92 * u, 82 * u], radius=14 * u, outline=c, width=lw)
        d.polygon([(42 * u, 36 * u), (42 * u, 64 * u), (66 * u, 50 * u)], fill=c)
    elif name == "doc":
        d.rounded_rectangle([20 * u, 8 * u, 80 * u, 92 * u], radius=9 * u, outline=c, width=lw)
        for i, y in enumerate((32, 48, 64)):
            d.line([(32 * u, y * u), ((68 if i < 2 else 56) * u, y * u)], fill=c, width=int(6 * u))
    elif name == "check":
        d.line([(18 * u, 54 * u), (42 * u, 76 * u), (84 * u, 26 * u)], fill=c, width=int(13 * u), joint="curve")
    elif name == "lock":
        d.rounded_rectangle([20 * u, 44 * u, 80 * u, 90 * u], radius=10 * u, fill=c)
        d.arc([30 * u, 12 * u, 70 * u, 72 * u], 180, 360, fill=c, width=int(9 * u))
        d.ellipse([44 * u, 58 * u, 56 * u, 70 * u], fill=(0, 0, 0, 0))
    elif name == "search":
        d.ellipse([12 * u, 12 * u, 66 * u, 66 * u], outline=c, width=int(10 * u))
        d.line([(58 * u, 58 * u), (88 * u, 88 * u)], fill=c, width=int(11 * u))
    elif name == "mic":
        d.rounded_rectangle([36 * u, 8 * u, 64 * u, 56 * u], radius=14 * u, outline=c, width=lw)
        d.arc([24 * u, 28 * u, 76 * u, 78 * u], 0, 180, fill=c, width=lw)
        d.line([(50 * u, 78 * u), (50 * u, 92 * u)], fill=c, width=lw)
    elif name == "person":
        d.ellipse([34 * u, 10 * u, 66 * u, 42 * u], fill=c)
        d.pieslice([16 * u, 48 * u, 84 * u, 120 * u], 180, 360, fill=c)
    elif name == "shield":
        d.polygon([(50 * u, 6 * u), (88 * u, 20 * u), (84 * u, 56 * u), (50 * u, 94 * u), (16 * u, 56 * u), (12 * u, 20 * u)], outline=c, width=lw)
        d.line([(32 * u, 50 * u), (46 * u, 64 * u), (70 * u, 36 * u)], fill=c, width=int(8 * u), joint="curve")
    elif name == "arrow":
        d.line([(10 * u, 50 * u), (84 * u, 50 * u)], fill=c, width=int(10 * u))
        d.polygon([(70 * u, 26 * u), (96 * u, 50 * u), (70 * u, 74 * u)], fill=c)
    elif name == "zip":
        d.rounded_rectangle([14 * u, 8 * u, 86 * u, 92 * u], radius=10 * u, outline=c, width=lw)
        for i in range(6):
            d.rectangle([(44 if i % 2 == 0 else 50) * u, (14 + i * 9) * u, (50 if i % 2 == 0 else 56) * u, (21 + i * 9) * u], fill=c)
    elif name == "chip":
        d.rounded_rectangle([24 * u, 24 * u, 76 * u, 76 * u], radius=8 * u, outline=c, width=lw)
        for k in (36, 50, 64):
            d.line([(k * u, 10 * u), (k * u, 24 * u)], fill=c, width=int(5 * u))
            d.line([(k * u, 76 * u), (k * u, 90 * u)], fill=c, width=int(5 * u))
            d.line([(10 * u, k * u), (24 * u, k * u)], fill=c, width=int(5 * u))
            d.line([(76 * u, k * u), (90 * u, k * u)], fill=c, width=int(5 * u))
    elif name == "cross":
        d.line([(22 * u, 22 * u), (78 * u, 78 * u)], fill=c, width=int(12 * u))
        d.line([(78 * u, 22 * u), (22 * u, 78 * u)], fill=c, width=int(12 * u))
    elif name == "copy":
        d.rounded_rectangle([30 * u, 30 * u, 88 * u, 90 * u], radius=9 * u, outline=c, width=lw)
        d.rounded_rectangle([10 * u, 10 * u, 68 * u, 70 * u], radius=9 * u, outline=c, width=lw)
    elif name == "restore":
        d.arc([14 * u, 14 * u, 86 * u, 86 * u], 40, 330, fill=c, width=int(9 * u))
        d.polygon([(70 * u, 6 * u), (94 * u, 30 * u), (62 * u, 34 * u)], fill=c)
    elif name == "star":
        pts = []
        for i in range(10):
            r_ = 46 if i % 2 == 0 else 20
            a_ = math.radians(-90 + i * 36)
            pts.append((50 * u + r_ * u * math.cos(a_), 52 * u + r_ * u * math.sin(a_)))
        d.polygon(pts, fill=c)
    else:
        raise ValueError(name)
    return np.asarray(im.resize((size, size), Image.LANCZOS))


def icon(g, name, cx, cy, size, color=INK, alpha=1.0, scale=1.0, rot=0.0):
    g.blit(icon_sprite(name, g.S(size), color), cx, cy, "c", alpha, scale, rot)


# ------------------------------------------------------------------ camera / post
def camera(img, zoom=1.0, cx=0.5, cy=0.5, rot=0.0, shake=(0, 0)):
    """Zoom about (cx, cy) in 0..1 frame coordinates, rotate, shake (pixels). Returns a new frame."""
    if zoom == 1.0 and not rot and not shake[0] and not shake[1]:
        return img
    h, w = img.shape[:2]
    c = (cx * w, cy * h)
    M = cv2.getRotationMatrix2D(c, rot, zoom)
    M[0, 2] += shake[0]
    M[1, 2] += shake[1]
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def zoom_blur(img, strength, cx=0.5, cy=0.5, samples=7):
    """Radial 'speed' blur used on the cuts: average of progressively zoomed copies."""
    if strength <= 0.002:
        return img
    acc = np.zeros(img.shape, np.float32)
    for i in range(samples):
        acc += camera(img, 1.0 + strength * i / (samples - 1), cx, cy).astype(np.float32)
    return (acc / samples).astype(np.uint8)


def chroma(img, px):
    """Chromatic aberration: red and blue channels pushed apart."""
    px = int(px)
    if px <= 0:
        return img
    out = img.copy()
    out[..., 0] = np.roll(img[..., 0], px, axis=1)
    out[..., 2] = np.roll(img[..., 2], -px, axis=1)
    return out


_vig = {}


def vignette(img, strength=0.5):
    h, w = img.shape[:2]
    key = (w, h)
    if key not in _vig:
        yy, xx = np.mgrid[0:h, 0:w]
        d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
        _vig[key] = np.clip(1 - 0.5 * np.clip(d - 0.55, 0, None) ** 1.5, 0.35, 1)[..., None].astype(np.float32)
    v = 1 - (1 - _vig[key]) * strength * 2
    return np.clip(img.astype(np.float32) * v, 0, 255).astype(np.uint8)
