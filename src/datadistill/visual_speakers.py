"""Fuse what the picture says about who is speaking with the voices found in the audio.

Inputs (all optional; each is used only when the video provides it):
  * lit name labels   {name: {second: 0..1}}     (highlight.py)       -> exclusive: one lit at a time
  * camera tiles      {tile id: {...activity}}   (faces.py)           -> mouth movement per second
Outputs:
  * camera_matches    voice -> the camera tile (or face in a room camera) whose mouth moves when that voice speaks
  * same_face_pairs   voices that both fit the same face: one person whose voice was split in two
  * overlap_seconds   seconds where two people are visibly speaking at once
  * mark_overlap      adds `overlap_with` to the transcript lines inside those seconds

Nothing here identifies a person by their face: a tile is a position in the picture and a series of numbers.
"""
from collections import defaultdict

import numpy as np

MIN_EVIDENCE_S = 20        # seconds of a voice speaking while its candidate tile is readable
MIN_SCORE = 0.5            # standard deviations by which the tile moves more during this voice than during the others
MIN_MARGIN = 0.25          # ...and better than the runner-up by this much


def speaking_seconds(turns):
    out = defaultdict(set)
    for t in turns:
        if t.get("speaker"):
            for s in range(int(t["start"]), int(np.ceil(t["end"]))):
                out[t["speaker"]].add(s)
    return out


def room_tracks(tile, tol=0.07):
    """A room camera shows several people: split its faces into tracks by horizontal position (the camera is fixed)
    -> {track id: {second: mouth activity}}. `tile["faces"]` is {second: [[(x, jaw), ...] per frame]}."""
    pts = [(x, sec) for sec, frames in tile.get("faces", {}).items() for fr in frames for x, _ in fr]
    if not pts:
        return {}
    xs = sorted(x for x, _ in pts)
    centres = [[xs[0]]]
    for x in xs[1:]:
        if x - centres[-1][-1] <= tol:
            centres[-1].append(x)
        else:
            centres.append([x])
    centres = [float(np.mean(c)) for c in centres if len(c) >= max(8, 0.03 * len(xs))]
    tracks = defaultdict(lambda: defaultdict(list))
    for sec, frames in tile["faces"].items():
        for fr in frames:
            for x, jaw in fr:
                k = int(np.argmin([abs(x - c) for c in centres])) if centres else 0
                if centres and abs(x - centres[k]) <= tol:
                    tracks[k][int(sec)].append(jaw)
    out = {}
    for k, secs in tracks.items():
        act = {}
        for sec, jaws in secs.items():
            if len(jaws) >= 2:
                act[sec] = float(np.abs(np.diff(jaws)).sum() + 0.25 * np.mean(jaws))
        if len(act) >= MIN_EVIDENCE_S:
            out[f"{k + 1}"] = act
    return out


def entities(face_tiles):
    """Camera entities without a name: unlabelled tiles with a readable face, and each face of a room camera.
    -> {entity id: {second: activity}}"""
    ents = {}
    for tid, tl in face_tiles.items():
        if tl["kind"] == "room":
            for k, act in room_tracks(tl).items():
                ents[f"{tid}/face{k}"] = {"activity": act, "room": True, "tile": tid}
        elif tl["name"] is None and tl["kind"] == "live":
            ents[tid] = {"activity": {int(s): a for s, a in tl["activity"].items()}, "room": False, "tile": tid}
    return ents


def _z(series):
    if not series:
        return {}
    v = np.array(list(series.values()))
    sd = v.std() or 1.0
    return {k: (x - v.mean()) / sd for k, x in series.items()}


