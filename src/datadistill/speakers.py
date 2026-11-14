"""Speaker diarization: who spoke when, from the voice only (works with cameras off).

ECAPA speaker embeddings (SpeechBrain, Apache-2.0, local) on short windows inside speech,
agglomerative clustering (cosine), small clusters merged, labels Speaker 1..N by first appearance.
Every word is assigned to a speaker; transcript lines are split where the speaker changes.
"""
import threading

import numpy as np

from . import diarization
from .config import ROOT

SR = 16000
WIN_S = 1.5
HOP_S = 0.75
CLUSTER_DIST = 0.62     # cosine distance threshold (average linkage), common default for ECAPA; not tuned to any sample
STABILITY = True        # word-level evidence + voice continuity layer (speaker_stability.py)
MIN_SPEAKER_S = 15.0    # clusters with less speech are merged into their nearest neighbour

_enc = None
_lock = threading.Lock()


def release():
    """Let go of the voice model (GPU memory) after a recording has been processed."""
    global _enc
    with _lock:
        _enc = None


def encoder():
    global _enc
    with _lock:
        if _enc is None:
            import torch
            from speechbrain.inference.speaker import EncoderClassifier
            from speechbrain.utils.fetching import LocalStrategy
            _enc = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb", savedir=str(ROOT / ".models" / "spkrec-ecapa-voxceleb"),
                run_opts={"device": "cuda:0" if torch.cuda.is_available() else "cpu"},
                local_strategy=LocalStrategy.COPY)  # no symlinks on Windows without admin rights
        return _enc


def _windows(words, duration):
    """Analysis windows covering speech only (word timestamps from ASR)."""
    spans = []
    for w in words:
        if spans and w["start"] - spans[-1][1] < 0.6:
            spans[-1][1] = max(spans[-1][1], w["end"])
        else:
            spans.append([w["start"], w["end"]])
    wins = []
    for a, b in spans:
        if b - a < 0.8:
            continue
        t = a
        while t + WIN_S <= b + 0.25:
            wins.append((t, min(t + WIN_S, duration)))
            t += HOP_S
        if not wins or wins[-1][1] < b - 0.4:  # tail
            wins.append((max(a, b - WIN_S), b))
    return wins


def embed(audio, wins, progress=None):
    import torch
    enc = encoder()
    out = []
    n = int(WIN_S * SR)
    batch = 64
    for i in range(0, len(wins), batch):
        chunk = []
        for a, b in wins[i:i + batch]:
            x = audio[int(a * SR):int(a * SR) + n]
            if len(x) < n:
                x = np.pad(x, (0, n - len(x)))
            chunk.append(x)
        with torch.no_grad():
            e = enc.encode_batch(torch.from_numpy(np.stack(chunk)).to(enc.device)).squeeze(1)
        out.append(torch.nn.functional.normalize(e, dim=1).cpu().numpy())
        if progress:
            progress(min(1.0, (i + batch) / len(wins)))
    return np.concatenate(out) if out else np.zeros((0, 192))


def _merge_closest(emb, lab):
    ids = list(np.unique(lab))
    cent = {c: emb[lab == c].mean(0) / np.linalg.norm(emb[lab == c].mean(0)) for c in ids}
    best, pair = -2.0, None
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            s = float(cent[a] @ cent[b])
            if s > best:
                best, pair = s, (a, b)
    lab[lab == pair[1]] = pair[0]
    return lab


def cluster(emb, wins, max_speakers=None):
    from sklearn.cluster import AgglomerativeClustering
    if len(emb) < 2:
        return np.zeros(len(emb), int)
    lab = AgglomerativeClustering(n_clusters=None, distance_threshold=CLUSTER_DIST, metric="cosine",
                                  linkage="average").fit_predict(emb)
    lab = _merge_small(emb, lab)  # first: fragments with too little speech join their nearest real voice
    # one person's voice often ends up in several clusters (through the room speaker and through Teams, early and
    # late in a long meeting): their average voices are almost the same, while different people stay far apart
    lab = merge_same_voice(emb, lab)
    # then: more voices than people visible in the meeting (+ margin): merge the most similar real voices.
    # (Capping before removing fragments lets one cluster swallow everyone.)
    if max_speakers:
        while len(np.unique(lab)) > max_speakers:
            lab = _merge_closest(emb, lab)
    return lab


SAME_VOICE_DIST = 0.22   # average voices closer than this are one person. Measured on a 2.5 h hybrid Teams
                         # meeting: one presenter split into 5 clusters at 0.12-0.18 apart; a video narrator was 0.29 from the host


