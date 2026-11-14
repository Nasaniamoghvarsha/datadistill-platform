"""Same recording, different file: re-encoded, re-exported or re-compressed copies of a meeting.

Their bytes (SHA-256) differ, so the exact duplicate check cannot see them. Two signals together:
  1. picture  one 64-bit dHash per second (the fingerprint every processed recording keeps, also after
              its video is deleted); >= 90 % of seconds within 8 bits, length within 1 %, up to 3 s shift
  2. speech   two 30 s pieces of the new file are transcribed and compared with the existing transcript at
              the same time: same meeting -> nearly the same words. This rules out different meetings
              that merely look alike (the same team in the same gallery layout, a static title slide).
If the existing recording has no speech (or the new one has no audio), the picture must match >= 97 %.
Runs right after the 1 fps frames are extracted, before the expensive stages: a copy is not transcribed again.
"""
import json
import re

import cv2

from . import db, media, transcribe
from .config import DATA_DIR, OUTPUT_DIR

MIN_FRAMES = 0.90
BITS = 8
MAX_OFFSET = 3
DURATION_TOL = 0.01
VISUAL_ONLY_MIN = 0.97
WINDOWS = (0.30, 0.65)    # where the speech pieces are taken (share of the duration)
WINDOW_S = 30.0
MIN_WORDS = 6             # a piece with fewer content words is not used
MIN_SPEECH = 0.60         # share of the new piece's content words found in the existing transcript
REDUCE = cv2.IMREAD_REDUCED_GRAYSCALE_8


class SameContent(Exception):
    def __init__(self, match):
        super().__init__(f"same recording as file {match['id']}")
        self.match = match


def fingerprint(frames):
    """Same format as the stored fingerprint.json: dHash of a 9x8 grey thumbnail per second."""
    out = []
    for p in frames:
        g = cv2.imread(str(p), REDUCE)
        if g is None:
            continue
        s = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
        out.append(int("".join("1" if b else "0" for b in (s[:, 1:] > s[:, :-1]).flatten()), 2))
    return out


def frame_similarity(ha, hb, max_offset=MAX_OFFSET):
    """(share of seconds that match, shift in seconds: new second i ~ existing second i + shift)."""
    best, best_off = 0.0, 0
    for off in range(-max_offset, max_offset + 1):
        pairs = [(ha[i], hb[i + off]) for i in range(len(ha)) if 0 <= i + off < len(hb)]
        if pairs:
            s = sum(bin(x ^ y).count("1") <= BITS for x, y in pairs) / max(len(ha), len(hb))
            if s > best:
                best, best_off = s, off
    return best, best_off


def _candidates(file_id, duration):
    for f in db.q("SELECT id, name, system, meta FROM files WHERE kind='video' AND status='processed' AND id != ?", (file_id,)):
        p = OUTPUT_DIR / "recordings" / str(f["id"]) / "fingerprint.json"
        if not p.exists():
            continue
        fp = json.loads(p.read_text())
        if abs(fp["duration"] - duration) <= max(1.0, DURATION_TOL * max(fp["duration"], duration)):
            yield f, fp


WORD = re.compile(r"[a-zà-ÿ0-9']+")
STOP = set("the and that this with have from they them then there what when were will would your you are was for not but "
           "can just like yeah okay so um uh our its it's i'm we're".split())


def _words(text):
    return [w for w in WORD.findall((text or "").lower()) if len(w) >= 3 and w not in STOP]


def _speech_check(path, master_id, duration, offset):
    """Per piece: share of the new piece's content words that appear in the existing transcript there."""
    out = []
    for frac in WINDOWS:
        t0 = duration * frac
        audio = media.load_audio(path, start=t0, duration=WINDOW_S)
        if len(audio) < 16000 * 5:
            continue
        segs, _ = transcribe.transcribe(audio, WINDOW_S)
        new = _words(" ".join(s["text"] for s in segs))
        rows = db.q("SELECT text FROM segments WHERE file_id=? AND end >= ? AND start <= ?",
                    (master_id, t0 + offset - 5, t0 + offset + WINDOW_S + 5))
        old = set(_words(" ".join(r["text"] for r in rows)))
        if len(new) < MIN_WORDS or len(old) < MIN_WORDS:
            continue
        out.append(round(sum(w in old for w in new) / len(new), 3))
    return out


def find(file_id, fp, duration, path, has_audio, log=print):
    """The existing recording this file is a copy of, or None."""
    best = None
    for f, cfp in _candidates(file_id, duration):
        sim, off = frame_similarity(fp, cfp.get("dhash64") or [])
        if sim >= MIN_FRAMES and (not best or sim > best["frames"]):
            best = {"id": f["id"], "name": f["name"], "folder": f["system"], "frames": round(sim, 3), "offset_s": off,
                    "original_deleted": bool(json.loads(f["meta"] or "{}").get("original_deleted"))}
    if not best:
        return None
    has_speech = db.q1("SELECT COUNT(*) n FROM segments WHERE file_id=?", (best["id"],))["n"] > 0
    speech = _speech_check(path, best["id"], duration, best["offset_s"]) if has_audio and has_speech else []
    best["speech"] = speech
    if speech:
        ok = sum(speech) / len(speech) >= MIN_SPEECH
    else:
        ok = best["frames"] >= VISUAL_ONLY_MIN
    log(f"looks like {best['folder']}/{best['name']}: {100 * best['frames']:.0f}% of seconds match, "
        f"speech check {speech or 'not possible'} -> {'same recording' if ok else 'different recording'}")
    return best if ok else None