def camera_matches(turns, ents, min_overlap_s=MIN_EVIDENCE_S, min_score=MIN_SCORE, min_margin=MIN_MARGIN):
    """{voice: {"entity", "score", "margin", "room"}}: the camera tile / room face whose mouth moves when that voice
    speaks and not when the others do. One face per voice and one voice per face (Hungarian assignment)."""
    speaking = speaking_seconds(turns)
    voices = sorted(speaking, key=lambda s: int(s[1:]))
    ids = list(ents)
    if len(voices) < 1 or not ids:
        return {}
    S = np.full((len(voices), len(ids)), np.nan)
    zs = {e: _z(ents[e]["activity"]) for e in ids}
    for i, v in enumerate(voices):
        others = set().union(*(speaking[o] for o in voices if o != v)) - speaking[v] if len(voices) > 1 else set()
        for j, e in enumerate(ids):
            a = [zs[e][s] for s in speaking[v] if s in zs[e]]
            b = [zs[e][s] for s in others if s in zs[e]]
            if len(a) >= min_overlap_s and len(b) >= min_overlap_s:
                S[i, j] = float(np.mean(a) - np.mean(b))
    from scipy.optimize import linear_sum_assignment
    rows, cols = linear_sum_assignment(-np.nan_to_num(S, nan=-9))
    out = {}
    for i, j in zip(rows, cols):
        sc = S[i, j]
        if np.isnan(sc) or sc < min_score:
            continue
        row = np.sort(np.nan_to_num(S[i], nan=-9))[::-1]
        margin = sc - (row[1] if len(row) > 1 else -9)
        if margin < min_margin:
            continue
        out[voices[i]] = {"entity": ids[j], "score": round(float(sc), 2), "margin": round(float(margin), 2),
                          "room": ents[ids[j]]["room"], "tile": ents[ids[j]]["tile"]}
    return out


def same_face_pairs(turns, ents, min_score=0.8, min_overlap_s=MIN_EVIDENCE_S):
    """[(voice a, voice b, entity)] where BOTH voices individually fit the same face strongly and never speak at once."""
    speaking = speaking_seconds(turns)
    voices = sorted(speaking, key=lambda s: int(s[1:]))
    best = defaultdict(list)
    for e, d in ents.items():
        z = _z(d["activity"])
        for v in voices:
            others = set().union(*(speaking[o] for o in voices if o != v)) - speaking[v]
            a = [z[s] for s in speaking[v] if s in z]
            b = [z[s] for s in others if s in z]
            if len(a) >= min_overlap_s and len(b) >= min_overlap_s and np.mean(a) - np.mean(b) >= min_score:
                best[e].append(v)
    pairs = []
    for e, vs in best.items():
        for i in range(len(vs)):
            for j in range(i + 1, len(vs)):
                a, b = vs[i], vs[j]
                if not (speaking[a] & speaking[b]):
                    pairs.append((a, b, e))
    return pairs


# ------------------------------------------------------------------ two people at once
MAX_MARKED_SHARE = 0.12      # a meeting where more than this share of lines "overlap" has a faulty signal, not a lot of overlap


def _runs_only(secs, min_run):
    """Keep only seconds that belong to a stretch of at least `min_run` consecutive seconds (one odd second is noise)."""
    keep, run = set(), []
    for t in sorted(secs) + [None]:
        if run and (t is None or t != run[-1] + 1):
            if len(run) >= min_run:
                keep.update(run)
            run = []
        if t is not None:
            run.append(t)
    return keep


def overlap_seconds(chip_series, face_tiles=None, min_share=0.6, min_tiles=2, lip_pctl=85, min_run=2):
    """{second: [names or tile ids]} where at least two people are visibly speaking at the same time, for at least
    `min_run` seconds in a row: two name labels lit together (the series must already have passed highlight.plausible),
    or two real camera tiles (live video, not a photo) whose mouth activity is in the top 15% of their own range."""
    cand = defaultdict(set)
    lit = defaultdict(list)
    for name, d in (chip_series or {}).items():
        for sec, f in d.items():
            if f >= min_share:
                lit[sec].append(name)
    for sec, names in lit.items():
        if len(names) >= min_tiles:
            cand[sec].update(names)
    if face_tiles:
        high = defaultdict(list)
        for tid, tl in face_tiles.items():
            if tl["kind"] != "live" or tl.get("motion", 0) < 1.5 or not tl["activity"]:
                continue                                            # a photo or a nearly still picture has no mouth to read
            thr = max(0.15, float(np.percentile(list(tl["activity"].values()), lip_pctl)))
            for sec, a in tl["activity"].items():
                if a >= thr:
                    high[int(sec)].append(tl["name"] or tid)
        for sec, names in high.items():
            if len(names) >= min_tiles:
                cand[sec].update(names)
    keep = _runs_only(set(cand), min_run)
    return {s: sorted(cand[s]) for s in keep}