def merge_same_voice(emb, lab, limit=SAME_VOICE_DIST):
    """Merge clusters whose average (centroid) voices are closer than `limit` (cosine distance), closest first.
    Average linkage over 1.5 s windows keeps them apart because single windows vary a lot (~0.4 from their centre)."""
    lab = lab.copy()
    while True:
        ids = list(np.unique(lab))
        if len(ids) < 2:
            return lab
        cent = {c: emb[lab == c].mean(0) for c in ids}
        cent = {c: v / (np.linalg.norm(v) or 1.0) for c, v in cent.items()}
        best, pair = limit, None
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                d = 1.0 - float(cent[a] @ cent[b])
                if d < best:
                    best, pair = d, (a, b)
        if pair is None:
            return lab
        a, b = pair
        keep, drop = (a, b) if (lab == a).sum() >= (lab == b).sum() else (b, a)
        lab[lab == drop] = keep


def _merge_small(emb, lab):
    while True:
        ids = np.unique(lab)
        dur = {c: HOP_S * (lab == c).sum() for c in ids}
        small = [c for c in ids if dur[c] < MIN_SPEAKER_S]
        big = [c for c in ids if dur[c] >= MIN_SPEAKER_S]
        if not small or not big:
            break
        cent = {c: emb[lab == c].mean(0) for c in ids}
        c = min(small, key=lambda c: dur[c])
        tgt = max(big, key=lambda b: float(cent[c] @ cent[b]) / (np.linalg.norm(cent[c]) * np.linalg.norm(cent[b])))
        lab[lab == c] = tgt
    return lab


def diarize(audio, words, duration, progress=None, max_speakers=None):
    """Returns per-word speaker ids ('S1'..), per-word confidence, and speaker stats."""
    wins = _windows(words, duration)
    if not wins:
        return [None] * len(words), [None] * len(words), {}
    emb = embed(audio, wins, progress)
    lab = cluster(emb, wins, max_speakers)
    # order speakers by first appearance
    first = {}
    for (a, _), c in zip(wins, lab):
        first.setdefault(c, a)
    order = {c: f"S{i + 1}" for i, c in enumerate(sorted(first, key=first.get))}
    cent = {c: emb[lab == c].mean(0) / np.linalg.norm(emb[lab == c].mean(0)) for c in order}
    centers = np.array([(a + b) / 2 for a, b in wins])

    spk, conf = [], []
    for w in words:
        mid = (w["start"] + w["end"]) / 2
        j = int(np.argmin(np.abs(centers - mid)))
        if abs(centers[j] - mid) > 2.5:
            spk.append(None)
            conf.append(None)
            continue
        # vote among windows overlapping the word (smoother than a single window)
        near = np.flatnonzero(np.abs(centers - mid) <= WIN_S / 2 + 0.1)
        votes = {}
        for k in near if len(near) else [j]:
            votes[lab[k]] = votes.get(lab[k], 0) + 1
        c = max(votes, key=votes.get)
        spk.append(order[c])
        conf.append(round(float(np.clip(emb[j] @ cent[c], 0, 1)), 3))
    stats = {order[c]: {"windows": int((lab == c).sum())} for c in order}
    if STABILITY:
        # Voice continuity and per-word evidence (speaker_stability, ported from the friend's branch): a voice needs a
        # clean stretch to count as a speaker, a pause is not a new person, a word the voice model cannot place keeps
        # its uncertainty (word["attribution"]) while the surrounding voice is shown as its context.
        from . import speaker_stability
        timeline = diarization.timeline_from_windows(wins, [order[c] for c in lab], HOP_S)
        contexts, diag = speaker_stability.annotate(words, timeline)
        spk = contexts
        stats["stability"] = {"word_stats": diag["word_stats"], "tentative_clusters": len(diag["tentative_clusters"]),
                              "supported_voices": len(diag["raw_to_voice"]), "policy": diag["policy"]}
    spk = _smooth(spk)
    spk = snap_changes(words, spk)
    spk = _absorb_tiny(spk)
    return spk, conf, stats


PAUSE_S = 0.25        # a speaker change needs a pause of at least this long before the word...
SNAP_WORDS = 8        # ...or a sentence end; otherwise it moves to the nearest such point within 8 words / 3 s
SNAP_S = 3.0
MERGE_WORDS = 8       # with no such point nearby, a fragment of up to 8 words joins the neighbouring speaker


MIN_SPEAKER_WORDS = 12   # a "voice" left with fewer words after snapping is a fragment of its neighbours


def _absorb_tiny(spk):
    from collections import Counter
    count = Counter(s for s in spk if s)
    tiny = {s for s, c in count.items() if c < MIN_SPEAKER_WORDS}
    if not tiny or len(count) - len(tiny) < 1:
        return spk
    out = list(spk)
    for i, s in enumerate(out):
        if s in tiny:
            left = next((out[j] for j in range(i - 1, -1, -1) if out[j] and out[j] not in tiny), None)
            right = next((spk[j] for j in range(i + 1, len(spk)) if spk[j] and spk[j] not in tiny), None)
            out[i] = left or right
    return out


