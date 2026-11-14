"""Music, sound effects and the final mix for the pitch video. Everything is synthesised with numpy: no samples, no licences.

Layout: the voice (one WAV per spoken beat) is placed on the timeline; a quiet electronic bed (pulse, bass, pad, arpeggio) runs
under it and ducks while the voice speaks; short effects (hits, whooshes, ticks, dings) are placed by the scenes.
"""
import numpy as np
import soundfile as sf
from scipy import signal

SR = 48000
rng = np.random.default_rng(7)


def _t(n):
    return np.arange(n) / SR


def lowpass(x, fc, order=2):
    b, a = signal.butter(order, fc / (SR / 2), "low")
    return signal.lfilter(b, a, x)


def highpass(x, fc, order=2):
    b, a = signal.butter(order, fc / (SR / 2), "high")
    return signal.lfilter(b, a, x)


def bandpass(x, f0, f1, order=2):
    b, a = signal.butter(order, [f0 / (SR / 2), min(0.99, f1 / (SR / 2))], "band")
    return signal.lfilter(b, a, x)


def env_ad(n, attack, decay):
    t = _t(n)
    return np.minimum(1, t / max(attack, 1e-4)) * np.exp(-t / max(decay, 1e-4))


# ------------------------------------------------------------------ effects
def fx(kind):
    if kind == "hit":
        n = int(0.28 * SR)
        t = _t(n)
        thump = np.sin(2 * np.pi * (140 * np.exp(-t * 22) + 48) * t) * np.exp(-t * 16)
        noise = bandpass(rng.standard_normal(n), 800, 6000) * np.exp(-t * 40) * 0.5
        return 0.9 * thump + noise
    if kind == "boom":
        n = int(1.4 * SR)
        t = _t(n)
        sub = np.sin(2 * np.pi * (70 * np.exp(-t * 3.0) + 34) * t) * np.exp(-t * 2.4)
        noise = lowpass(rng.standard_normal(n), 900) * np.exp(-t * 6) * 0.5
        return 1.0 * sub + noise
    if kind == "whoosh":
        n = int(0.55 * SR)
        t = _t(n)
        x = rng.standard_normal(n)
        out = np.zeros(n)
        for i in range(5):                               # sweep: band moves up while the level rises and falls
            a, b = int(i * n / 5), int((i + 1) * n / 5)
            out[a:b] = bandpass(x[a:b], 300 + i * 650, 900 + i * 1100)
        return out * np.sin(np.pi * np.clip(t / (n / SR), 0, 1)) ** 1.5 * 0.9
    if kind == "riser_down":
        n = int(1.1 * SR)
        t = _t(n)
        x = rng.standard_normal(n)
        out = np.zeros(n)
        for i in range(6):
            a, b = int(i * n / 6), int((i + 1) * n / 6)
            out[a:b] = lowpass(x[a:b], 5200 - i * 750)
        return out * np.exp(-t * 2.2) * 0.5
    if kind == "tick":
        n = int(0.03 * SR)
        t = _t(n)
        return np.sin(2 * np.pi * 2100 * t) * np.exp(-t * 160) * 0.8
    if kind == "click":
        n = int(0.05 * SR)
        t = _t(n)
        return (highpass(rng.standard_normal(n), 1500) * np.exp(-t * 120) * 0.7) + np.sin(2 * np.pi * 900 * t) * np.exp(-t * 90) * 0.3
    if kind == "ding":
        n = int(0.9 * SR)
        t = _t(n)
        return (np.sin(2 * np.pi * 1318.5 * t) + 0.5 * np.sin(2 * np.pi * 1976 * t) + 0.25 * np.sin(2 * np.pi * 2637 * t)) * np.exp(-t * 5) * 0.35
    if kind == "boing":
        n = int(0.5 * SR)
        t = _t(n)
        f = 220 + 260 * np.exp(-t * 7) * (1 + 0.5 * np.sin(2 * np.pi * 12 * t))
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 6) * 0.6
    if kind == "pop":
        n = int(0.12 * SR)
        t = _t(n)
        return np.sin(2 * np.pi * (900 * np.exp(-t * 40) + 300) * t) * np.exp(-t * 35) * 0.8
    if kind == "thud":
        n = int(0.22 * SR)
        t = _t(n)
        return (np.sin(2 * np.pi * (90 * np.exp(-t * 18) + 45) * t) * np.exp(-t * 16) + lowpass(rng.standard_normal(n), 500) * np.exp(-t * 40) * 0.4) * 0.9
    if kind == "steps":
        n = int(0.3 * SR)
        t = _t(n)
        out = np.zeros(n)
        for k0 in (0.0, 0.15):
            a0 = int(k0 * SR)
            m = int(0.07 * SR)
            out[a0:a0 + m] += lowpass(rng.standard_normal(m), 600) * np.exp(-_t(m) * 60) * 0.8
        return out
    if kind == "wah":                                    # sad trombone: three notes sliding down
        n = int(1.4 * SR)
        t = _t(n)
        notes = [(0.0, 0.35, 233), (0.38, 0.7, 220), (0.72, 1.4, 196)]
        out = np.zeros(n)
        for a0, b0, f0 in notes:
            i0, i1 = int(a0 * SR), int(b0 * SR)
            tt = _t(i1 - i0)
            f = f0 * (1 - 0.08 * (tt / max(tt[-1], 1e-3)) * (a0 > 0.7)) * (1 + 0.015 * np.sin(2 * np.pi * 6 * tt))
            w = signal.sawtooth(2 * np.pi * np.cumsum(f) / SR)
            out[i0:i1] += lowpass(w, 900) * np.minimum(1, tt / 0.03) * np.exp(-tt * 1.5) * 0.45
        return out
    if kind == "wah_happy":
        n = int(0.5 * SR)
        t = _t(n)
        f = np.where(t < 0.2, 392, 523)
        return signal.sawtooth(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 6) * 0.2
    if kind == "scratch":
        n = int(0.4 * SR)
        t = _t(n)
        x = rng.standard_normal(n)
        return bandpass(x, 1500, 7000) * np.abs(np.sin(2 * np.pi * 7 * t)) * np.exp(-t * 5) * 0.7
    if kind == "alarm":
        n = int(0.9 * SR)
        t = _t(n)
        f = np.where((t * 5) % 1 < 0.5, 880, 660)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 2) * 0.3
    if kind == "hiss":
        n = int(0.7 * SR)
        t = _t(n)
        return highpass(rng.standard_normal(n), 3000) * np.exp(-t * 4) * 0.35
    if kind == "drip":
        n = int(0.2 * SR)
        t = _t(n)
        return np.sin(2 * np.pi * (1500 * np.exp(-t * 30) + 500) * t) * np.exp(-t * 28) * 0.6
    if kind == "stamp":
        n = int(0.35 * SR)
        t = _t(n)
        return (np.sin(2 * np.pi * (110 * np.exp(-t * 14) + 55) * t) * np.exp(-t * 12) + bandpass(rng.standard_normal(n), 600, 5000) * np.exp(-t * 50) * 0.5)
    if kind == "sparkle":
        n = int(0.9 * SR)
        t = _t(n)
        out = np.zeros(n)
        for i, f in enumerate((1568, 1976, 2349, 2637, 3136)):
            a0 = int(i * 0.07 * SR)
            m = n - a0
            tt = _t(m)
            out[a0:] += np.sin(2 * np.pi * f * tt) * np.exp(-tt * 8) * 0.18
        return out
    raise ValueError(kind)