def mark_overlap(turns, secs_by_name, min_seconds=2):
    """Adds `overlap_with` (names of the others who speak at once) to a line only when the line's OWN speaker is one of
    the people seen speaking in at least `min_seconds` of its seconds: a line is never marked because of two other
    people. Music / video sound is never marked. A transcript of overlapping speech is incomplete: the speech model
    writes the louder voice. Returns the number of lines marked."""
    n = 0
    for t in turns:
        me = t.get("speaker_name")
        if not me or t.get("speaker") == "S0":
            continue
        secs = [s for s in range(int(t["start"]), int(np.ceil(t["end"]))) if s in secs_by_name and me in secs_by_name[s]]
        if len(secs) >= min_seconds:
            others = sorted({x for s in secs for x in secs_by_name[s] if x != me})
            if others:
                t["overlap_with"] = others
                n += 1
    return n


# ------------------------------------------------------------------ one camera, several people
def shared_camera(turns, lit_secs, total_secs, min_voice_share=0.7, min_in_speech=0.75, min_lift=0.25):
    """A name label that is lit almost all the time (highlight.dominant_label): does it follow SPEECH? It must be lit while
    people talk (at least `min_in_speech` of the speech seconds) and clearly less in the silence (`min_lift` lower), or it is
    just a coloured background. If it does, every voice that speaks mostly while it is lit speaks through that tile.
    -> {"voices": {voice: share}, "lit_in_speech", "lit_in_silence"} or None."""
    lit = set(lit_secs)
    speaking = speaking_seconds(turns)
    speech = set().union(*speaking.values()) if speaking else set()
    if not speech:
        return None
    in_speech = len(lit & speech) / len(speech)
    silence = max(1, total_secs - len(speech))
    in_silence = len(lit - speech) / silence
    if in_speech < min_in_speech or in_speech - in_silence < min_lift:
        return None
    voices = {sid: len(secs & lit) / len(secs) for sid, secs in speaking.items() if secs}
    voices = {sid: round(v, 3) for sid, v in voices.items() if v >= min_voice_share}
    return {"voices": voices, "lit_in_speech": round(in_speech, 3), "lit_in_silence": round(in_silence, 3)} if voices else None


def camera_of_turn(turn, lit_secs, min_share=0.7):
    """True if the tile was lit for most of the seconds of this line (so it was said through that camera)."""
    secs = list(range(int(turn["start"]), int(np.ceil(turn["end"])))) or [int(turn["start"])]
    return sum(1 for s in secs if s in lit_secs) / len(secs) >= min_share


# ------------------------------------------------------------------ evidence for naming
def highlight_observations(turns, series, min_share=0.5, max_others=0.2, min_run=2):
    """A voice speaks while exactly one name label is lit (learned chip colour or coloured border): one observation per
    stretch of at least `min_run` seconds, {speaker, name, kind: 'highlight', start, end}. `series` is {name: {sec: 0..1}}."""
    from . import highlight
    active = highlight.active_speaker(series, min_share=min_share, max_others=max_others)
    speaking = speaking_seconds(turns)
    out = []
    for sid, secs in speaking.items():
        by_name = defaultdict(list)
        for t in secs:
            if t in active:
                by_name[active[t]].append(t)
        for name, ts in by_name.items():
            run = []
            for t in sorted(ts) + [None]:
                if run and (t is None or t != run[-1] + 1):
                    if len(run) >= min_run:
                        out.append({"speaker": sid, "name": name, "kind": "highlight", "start": float(run[0]), "end": float(run[-1] + 1)})
                    run = []
                if t is not None:
                    run.append(t)
    return out