def snap_changes(words, spk):
    """People hand over at pauses and sentence ends, not in the middle of a phrase. The voice model decides per
    1.5 s window, and one person's voice drifting across the threshold flips the label mid-sentence
    ("Alita will need some | slots as well"). Changes are moved to the nearest pause or sentence end."""
    n = len(words)
    if n < 2:
        return spk

    def allowed(i):          # may a new speaker start at word i?
        if i <= 0 or i >= n:
            return True
        gap = (words[i].get("start") or 0) - (words[i - 1].get("end") or 0)
        return gap >= PAUSE_S or (words[i - 1].get("word") or "").strip().endswith((".", "?", "!"))

    out = list(spk)
    i = 1
    while i < n:
        if out[i] == out[i - 1] or out[i] is None or out[i - 1] is None or allowed(i):
            i += 1
            continue
        a = i - 1                       # run boundaries around the change at i
        while a > 0 and out[a - 1] == out[i - 1]:
            a -= 1
        b = i
        while b + 1 < n and out[b + 1] == out[i]:
            b += 1
        t = words[i].get("start") or 0
        cands = [j for j in range(max(a + 1, i - SNAP_WORDS), min(b, i + SNAP_WORDS) + 1)
                 if j != i and allowed(j) and abs((words[j].get("start") or 0) - t) <= SNAP_S]
        if cands:
            j = min(cands, key=lambda j: (abs(j - i), j))
            if j < i:
                for k in range(j, i):
                    out[k] = out[i]
            else:
                for k in range(i, j):
                    out[k] = out[i - 1]
        elif b + 1 < n and out[b + 1] == out[i - 1] and not allowed(b + 1) and b - i + 1 <= 2 * MERGE_WORDS:
            for k in range(i, b + 1):            # A | B | A, both changes mid-phrase: B is A's voice drifting
                out[k] = out[i - 1]
        else:
            left, right = i - a, b - i + 1
            if min(left, right) <= MERGE_WORDS:
                if left <= right:
                    for k in range(a, i):
                        out[k] = out[i]
                else:
                    for k in range(i, b + 1):
                        out[k] = out[i - 1]
        i += 1
    return out


def attribution_summary(turns):
    """How sure the voice of each line is, from the word evidence (speaker_stability): `supported` = every word has strict
    voice evidence for the line's speaker; `contextual` = the line is shown under the surrounding voice but some words
    have no strict evidence (timing at a boundary, a pause, a possible interruption); `unresolved` = no speaker."""
    for t in turns:
        ws = t.get("words") or []
        uncertain = [w for w in ws if (w.get("attribution") or {}).get("speaker") != t.get("speaker") or not t.get("speaker")]
        t["attribution"] = {"method": "voice_context_with_word_evidence",
                            "status": "contextual" if t.get("speaker") and uncertain else "supported" if t.get("speaker") else "unresolved",
                            "uncertain_word_count": len(uncertain), "words": len(ws)}
    return turns


def _smooth(spk, min_run=3):
    """Remove speaker flicker: runs shorter than min_run words take the neighbouring speaker."""
    out = list(spk)
    i = 0
    while i < len(out):
        j = i
        while j < len(out) and out[j] == out[i]:
            j += 1
        if out[i] is not None and j - i < min_run and (i > 0 or j < len(out)):
            fill = out[i - 1] if i > 0 else out[j] if j < len(out) else out[i]
            for k in range(i, j):
                out[k] = fill
        i = j
    return out


def to_turns(segments, words_spk, words_conf):
    """Split ASR segments where the speaker changes. segments carry their words in order."""
    turns = []
    k = 0
    for seg in segments:
        ws = seg.get("words") or []
        if not ws:
            turns.append({**seg, "speaker": None})
            continue
        group = None
        for w in ws:
            s, c = words_spk[k], words_conf[k]
            k += 1
            if group is None or s != group["speaker"]:
                if group:
                    turns.append(_close(group, seg))
                group = {"speaker": s, "words": [], "spk_conf": []}
            group["words"].append(w)
            if c is not None:
                group["spk_conf"].append(c)
        turns.append(_close(group, seg))
    return [t for t in turns if t["text"]]


MAX_WORD_S = 1.2  # the speech model sometimes stretches a phrase's last word over the following silence


def _close(g, seg):
    ws = [{**w, "end": min(w["end"], w["start"] + MAX_WORD_S)} for w in g["words"]]
    probs = [w.get("probability") for w in ws if w.get("probability") is not None]
    return {"start": round(ws[0]["start"], 2), "end": round(ws[-1]["end"], 2),
            "text": "".join(w["word"] for w in ws).strip(), "speaker": g["speaker"],
            "confidence": round(float(np.mean(probs)), 3) if probs else seg.get("confidence"),
            "speaker_confidence": round(float(np.mean(g["spk_conf"])), 3) if g["spk_conf"] else None,
            "avg_logprob": seg.get("avg_logprob"), "no_speech_prob": seg.get("no_speech_prob"), "words": ws}
