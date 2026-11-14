"""Voice timeline helpers used by speaker_stability.

The friend's branch of DataDistill provides `speaker_stability.py` and `identity.py` but not this module; it is written
here from what those two files and their tests require:

  _union_length(intervals)         seconds covered by a set of (start, end) intervals (overlaps counted once)
  assignment(a, b, timeline)       which voice(s) the interval [a, b] (a word) belongs to, and how sure that is

A timeline is a list of {"start", "end", "speaker"} intervals of raw voice clusters. Two intervals of DIFFERENT clusters
that overlap in time mean two voices at once, so a timeline built from overlapping analysis windows must be tiled first
(timeline_from_windows) or every speaker change would look like overlap.
"""
from bisect import bisect_left, bisect_right
from collections import defaultdict

MIN_SHARE = 0.5          # a voice must cover at least this share of a word to be its speaker
MIN_CANDIDATE = 0.25     # ... and a voice covering at least this share is a competing candidate


def _union_length(intervals):
    total, stop = 0.0, None
    for a, b in sorted((a, b) for a, b in intervals if b > a):
        if stop is None or a > stop:
            total += b - a
            stop = b
        elif b > stop:
            total += b - stop
            stop = b
    return total


def _clip(rows, a, b):
    return [(max(t["start"], a), min(t["end"], b)) for t in rows if min(t["end"], b) > max(t["start"], a)]


_INDEX = {}


def _near(timeline, a, b):
    """Intervals that can overlap [a, b]. Long timelines (a 2 h meeting has thousands of intervals, and every word asks)
    are indexed once by start time."""
    if len(timeline) < 64:
        return timeline
    idx = _INDEX.get("idx")
    if idx is None or idx[3] is not timeline or idx[4] != len(timeline):       # the cache holds the list itself: no id reuse
        rows = sorted(timeline, key=lambda t: t["start"])
        idx = _INDEX["idx"] = (rows, [t["start"] for t in rows], max(t["end"] - t["start"] for t in rows), timeline, len(timeline))
    rows, starts, longest = idx[0], idx[1], idx[2]
    return rows[bisect_left(starts, a - longest):bisect_right(starts, b)]


def assignment(a, b, timeline, min_share=MIN_SHARE, min_candidate=MIN_CANDIDATE):
    """Evidence for the interval [a, b]:
      speaker      the voice that covers at least `min_share` of it and has no competitor, else None
      candidates   every voice covering at least `min_candidate` of it: [{"speaker", "share"}], strongest first
      status       clear | partial (one voice, too little of the word) | overlap (two voices at the same moment)
                   | multiple_speakers (two voices one after the other inside the word) | no_voice_evidence
    """
    dur = max(b - a, 1e-6)
    rows = defaultdict(list)
    for t in _near(timeline, a, b):
        rows[t["speaker"]].append(t)
    covered = {sp: _clip(r, a, b) for sp, r in rows.items()}
    share = {sp: _union_length(iv) / dur for sp, iv in covered.items() if iv}
    cands = sorted(((sp, s) for sp, s in share.items() if s >= min_candidate), key=lambda x: -x[1])
    out = {"speaker": None, "status": "no_voice_evidence", "candidates": [{"speaker": sp, "share": round(s, 3)} for sp, s in cands]}
    if not cands:
        return out
    if len(cands) >= 2:
        first, second = covered[cands[0][0]], covered[cands[1][0]]
        simultaneous = any(min(e1, e2) > max(s1, s2) for s1, e1 in first for s2, e2 in second)
        out["status"] = "overlap" if simultaneous else "multiple_speakers"
        return out
    sp, s = cands[0]
    if s >= min_share:
        out.update(speaker=sp, status="clear")
    else:
        out["status"] = "partial"
    return out


def timeline_from_windows(windows, labels, hop):
    """Raw voice timeline from analysis windows (start, end) with a cluster label each. Windows overlap one another, so
    each contributes only its middle `hop` seconds: neighbours tile the time axis and never overlap. Neighbouring
    pieces of one voice are joined; gaps (silence, music) stay gaps."""
    pieces = []
    for (a, b), lab in zip(windows, labels):
        mid = (a + b) / 2
        pieces.append({"start": mid - hop / 2, "end": mid + hop / 2, "speaker": lab})
    pieces.sort(key=lambda t: t["start"])
    out = []
    for p in pieces:
        if out and out[-1]["speaker"] == p["speaker"] and p["start"] - out[-1]["end"] <= 0.1:
            out[-1]["end"] = max(out[-1]["end"], p["end"])
        else:
            out.append(dict(p))
    return out