# ------------------------------------------------------------------ music bed
def music(duration, scene_spans, bpm=116):
    """A bouncy bed in C major: pizzicato bass, marimba-style arpeggios, soft kick, claps on 2 and 4, shaker. C - Am - F - G."""
    n = int(duration * SR)
    out = np.zeros(n)
    beat = 60.0 / bpm
    bar = 4 * beat
    chords = [((48, 60), (60, 64, 67)), ((45, 57), (57, 60, 64)), ((41, 53), (53, 57, 60)), ((43, 55), (55, 59, 62))]
    mf = lambda m: 440.0 * 2 ** ((m - 69) / 12)  # noqa: E731

    def energy_at(t):
        for a, b, e in scene_spans:
            if a <= t < b:
                return e
        return 0.0

    def pluck(t0, midi, dur, gain, bright=0.35):
        a = int(t0 * SR)
        m = min(int(dur * SR), n - a)
        if m <= 0:
            return
        tt = _t(m)
        f = mf(midi)
        w = np.sin(2 * np.pi * f * tt) + bright * np.sin(2 * np.pi * 3 * f * tt) * np.exp(-tt * 20) + 0.15 * np.sin(2 * np.pi * 2 * f * tt)
        out[a:a + m] += w * np.exp(-tt * (7 + f / 220)) * gain

    # soft pad per bar
    for i in range(int(duration / bar) + 2):
        t0 = i * bar
        if t0 >= duration:
            break
        e = energy_at(t0 + 0.1)
        root, tones = chords[i % 4]
        a, b = int(t0 * SR), min(n, int((t0 + bar + 0.1) * SR))
        tt = _t(b - a)
        env = np.minimum(1, tt / 0.25) * np.minimum(1, (bar + 0.1 - tt) / 0.3)
        pad = sum(np.sin(2 * np.pi * mf(p) * tt) for p in tones) * 0.035
        out[a:b] += pad * env * min(1.0, e)
    # bass, arps, drums
    nb = int(duration / beat) + 1
    kick_t = _t(int(0.25 * SR))
    kick = np.sin(2 * np.pi * (100 * np.exp(-kick_t * 28) + 48) * kick_t) * np.exp(-kick_t * 12)
    clap = bandpass(rng.standard_normal(int(0.09 * SR)), 900, 3800) * np.exp(-_t(int(0.09 * SR)) * 45)
    shk = highpass(rng.standard_normal(int(0.03 * SR)), 6000) * np.exp(-_t(int(0.03 * SR)) * 120)
    for i in range(nb):
        t0 = i * beat
        e = energy_at(t0)
        if e <= 0:
            continue
        bi = int(t0 / bar) % 4
        root, tones = chords[bi]
        beat_in_bar = i % 4
        if beat_in_bar in (0, 2):
            pluck(t0, root[1] - 12 if beat_in_bar == 0 else root[1] - 5, 0.4, 0.42, 0.1)
            a = int(t0 * SR)
            m = min(len(kick), n - a)
            if m > 0 and e >= 0.6:
                out[a:a + m] += kick[:m] * 0.4
        if beat_in_bar in (1, 3) and e >= 0.85:
            a = int(t0 * SR)
            m = min(len(clap), n - a)
            if m > 0:
                out[a:a + m] += clap[:m] * 0.22
        # eighth-note marimba, a little swing, richer when the energy is up
        for k, off in enumerate((0.0, 0.5)):
            tt0 = t0 + off * beat + (0.04 * beat if off else 0)
            if e < 0.6 and k == 1:
                continue
            note = tones[(i * 2 + k) % 3] + (12 if (i + k) % 4 == 3 else 0) + 12
            pluck(tt0, note, 0.3, 0.09 * min(1.2, e))
        if e >= 0.9:
            for k in range(4):
                a = int((t0 + k * beat / 4) * SR)
                m = min(len(shk), n - a)
                if m > 0:
                    out[a:a + m] += shk[:m] * (0.05 if k % 2 == 0 else 0.03)
    return out


