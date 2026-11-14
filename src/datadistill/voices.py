"""Voice samples: about 10 s of each speaker's clearest speech, kept as a small Opus file (~40 KB).

Together with the transcript and the snapshots this keeps what is needed to reconstruct the meeting
(who said what, what was on screen, how each person sounds) after the original video is deleted.
Pieces are chosen from the speaker's own turns: high recognition confidence, voice clearly matching
the speaker, steady loudness above the background, no clipping; longer pieces first.
"""
import json
from collections import defaultdict

import numpy as np

from . import db, media
from .config import DATA_DIR, FFMPEG, OUTPUT_DIR

TARGET_S = 10.0
MIN_PIECE_S = 2.0
EDGE_S = 0.15         # turn boundaries are approximate: trim a little on both sides
BITRATE = "32k"
SR = 16000


def _audio_score(seg):
    """0..1: dynamic range speech vs. pauses (clean recording), share of active frames, no clipping."""
    n = len(seg) // 800 * 800
    if n < 800 * 8:
        return 0.0
    fr = seg[:n].reshape(-1, 800)  # 50 ms frames
    lvl = 20 * np.log10(np.sqrt((fr ** 2).mean(1)) + 1e-9)
    hi, lo = np.percentile(lvl, 90), np.percentile(lvl, 10)
    active = float((lvl > hi - 18).mean())
    clip = float((np.abs(seg) > 0.98).mean())
    return min(1.0, (hi - lo) / 25.0) * active * max(0.0, 1.0 - 200 * clip)


def pick(turns, audio=None):
    """{speaker: [(start, end), ...]} totalling up to TARGET_S per speaker."""
    by = defaultdict(list)
    for t in turns:
        s0, s1 = (t["start"] or 0) + EDGE_S, (t["end"] or 0) - EDGE_S
        if t.get("speaker") and s1 - s0 >= 0.8:
            by[t["speaker"]].append((s0, s1, t))
    # longer pieces sound natural; a speaker with only short remarks still gets a (shorter) sample
    by = {sid: ([x for x in v if x[1] - x[0] >= MIN_PIECE_S] or v) for sid, v in by.items()}
    out = {}
    for sid, items in by.items():
        scored = []
        for s0, s1, t in items:
            q = (t.get("confidence") or 0.8) * (t.get("speaker_confidence") if t.get("speaker_confidence") is not None else 0.8)
            if audio is not None and len(audio):
                q *= _audio_score(audio[int(s0 * SR):int(s1 * SR)])
            scored.append((q * min(1.0, (s1 - s0) / 5.0), s0, s1))
        chosen, total = [], 0.0
        for q, s0, s1 in sorted(scored, reverse=True):
            if total >= TARGET_S - 0.5 or q <= 0:
                break
            s1 = min(s1, s0 + TARGET_S - total)
            if s1 - s0 >= 0.6:
                chosen.append((round(s0, 2), round(s1, 2)))
                total += s1 - s0
        if chosen:
            out[sid] = sorted(chosen)
    return out


def export(path, pieces, dst):
    """Cut the pieces from the original file, join them with short fades, encode Opus."""
    parts, labels = [], []
    for k, (s0, s1) in enumerate(pieces):
        d = s1 - s0
        parts.append(f"[0:a]atrim={s0}:{s1},asetpts=PTS-STARTPTS,afade=t=in:d=0.03,afade=t=out:st={max(0, d - 0.03):.2f}:d=0.03[a{k}]")
        labels.append(f"[a{k}]")
    fc = ";".join(parts) + f";{''.join(labels)}concat=n={len(pieces)}:v=0:a=1,{media.SPEECH_FILTER}[out]"
    dst.parent.mkdir(parents=True, exist_ok=True)
    media.run([FFMPEG, "-y", "-v", "error", "-i", str(path), "-filter_complex", fc, "-map", "[out]", "-ac", "1",
               "-c:a", "libopus", "-b:a", BITRATE, "-application", "voip", str(dst)], capture_output=True)
    return dst.stat().st_size


def delete_all():
    """Remove every voice sample (files and references). Returns the number of files and bytes removed."""
    n = size = 0
    for rec in db.q("SELECT file_id, speakers FROM recordings"):
        d = OUTPUT_DIR / "recordings" / str(rec["file_id"]) / "voices"
        if d.exists():
            for p in d.glob("*"):
                n += 1
                size += p.stat().st_size
                p.unlink()
            d.rmdir()
        sp = json.loads(rec["speakers"] or "{}")
        if any("voice" in m for m in sp.values()):
            for m in sp.values():
                m.pop("voice", None)
            db.ex("UPDATE recordings SET speakers=? WHERE file_id=?", (json.dumps(sp), rec["file_id"]))
    db.audit("delete_voice_samples", None, files=n, bytes=size)
    return {"files": n, "bytes": size}


def build(file_id, audio=None):
    """(Re)create voice samples; stored in the speakers metadata as speakers[id]['voice'].
    Returns a summary, or None if the original video is gone (existing samples are kept)."""
    f = db.file_row(file_id)
    path = DATA_DIR / f["path"]
    rec = db.q1("SELECT speakers FROM recordings WHERE file_id=?", (file_id,))
    if not rec or not path.exists():
        return None
    if not db.setting("keep_voice_samples"):  # voices are personal data: switched off -> none kept for this recording
        vd = OUTPUT_DIR / "recordings" / str(file_id) / "voices"
        if vd.exists():
            for p in vd.glob("*"):
                p.unlink()
            vd.rmdir()
        sp = json.loads(rec["speakers"] or "{}")
        for m in sp.values():
            m.pop("voice", None)
        db.ex("UPDATE recordings SET speakers=? WHERE file_id=?", (json.dumps(sp), file_id))
        return {"speakers": 0, "bytes": 0, "disabled": True}
    if audio is None:
        audio = media.load_audio(path)
    turns = db.q("SELECT start, end, speaker, confidence, speaker_confidence FROM segments WHERE file_id=?", (file_id,))
    pieces = pick(turns, audio)
    base = OUTPUT_DIR / "recordings" / str(file_id)
    for old in (base / "voices").glob("*.ogg") if (base / "voices").exists() else []:
        old.unlink()
    sp = json.loads(rec["speakers"] or "{}")
    total = 0
    for sid, meta in sp.items():
        meta.pop("voice", None)
        if sid in pieces:
            size = export(path, pieces[sid], base / "voices" / f"{sid}.ogg")
            meta["voice"] = {"file": f"voices/{sid}.ogg", "seconds": round(sum(b - a for a, b in pieces[sid]), 1),
                             "bytes": size, "clips": pieces[sid]}
            total += size
    db.ex("UPDATE recordings SET speakers=? WHERE file_id=?", (json.dumps(sp), file_id))
    return {"speakers": len([m for m in sp.values() if m.get("voice")]), "bytes": total}
