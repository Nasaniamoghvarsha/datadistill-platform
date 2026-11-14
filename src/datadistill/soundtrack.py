"""Music and played-video sound in a meeting recording (a song before the start, a video shown during a talk).

Speech stops between words and sentences; music and video soundtracks almost never fall silent. Measured on a
2.5 h hybrid Teams meeting: speech windows are near-silent 30-60% of the time, the song before the start and a
video played in the room 0-8%. A stretch of at least 30 s that is never near-silent is marked as soundtrack: its
lines are kept (a video can carry information) but are not given to a person, and do not create voices.
"""
from __future__ import annotations

import numpy as np

SR = 16000
WIN_S, HOP_S = 20, 10
FRAME = 400               # 25 ms
QUIET = 0.12              # a frame below 12% of the window's loud level counts as near-silent
MAX_PAUSE = 0.08          # windows near-silent less than 8% of the time are music / soundtrack
MIN_LEN_S = 30
SOUNDTRACK_ID = "S0"
SOUNDTRACK_NAME = "Music / video sound"


def pause_share(a):
    n = len(a) // FRAME
    if n < 10:
        return 1.0
    rms = np.sqrt((a[: n * FRAME].reshape(n, FRAME) ** 2).mean(1) + 1e-12)
    hi = np.percentile(rms, 95)
    if hi < 1e-4:                      # silence: not music
        return 1.0
    return float((rms < QUIET * hi).mean())


def regions(audio, sr=SR):
    """[(start_s, end_s)] of music / soundtrack."""
    if audio is None or len(audio) < WIN_S * sr:
        return []
    starts = range(0, max(1, int(len(audio) / sr) - WIN_S + 1), HOP_S)
    flagged = [(t, pause_share(audio[t * sr:(t + WIN_S) * sr]) < MAX_PAUSE) for t in starts]
    out = []
    for t, f in flagged:
        if not f:
            continue
        a, b = t, t + WIN_S
        if out and a <= out[-1][1]:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(float(a), float(b)) for a, b in out if b - a >= MIN_LEN_S]


def is_soundtrack(start, end, regs, share=0.6):
    if not regs or end <= start:
        return False
    cover = sum(max(0.0, min(end, b) - max(start, a)) for a, b in regs)
    return cover / (end - start) >= share