# ------------------------------------------------------------------ the mix
def mix(duration, voice_items, sfx_events, scene_spans, path):
    """voice_items: [(start_seconds, wav_path)], sfx_events: [(t, kind, gain)]. Writes a stereo 48 kHz WAV."""
    n = int(duration * SR)
    voice = np.zeros(n)
    for start, p in voice_items:
        x, sr = sf.read(p, dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        x = signal.resample_poly(x, SR, sr)
        x = highpass(x, 70)
        act = x[np.abs(x) > 0.02 * (np.abs(x).max() or 1.0)]            # level every voice the same: real recordings vs the narrator
        if len(act):
            x = x * min(30.0, 0.11 / (np.sqrt(np.mean(act ** 2)) + 1e-6))
        a = int(start * SR)
        m = min(len(x), n - a)
        if m > 0:
            voice[a:a + m] += x[:m]
    peak = np.abs(voice).max() or 1.0
    voice = voice / peak * 0.85
    # gentle compression: pull loud peaks down, bring quiet words up
    voice = np.tanh(voice * 1.6) / np.tanh(1.6)

    bed = music(duration, scene_spans)
    # duck the bed while the voice speaks (smoothed voice level -> gain)
    env = np.clip(lowpass(np.abs(voice), 8, 1) / 0.12, 0, 1)
    duck = 1.0 - 0.62 * env
    bed = bed * duck * 0.30

    fxm = np.zeros(n)
    for t0, kind, gain in sfx_events:
        s = fx(kind)
        a = int(max(0, t0) * SR)
        m = min(len(s), n - a)
        if m > 0:
            fxm[a:a + m] += s[:m] * gain * 0.55

    mono = voice * 1.0 + bed + fxm
    # stereo: widen the bed and effects a little
    L = mono + 0.04 * np.roll(bed, 480)
    R = mono + 0.04 * np.roll(bed, -480)
    st = np.stack([L, R], axis=1)
    st = np.tanh(st * 0.9) / np.tanh(0.9)
    st = st / (np.abs(st).max() or 1.0) * 0.89
    fade = int(0.6 * SR)
    st[-fade:] *= np.linspace(1, 0, fade)[:, None]
    sf.write(path, st.astype(np.float32), SR)
    return path
