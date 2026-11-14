"""Part 1: a meeting replay rebuilt from the distilled output (real audio, stored slide pictures, timestamps, transcript lines).
Part 2: the plot twist, told by Disky: what is real, what is rebuilt, how close it is, how small it is.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

import art
import gfx
from art import BLUE, CORAL, INKC, LILAC, MINT, SUN, TEAL, WHITE, disky, ghost, sticker
from gfx import clamp, ease_in, ease_io, ease_out, lerp, mix, rand, rrect_sprite, seg, text_sprite
from scenes import Scene, fmt_bytes, gradient_text, pop, title

HERE = Path(__file__).resolve().parent
RP = HERE / "replay"

# ------------------------------------------------------------------ the replay window (a generic meeting UI, not any product's)
UI_BG = (24, 25, 30)
UI_BAR = (32, 33, 40)
UI_TILE = (44, 46, 56)
UI_LINE = (66, 68, 82)
UI_GREEN = (98, 196, 84)
UI_PURPLE = (124, 128, 214)
SKINS = [(247, 205, 168), (226, 170, 128), (198, 134, 98), (240, 188, 150)]
HAIRS = [(48, 36, 32), (110, 78, 50), (30, 30, 36), (170, 120, 70)]
SHIRTS = [(86, 126, 214), (214, 98, 110), (88, 170, 140), (160, 120, 210)]


def clock(t):
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


@lru_cache(maxsize=32)
def _slide(name):
    im = cv2.imread(str(RP / "slides" / name))
    return cv2.cvtColor(im, cv2.COLOR_BGR2RGB)


def stage_image(g, name, cx, cy, bw, bh, alpha=1.0, zoom=1.0):
    """The slide picture fitted into the stage box (bw x bh logical px), rounded corners."""
    src = _slide(name)
    h0, w0 = src.shape[:2]
    k = min(bw / w0, bh / h0)
    dw, dh = w0 * k, h0 * k
    cw, ch = w0 / zoom, h0 / zoom
    x0, y0 = (w0 - cw) / 2, (h0 - ch) / 2
    crop = src[int(y0):int(y0 + ch), int(x0):int(x0 + cw)]
    pw, ph = g.S(dw), g.S(dh)
    img = cv2.resize(crop, (pw, ph), interpolation=cv2.INTER_AREA if crop.shape[1] > pw else cv2.INTER_CUBIC)
    mask = rrect_sprite(pw, ph, g.S(14), (255, 255, 255))[..., 3]
    g.blit(np.dstack([img, mask]), cx, cy, "c", alpha)
    return dw, dh


def avatar(g, cx, cy, r, who, env, speaking, t):
    """A friendly cartoon participant. The mouth opens with the real loudness of the audio."""
    sk, hr, sh = SKINS[who % 4], HAIRS[who % 4], SHIRTS[who % 4]
    bob = (math.sin(t * 3 + who) * 2 + (env * 4 if speaking else 0))
    art.ell(g, cx, cy + r * 1.55, r * 1.55, r * 0.95, sh)
    art.ell(g, cx, cy + bob * 0.2, r, r * 1.08, sk)
    art.ell(g, cx, cy - r * 0.35 + bob * 0.2, r * 1.06, r * 0.7, hr)
    art.ell(g, cx, cy - r * 0.05 + bob * 0.2, r * 0.98, r * 0.5, sk)
    for sgn in (-1, 1):
        art.ell(g, cx + sgn * r * 0.38, cy + r * 0.05 + bob * 0.2, r * 0.11, r * 0.13 * (0.15 if (t + who) % 3.4 < 0.1 else 1.0), INKC)
    mo = r * (0.06 + 0.26 * clamp(env)) if speaking else r * 0.05
    art.ell(g, cx, cy + r * 0.52 + bob * 0.2, r * (0.24 + 0.06 * clamp(env)), mo, (120, 50, 60) if mo > r * 0.1 else INKC)


# ------------------------------------------------------------------ Part 1: one scene per excerpt
class Replay(Scene):
    def __init__(self, ctx, ex, prev_t):
        super().__init__(ctx)
        self.ex, self.prev_t = ex, prev_t
        self.id = f"replay{ex['i']}"
        self.lead = 0.25
        self.env = np.array(ex["env"])
        self.people = sorted({tn["who"] for tn in ex["turns"]}) or ["Speaker 1"]

    def draw(self, g, t, D, B):
        ex = self.ex
        te = clamp(t - self.lead, 0, ex["dur"])
        g.img[:] = UI_BG
        # ---- top bar
        g.fill_rect(0, 0, 1920, 70, UI_BAR)
        g.text(ex["title"], 60, 35, 30, WHITE, "bodyb", "l")
        art.ell(g, 60 + g.text_width(ex["title"], 30, "bodyb") + 40, 35, 9, 9, (230, 70, 70) if int(t * 1.6) % 2 == 0 else (120, 40, 44))
        g.text("Recording", 60 + g.text_width(ex["title"], 30, "bodyb") + 62, 35, 24, (190, 192, 204), "body", "l")
        g.text(clock(ex["orig_start"] + te), 960, 35, 30, WHITE, "mono")
        for i in range(3):
            art.ell(g, 1700 + i * 60, 35, 18, 18, UI_TILE)
        gfx.icon(g, "person", 1700, 35, 22, (190, 192, 204))
        gfx.icon(g, "doc", 1760, 35, 22, (190, 192, 204))
        gfx.icon(g, "search", 1820, 35, 22, (190, 192, 204))
        # ---- stage with the shared screen
        sx, sy, sw, sh = 700, 480, 1250, 700
        g.card(sx - 30, sy, sw + 20, sh + 20, r=22, fill=(14, 14, 18), outline=UI_LINE, ow=2, shadow=0, anchor="c") if False else None
        slides = ex["slides"]
        cur = 0
        for i, s in enumerate(slides):
            if te >= s["start"]:
                cur = i
        z = 1.0 + 0.03 * (te / max(1.0, ex["dur"]))
        if slides:
            s = slides[cur]
            fade = ease_out(seg(te, s["start"], s["start"] + 0.45)) if cur > 0 else 1.0
            if cur > 0 and fade < 1.0:
                stage_image(g, slides[cur - 1]["file"], sx, sy, sw, sh, 1.0, z)
            stage_image(g, s["file"], sx, sy, sw, sh, fade if cur > 0 else 1.0, z)
            if cur == 0 and te < 0.2:
                pass
        speaker_now = self._who_at(te)
        pill = f"{speaker_now} is presenting"
        pw = g.text_width(pill, 22, "bodyb") + 44
        g.card(70 + pw / 2 + 20, 115, pw, 44, r=22, fill=(20, 20, 26), outline=None, ow=0, shadow=0, alpha=0.88)
        art.ell(g, 70 + 36, 115, 8, 8, UI_GREEN)
        g.text(pill, 70 + 56, 115, 22, WHITE, "bodyb", "l")
        # ---- participants
        env = float(self.env[min(len(self.env) - 1, int(te * 30))]) if len(self.env) else 0.0
        names = (self.people + ["Speaker 2", "Speaker 3"])[:3]
        tiles = [(1640, 250), (1640, 500), (1640, 750)]
        for i, ((tx, ty), nm) in enumerate(zip(tiles, names)):
            speaking = (nm == speaker_now) and env > 0.05
            pulse = 0.5 + 0.5 * math.sin(t * 8) if speaking else 0
            g.card(tx, ty, 460, 232, r=22, fill=UI_TILE, outline=UI_GREEN if speaking else UI_LINE, ow=5 if speaking else 2, shadow=0)
            if speaking:
                g.glow_disc(tx, ty, 150, UI_GREEN, 0.10 + 0.14 * pulse)
            avatar(g, tx, ty - 22, 50, i, env if speaking else 0, speaking, t)
            g.card(tx - 150, ty + 96, 130, 32, r=10, fill=(20, 20, 26), outline=None, ow=0, shadow=0, alpha=0.85)
            g.text(nm, tx - 150, ty + 96, 20, WHITE, "bodyb")
            if speaking:
                for b_ in range(4):
                    hh = 6 + 22 * clamp(env) * abs(math.sin(t * 9 + b_ * 1.3))
                    g.fill_rect(tx + 176 + b_ * 9, ty + 100 - hh / 2, 5, hh, UI_GREEN)
            else:
                gfx.icon(g, "mic", tx + 190, ty + 96, 22, (150, 152, 168))
                g.line((tx + 176, ty + 108), (tx + 204, ty + 84), (230, 80, 80), 3)
        # ---- live captions (centred under the shared screen)
        cap = self._caption(g, te)
        if cap:
            who, lines = cap
            h = 46 * len(lines) + 40
            wmax = max(g.text_width(ln, 38, "bodyb") for ln in lines)
            cw = min(1260, max(560, wmax + 80))
            left = 700 - cw / 2
            g.card(700, 915, cw, h, r=18, fill=(12, 12, 16), outline=UI_LINE, ow=1, shadow=0, alpha=0.93)
            g.text(who, left + 30, 915 - h / 2 + 22, 20, UI_PURPLE, "bodyb", "l")
            for k, ln in enumerate(lines):
                g.text(ln, left + 30, 915 - h / 2 + 62 + k * 46, 38, WHITE, "bodyb", "l")
        # ---- scrubber and toolbar
        g.fill_rect(0, 990, 1920, 90, UI_BAR)
        x0, x1 = 160, 1760
        g.fill_rect(x0, 1008, x1 - x0, 6, (70, 72, 88))
        a = ease_io(seg(t, 0.0, 0.55))
        pos = lerp(self.prev_t, ex["orig_start"] + te, a) if self.prev_t is not None else ex["orig_start"] + te
        px = x0 + (x1 - x0) * pos / ex["total"]
        g.fill_rect(x0, 1008, px - x0, 6, UI_PURPLE)
        art.ell(g, px, 1011, 12, 12, WHITE)
        g.text(f"{clock(pos)} / {clock(ex['total'])}", 36, 1045, 22, (190, 192, 204), "mono", "l")
        for i, ic in enumerate(("mic", "person", "doc", "search")):
            art.ell(g, 760 + i * 80, 1046, 24, 24, UI_TILE)
            gfx.icon(g, ic, 760 + i * 80, 1046, 26, WHITE)
        g.card(1160, 1046, 110, 44, r=22, fill=(214, 64, 64), outline=None, ow=0, shadow=0)
        g.text("Leave", 1160, 1046, 22, WHITE, "bodyb")
        # fade in the very first frames / out at the end
        k_in = 1 - ease_out(seg(t, 0, 0.18))
        k_out = ease_in(seg(t, D - 0.18, D))
        if max(k_in, k_out) > 0:
            g.fill_rect(0, 0, 1920, 1080, (0, 0, 0), max(k_in, k_out))

    def _who_at(self, te):
        who = self.ex["turns"][0]["who"] if self.ex["turns"] else "Speaker 1"
        for tn in self.ex["turns"]:
            if te >= tn["start"]:
                who = tn["who"]
        return who

    def _caption(self, g, te):
        cur = None
        for tn in self.ex["turns"]:
            if tn["start"] - 0.05 <= te:
                cur = tn
        if cur is None or te > cur["end"] + 0.8:
            return None
        words = cur["text"].split()
        frac = clamp((te - cur["start"]) / max(0.5, (cur["end"] - cur["start"]) * 0.92))
        shown = words[: max(1, int(math.ceil(len(words) * frac)))]
        lines, line = [], ""
        for w in shown:
            trial = (line + " " + w).strip()
            if g.text_width(trial, 38, "bodyb") <= 1150 or not line:
                line = trial
            else:
                lines.append(line)
                line = w
        lines.append(line)
        return cur["who"], lines[-2:]

    def cam(self, t, D, B):
        return {"zoom": 1.0, "cx": 0.5, "cy": 0.5, "rot": 0.0, "shake": (0, 0)}

    def sfx(self, D, B):
        return [(0.0, "tick", 0.15)] if self.prev_t is not None else []


# ------------------------------------------------------------------ Part 2 scenes
class Twist(Scene):
    """Plot twist: three ingredients become the replay."""
    id = "twist"

    def draw(self, g, t, D, B):
        c = self.ctx
        art.bg_lab(g, t)
        b0, b1 = B[0], B[1]
        title(g, "PLOT TWIST", 960, 150, 120, SUN, INKC, alpha=ease_out(seg(t, 0.15, 0.5)), scale=1 + 0.3 * (1 - ease_out(seg(t, 0.15, 0.55))))
        disky(g, 330, 780, t, fat=0.0, mood="happy", arms="wave" if t < b1[0] else "point", goggles=True, bar=0.03, scale=0.95)
        # the three ingredients, each with its real size
        items = [("THE VOICES", "audio track", c["audio"], BLUE, 700), ("THE SLIDES", "stored pictures", c["slides"], CORAL, 960), ("THE TIMELINE", "timestamps + words", c["text"], LILAC, 1220)]
        for i, (lab, sub, size, col, x) in enumerate(items):
            p = pop(t, b1[0] + 0.1 + i * 0.5, 0.55)
            if p <= 0:
                continue
            art.shadow(g, x, 560, 130, 18, 0.2)
            art.rr(g, x, 480, 230, 190, 30, WHITE, outline=INKC, ow=6, alpha=min(1, p * 1.3)) if False else g.card(x, 480, 230, 190, r=30, fill=WHITE, outline=INKC, ow=6, shadow=0, alpha=min(1, p * 1.3), scale=max(0.01, p))
            art.bottle(g, x, 530, "", col, level=0.9, scale=0.9) if False else None
            g.text(fmt_bytes(size), x, 450, 44, col, "comic", alpha=min(1, p), scale=max(0.01, p))
            g.text(lab, x, 505, 24, INKC, "comic", alpha=min(1, p), scale=max(0.01, p))
            g.text(sub, x, 540, 20, INKC, "ink", alpha=min(1, p), scale=max(0.01, p))
        # the replay window that comes out
        if t > b1[0] + 1.9:
            p = pop(t, b1[0] + 1.9, 0.6)
            g.card(1590, 470, 460, 300, r=28, fill=UI_BG, outline=INKC, ow=6, shadow=24, alpha=min(1, p), scale=max(0.01, p))
            for k in range(3):
                g.card(1590 - 120 + k * 120, 560, 100, 62, r=10, fill=UI_TILE, outline=None, ow=0, shadow=0, alpha=min(1, p), scale=max(0.01, p))
            g.card(1560, 430, 330, 160, r=10, fill=(244, 247, 255), outline=None, ow=0, shadow=0, alpha=min(1, p), scale=max(0.01, p))
            g.text("REPLAY", 1590, 320, 40, MINT, "comic", alpha=min(1, p))
            for i in range(3):
                x = 960 + 200 * (i - 1)
                a0 = ease_out(seg(t, b1[0] + 1.2 + i * 0.1, b1[0] + 1.9))
                art.poly(g, [(1350, 470), (1400, 470), (1400, 452), (1440, 480), (1400, 508), (1400, 490), (1350, 490)], INKC) if i == 0 and a0 > 0.1 else None
        sticker(g, 960, 910, "a meeting that was never played back", MINT, size=36, rot=-3, p=pop(t, b0[0] + 1.0, 0.5))

    def sfx(self, D, B):
        return [(0.15, "boom", 0.8), (0.2, "sparkle", 0.5), (B[1][0] + 0.1, "pop", 0.6), (B[1][0] + 0.6, "pop", 0.6), (B[1][0] + 1.1, "pop", 0.6), (B[1][0] + 1.9, "ding", 0.7)]


class SideBySide(Scene):
    """The original next to the replay: what is real, what is rebuilt."""
    id = "sidebyside"

    def __init__(self, ctx, replay_scene):
        super().__init__(ctx)
        self.replay = replay_scene

    def draw(self, g, t, D, B):
        art.bg_library(g, t)
        b0, b1, b2 = B[0], B[1], B[2]
        # left: the original frame (participant strip pixelated), right: the replay frame rendered by the real scene code
        pl = pop(t, 0.1, 0.6)
        W, H = 800, 450
        self._panel_orig(g, 480, 440, W, H, min(1, pl), max(0.01, pl))
        pr = pop(t, 0.35, 0.6)
        self._panel_replay(g, 1440, 440, W, H, min(1, pr), max(0.01, pr))
        g.text("ORIGINAL", 480, 175, 40, WHITE, "comic", alpha=min(1, pl))
        g.text("REBUILT", 1440, 175, 40, MINT, "comic", alpha=min(1, pr))
        # real vs rebuilt tags
        reals = ["the voices", "the words", "the slides", "the timing"]
        rebuilt = ["the window", "the avatars", "the motion between slides"]
        for i, txt in enumerate(reals):
            p = pop(t, b1[0] + 0.2 + i * 0.35, 0.5)
            if p > 0:
                sticker(g, 330 + i * 255, 760, txt, MINT, size=34, rot=[-3, 3, -2, 2][i], p=min(1, p))
        if t >= b1[0]:
            g.text("REAL", 90, 760, 34, MINT, "comic", "l", alpha=ease_out(seg(t, b1[0], b1[0] + 0.3)))
        for i, txt in enumerate(rebuilt):
            p = pop(t, b2[0] + 0.2 + i * 0.5, 0.5)
            if p > 0:
                sticker(g, [430, 780, 1290][i], 860, txt, SUN, size=34, rot=[-3, 3, -2][i], p=min(1, p))
        if t >= b2[0]:
            g.text("REBUILT", 90, 860, 34, SUN, "comic", "l", alpha=ease_out(seg(t, b2[0], b2[0] + 0.3)))
            if t > b2[0] + 2.0:
                sticker(g, 960, 970, "no face and no voice was ever generated", CORAL, size=36, rot=-2, p=pop(t, b2[0] + 2.0, 0.5))

    def _panel_orig(self, g, cx, cy, w, h, a, s):
        im = cv2.cvtColor(cv2.imread(str(RP / "orig_cmp.png")), cv2.COLOR_BGR2RGB)
        pw, ph = g.S(w), g.S(h)
        img = cv2.resize(im, (pw, ph), interpolation=cv2.INTER_AREA)
        mask = rrect_sprite(pw, ph, g.S(20), (255, 255, 255))[..., 3]
        g.card(cx, cy, w + 20, h + 20, r=26, fill=WHITE, outline=INKC, ow=5, shadow=26, alpha=a, scale=s)
        g.blit(np.dstack([img, mask]), cx, cy, "c", a, s)
        g.text("participants hidden", cx + w * 0.36, cy - h * 0.43, 18, WHITE, "ink", alpha=a * 0.9)

    def _panel_replay(self, g, cx, cy, w, h, a, s):
        sub = gfx.Gfx(1920, 1080)
        self.replay.draw(sub, self.replay.lead + 7.0, 11.0, [])
        img = cv2.resize(sub.img, (g.S(w), g.S(h)), interpolation=cv2.INTER_AREA)
        mask = rrect_sprite(g.S(w), g.S(h), g.S(20), (255, 255, 255))[..., 3]
        g.card(cx, cy, w + 20, h + 20, r=26, fill=WHITE, outline=INKC, ow=5, shadow=26, alpha=a, scale=s)
        g.blit(np.dstack([img, mask]), cx, cy, "c", a, s)

    def sfx(self, D, B):
        return [(0.1, "whoosh", 0.4), (B[1][0] + 0.2, "ding", 0.5), (B[1][0] + 0.55, "ding", 0.5), (B[1][0] + 0.9, "ding", 0.5), (B[1][0] + 1.25, "ding", 0.5),
                (B[2][0] + 0.2, "pop", 0.5), (B[2][0] + 0.7, "pop", 0.5), (B[2][0] + 1.2, "pop", 0.5), (B[2][0] + 2.0, "stamp", 0.7)]


class HowClose(Scene):
    id = "howclose"

    def draw(self, g, t, D, B):
        c = self.ctx
        art.bg_sky(g, t)
        b0, b1 = B[0], B[1]
        # the score ring
        p = ease_out(seg(t, b0[0], b0[0] + 1.6), 3)
        cx, cy, R = 560, 470, 230
        art.ell(g, cx, cy, R + 24, R + 24, WHITE)
        art.ell(g, cx, cy, R + 24, R + 24, INKC, thick=6)
        g.arc(cx, cy, R, 0, 359.9, (226, 232, 246), 36)
        g.arc(cx, cy, R, -90, -90 + 359 * c["share"] * p, MINT, 36)
        g.text(f"{c['share'] * 100 * p:.0f}%", cx, cy - 10, 140, INKC, "comic")
        g.text("same slide as the original", cx, cy + 90, 30, INKC, "ink")
        g.text(f"{c['same']} of {c['checked']} random moments, checked by eye", cx, cy + R + 70, 28, INKC, "comic", alpha=ease_out(seg(t, b0[0] + 1.0, b0[0] + 1.5)))
        disky(g, 170, 850, t, fat=0.0, mood="proud", arms="cheer", glasses=True, bar=0.03, scale=0.7)
        # sizes
        if t >= b1[0]:
            q = t - b1[0]
            pp = pop(q, 0.1, 0.5)
            g.card(1350, 520, 780, 600, r=34, fill=WHITE, outline=INKC, ow=6, shadow=26, alpha=min(1, pp), scale=max(0.01, pp))
            g.text("THE SAME TALK", 1350, 270, 36, INKC, "comic", alpha=min(1, pp))
            full = 640
            ob = ease_out(seg(q, 0.3, 1.0))
            g.card(1050 + full * ob / 2, 380, full * ob + 1, 70, r=18, fill=CORAL, outline=INKC, ow=4, shadow=0)
            g.text(f"original  {fmt_bytes(c['orig'])}", 1050 + 20, 380, 30, WHITE, "comic", "l", alpha=ob)
            rb = ease_out(seg(q, 1.2, 1.8))
            wrep = max(18.0, full * c["rep"] / c["orig"])
            g.card(1050 + wrep * rb / 2, 500, wrep * rb + 1, 70, r=18, fill=MINT, outline=INKC, ow=4, shadow=0)
            g.text(f"audio + slides + text  {fmt_bytes(c['rep'])}", 1050, 575, 28, INKC, "comic", "l", alpha=rb)
            sticker(g, 1350, 700, f"{(1 - c['rep'] / c['orig']) * 100:.1f}% smaller", MINT, size=52, rot=-3, p=pop(q, 2.0, 0.5))

    def sfx(self, D, B):
        return [(B[0][0] + 0.1, "ding", 0.6), (B[0][0] + 0.2, "sparkle", 0.5), (B[1][0] + 0.1, "whoosh", 0.5), (B[1][0] + 1.2, "pop", 0.5), (B[1][0] + 2.0, "stamp", 0.8)]


class WhyAI(Scene):
    id = "whyai"

    def draw(self, g, t, D, B):
        art.bg_lab(g, t)
        b0 = B[0]
        # Disky the chef
        disky(g, 520, 700, t, fat=0.2, mood="happy", arms="cheer", bar=0.1, scale=1.1, hat=True)
        q = t - b0[0]
        cards = [("REAL", "voices, words, slides, timing", MINT), ("GENERATED", "the picture around them", SUN), ("PRIVATE", "faces become avatars on purpose", LILAC)]
        for i, (head, sub, col) in enumerate(cards):
            p = pop(q, 0.3 + i * 0.9, 0.55)
            if p <= 0:
                continue
            y = 330 + i * 190
            g.card(1330, y, 760, 160, r=30, fill=WHITE, outline=INKC, ow=6, shadow=22, alpha=min(1, p * 1.3), scale=max(0.01, p))
            g.card(1330 - 360, y, 26, 160, r=13, fill=col, outline=None, ow=0, shadow=0, alpha=min(1, p), scale=max(0.01, p))
            g.text(head, 1330, y - 32, 44, col if col != SUN else (230, 150, 20), "comic", alpha=min(1, p), scale=max(0.01, p))
            g.text(sub, 1330, y + 28, 30, INKC, "ink", alpha=min(1, p), scale=max(0.01, p))
        sticker(g, 520, 930, "all the flavour, none of the groceries", SUN, size=36, rot=-3, p=pop(q, 3.6, 0.5))

    def sfx(self, D, B):
        return [(B[0][0] + 0.3, "pop", 0.5), (B[0][0] + 1.2, "pop", 0.5), (B[0][0] + 2.1, "pop", 0.5), (B[0][0] + 3.6, "stamp", 0.6)]


class Finale(Scene):
    id = "finale"

    def draw(self, g, t, D, B):
        c = self.ctx
        b0 = B[0]
        art.bg_sky(g, t, sunset=ease_out(seg(t, 0, 2.5)))
        ghost(g, 1520, 640, t, mood="content", scale=0.8, badge=True, wave=True)
        art.dana(g, 1000, 960, t, mood="calm", mug=True, scale=0.9, thumbs=True)
        disky(g, 520, 760, t, fat=0.0, mood="happy", arms="wave", glasses=True, bar=0.03, scale=1.0)
        p = pop(t, b0[0] + 0.1, 0.7)
        gradient_text(g, c["name"], 960, 260, 190, (255, 120, 90), (120, 80, 230), alpha=min(1, p * 1.4), scale=max(0.01, p))
        title(g, c["tagline"], 960, 410, 54, WHITE, INKC, alpha=ease_out(seg(t, b0[0] + 0.9, b0[0] + 1.5)))
        sticker(g, 470, 560, "Disk usage: 3%.  Mood: 100%.", SUN, size=36, rot=-3, p=pop(t, b0[0] + 2.4, 0.5))
        fade = ease_in(seg(t, D - 0.7, D), 2)
        if fade > 0:
            g.fill_rect(0, 0, 1920, 1080, (0, 0, 0), fade)

    def cam(self, t, D, B):
        c = super().cam(t, D, B)
        c["zoom"] = 1.0 + 0.04 * (t / max(D, 1e-3)) + 0.2 * (1 - ease_out(seg(t, 0, 0.8), 4))
        return c

    def sfx(self, D, B):
        return [(0.0, "whoosh", 0.5), (B[0][0] + 0.15, "boing", 0.6), (B[0][0] + 0.25, "ding", 0.8), (B[0][0] + 2.4, "pop", 0.5)]
