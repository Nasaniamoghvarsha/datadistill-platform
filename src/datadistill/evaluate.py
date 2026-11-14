"""Accuracy against a human reference transcript.

    datadistill accuracy <file_id> <reference>      reference: .vtt / .srt (Teams style <v Name>), .json, .txt

Measures what our machine output got right, never the hand-edited text:
  * word error rate (WER): (substituted + deleted + inserted words) / reference words, after the usual
    normalisation (case, punctuation, numbers written as digits, fillers like "uh" ignored);
  * content words: share of the reference's meaningful words we have (recall) and share of ours that are right (precision);
  * who spoke when (DER, standard 0.25 s collar): missed speech + false alarm + wrong speaker, as a share of reference speech;
  * names: our speaker names compared with the reference names after matching speakers by talk time.

The reference must come from a person (or a trusted caption file), never from our own output.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from rapidfuzz.distance import Levenshtein

from . import config, db

# ------------------------------------------------------------------ text normalisation
FILLERS = {"uh", "um", "er", "erm", "ah", "eh", "hmm", "mm", "mhm", "uhm", "huh", "hm", "mmhmm", "uhhuh", "mmm"}
STOP = set("""a an the and or but if of to in on at by for with from as is are was were be been being it its this that these those
there here i you he she we they me him her us them my your his our their do does did have has had will would can could should
shall may might must not no so than then too very just also about into over out up down off what which who whom whose when where
why how all any some each both few more most other such only own same s t don isn aren wasn weren ok okay yeah yes oh well
right like""".split())

_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                                    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
# The same writing conventions on both sides: human transcribers write "gonna" and "favourite", Whisper writes
# "going to" and "favorite". Neither is a recognition error. (Same rules as the 11 Nov measurement.)
_SPELL = {"programme": "program", "programmes": "programs", "colour": "color", "colours": "colors", "centre": "center",
          "favourite": "favorite", "behaviour": "behavior", "organise": "organize", "organised": "organized",
          "realise": "realize", "recognise": "recognize", "analyse": "analyze", "travelling": "traveling",
          "grey": "gray", "labour": "labor", "okay": "ok", "kay": "ok", "o.k": "ok"}
_SPOKEN = {"gonna": "going to", "wanna": "want to", "gotta": "got to", "kinda": "kind of", "sorta": "sort of",
           "lemme": "let me", "dunno": "do not know", "cause": "because", "cos": "because"}
_CONTRACTIONS = {"i'm": "i am", "you're": "you are", "we're": "we are", "they're": "they are", "it's": "it is",
                 "that's": "that is", "there's": "there is", "what's": "what is", "let's": "let us", "i'll": "i will",
                 "we'll": "we will", "you'll": "you will", "it'll": "it will", "i've": "i have", "we've": "we have",
                 "you've": "you have", "i'd": "i would", "we'd": "we would", "don't": "do not", "doesn't": "does not",
                 "didn't": "did not", "can't": "can not", "cannot": "can not", "won't": "will not", "isn't": "is not",
                 "aren't": "are not", "wasn't": "was not", "couldn't": "could not", "wouldn't": "would not",
                 "shouldn't": "should not", "he's": "he is", "she's": "she is", "who's": "who is"}


def _numbers(ws):
    """'thirty one' -> '31', 'three hundred and five' -> '305', 'two thousand' -> '2000'. Digits stay as they are.
    Applied to the reference and to our text alike, so "eighteen percent" and "18%" count as the same words."""
    out, i = [], 0
    while i < len(ws):
        if ws[i] not in _ONES and ws[i] not in _TENS:
            out.append(ws[i])
            i += 1
            continue
        total, cur, last, j = 0, 0, None, i
        while j < len(ws):
            x = ws[j]
            if x in _ONES:
                v = _ONES[x]
                if last in ("ones", "teen") or (last == "tens" and v >= 10) or (last == "tens" and v == 0):
                    break
                cur, last = cur + v, ("teen" if v >= 10 else "ones")
            elif x in _TENS:
                if last in ("ones", "teen", "tens"):
                    break
                cur, last = cur + _TENS[x], "tens"
            elif x == "hundred" and last in ("ones", "teen"):
                cur, last = cur * 100, "hundred"
            elif x == "thousand" and last not in (None, "thousand"):
                total, cur, last = total + cur * 1000, 0, "thousand"
            elif x == "and" and last in ("hundred", "thousand") and j + 1 < len(ws) and (ws[j + 1] in _ONES or ws[j + 1] in _TENS):
                pass
            else:
                break
            j += 1
        out.append(str(total + cur))
        i = j
    return out


def words(text: str, fillers=True, join_hyphens=True) -> list[str]:
    t = (text or "").lower().replace("’", "'").replace("&", " and ").replace("%", " percent ")
    t = re.sub(r"\b((?:[a-z]_)+)", lambda m: m.group(1).replace("_", ""), t)   # AMI spelling "t_v_" -> "tv"
    t = re.sub(r"\b([a-z])\.(?=[a-z]\.)", r"\1", t)                            # "t.v." -> "tv."
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)              # 1,000 -> 1000
    t = re.sub(r"(?<=\d)\.(?!\d)", " ", t)                   # "18." -> "18"
    if join_hyphens:
        t = re.sub(r"(?<=[a-z])-(?=[a-z])", "", t)          # kick-off -> kickoff (WER: both spellings are the same word)
    t = re.sub(r"[-–—/]", " ", t)
    t = re.sub(r"[^\w\s'.]", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)
    t = re.sub(r"\b([b-hj-z])\s+(?=[b-hj-z]\b)", r"\1", t)   # spelled letters "t v" -> "tv" (not "I", "a")
    ws = []
    for w in t.split():
        w = w.strip("'")
        w = _CONTRACTIONS.get(w, w)
        w = _SPOKEN.get(w, w)
        ws += [_SPELL.get(x, x) for x in w.split()]
    if fillers:
        ws = [w for w in ws if w not in FILLERS]
    return _numbers(ws)


def timed_words(segs, fillers=True):
    """All words of all turns in time order (each word placed evenly inside its turn), so that two people talking
    at the same time cause small local reorderings, not whole sentences counted as missing and extra."""
    out = []
    for s in segs:
        ws = words(s["text"], fillers)
        dur = max(0.0, (s["end"] or 0) - (s["start"] or 0))
        out += [((s["start"] or 0) + dur * (i + 0.5) / max(1, len(ws)), w) for i, w in enumerate(ws)]
    return [w for _, w in sorted(out, key=lambda x: x[0])]


def content(ws):
    return [w for w in ws if w not in STOP and (len(w) > 2 or w.isdigit())]


# ------------------------------------------------------------------ references
_TS_LINE = re.compile(r"^(\d{1,2}:\d{2}:\d{2})\s+(?:((?:(?:Dr|Mr|Mrs|Ms|Mx|Prof|Hon|Sen|Rep)\.?\s+)?"
                      r"[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){0,3}):\s+)?(.*)$")


def _secs(ts):
    return sum(float(p) * 60 ** i for i, p in enumerate(reversed(ts.split(":"))))


def transcript_pdf(path: Path):
    """A court-reporter style transcript PDF (e.g. FDA advisory committees): lines like
    "00:18:10 Ms. Paulsen: Hi, good morning." Line numbers, page numbers and the cover pages are skipped.
    A timestamp without a name continues the previous speaker. A turn ends where the next one starts,
    or after a normal speaking time for its words if the next turn is much later."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(path))
    turns, cur, session = [], None, 0
    for page in doc:
        for line in page.get_textpage().get_text_range().splitlines():
            line = line.strip()
            if not line or line.isdigit():
                continue
            m = _TS_LINE.match(line)
            if m:
                t = _secs(m.group(1))
                if cur is not None and t < cur["start"] - 60:
                    session += 1                                  # the clock restarted (after a break)
                spk = (m.group(2) or (cur["speaker"] if cur else None))
                cur = {"start": t, "end": None, "speaker": spk, "text": m.group(3).strip(), "session": session}
                turns.append(cur)
            elif cur is not None:
                cur["text"] += " " + line
    for a, b in zip(turns, turns[1:] + [None]):
        speak = len(a["text"].split()) / 2.6 + 1.0               # ~ 155 words a minute, plus a second
        nxt = b["start"] if b and b["session"] == a["session"] and b["start"] >= a["start"] else None
        a["end"] = round(min(nxt, a["start"] + speak * 1.5) if nxt is not None else a["start"] + speak, 2)
    return [t for t in turns if t["text"]]


def load_reference(path: Path):
    """-> list of {start, end, speaker, text}. Times / speakers may be None (plain text)."""
    path = Path(path)
    suf = path.suffix.lower()
    if suf == ".pdf":
        return transcript_pdf(path)
    if suf in (".vtt", ".srt"):
        from .transcribe import parse_caption_file
        segs, _ = parse_caption_file(path)
        return [{"start": s["start"], "end": s["end"], "speaker": s["speaker"], "text": s["text"]} for s in segs]
    if suf == ".json":
        d = json.loads(path.read_text(encoding="utf-8"))
        rows = d.get("transcript", d.get("segments", d)) if isinstance(d, dict) else d
        out = []
        for r in rows:
            out.append({"start": r.get("start"), "end": r.get("end"),
                        "speaker": r.get("speaker_name") or r.get("speaker"), "text": r.get("text", "")})
        return out
    return [{"start": None, "end": None, "speaker": None, "text": path.read_text(encoding="utf-8")}]


def ami_reference(words_dir: Path, meeting: str, out: Path, gap=0.6):
    """AMI corpus manual words (words/<meeting>.<A-E>.words.xml) -> Teams-style .vtt with one voice per AMI speaker letter."""
    import xml.etree.ElementTree as ET
    segs = []
    for f in sorted(Path(words_dir).glob(f"{meeting}.*.words.xml")):
        who = "Speaker " + f.name.split(".")[1]
        cur = None
        for el in ET.parse(f).getroot():
            tag = el.tag.split("}")[-1]
            st, en = el.get("starttime"), el.get("endtime")
            if tag != "w" or st is None or en is None or el.get("punc") == "true":
                continue
            st, en, w = float(st), float(en), (el.text or "").strip()
            if cur and st - cur["end"] <= gap:
                cur["end"], cur["text"] = en, cur["text"] + " " + w
            else:
                cur = {"start": st, "end": en, "speaker": who, "text": w}
                segs.append(cur)
    segs.sort(key=lambda s: s["start"])

    def ts(t):
        return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:06.3f}"
    out.write_text("WEBVTT\n\n" + "\n\n".join(f"{ts(s['start'])} --> {ts(s['end'])}\n<v {s['speaker']}>{s['text']}</v>"
                                               for s in segs) + "\n", encoding="utf-8")
    return len(segs)


def hypothesis(file_id: int):
    """Our machine output for the recording: original text where a person has edited a line."""
    from . import package
    d = package.load_recording(file_id)
    if not d:
        raise ValueError(f"file {file_id} has no transcript")
    turns = [{"start": t["start"], "end": t["end"], "speaker": t["speaker"], "speaker_name": t["speaker_name"],
              "text": (t["original_text"] if t.get("edited") and t.get("original_text") else t["text"])}
             for t in d["turns"]]
    return d, turns


# ------------------------------------------------------------------ word accuracy
def word_scores(ref, hyp, examples=12):
    """ref / hyp: text, or lists of already normalised words (e.g. from timed_words)."""
    r = ref if isinstance(ref, list) else words(ref)
    h = hyp if isinstance(hyp, list) else words(hyp)
    ops = Levenshtein.editops(r, h)
    kind = Counter(o.tag for o in ops)
    S, D, I = kind.get("replace", 0), kind.get("delete", 0), kind.get("insert", 0)
    rc, hc = Counter(content(r)), Counter(content(h))
    hit = sum((rc & hc).values())
    subs = Counter(f"{r[o.src_pos]} → {h[o.dest_pos]}" for o in ops if o.tag == "replace")
    return {
        "reference_words": len(r), "our_words": len(h),
        "substituted": S, "deleted": D, "inserted": I,
        "wer": round((S + D + I) / max(1, len(r)), 4),
        "word_accuracy": round(1 - (S + D + I) / max(1, len(r)), 4),
        "content_recall": round(hit / max(1, sum(rc.values())), 4),
        "content_precision": round(hit / max(1, sum(hc.values())), 4),
        "top_substitutions": subs.most_common(examples),
        "top_missed": Counter(r[o.src_pos] for o in ops if o.tag == "delete").most_common(examples),
        "top_added": Counter(h[o.dest_pos] for o in ops if o.tag == "insert").most_common(examples),
    }


# ------------------------------------------------------------------ who spoke when
def der(ref, hyp, collar=0.25, step=0.01):
    """Diarization error rate on a 10 ms grid. ref may overlap (several speakers at once); speakers are matched
    one-to-one by the largest shared talk time (Hungarian assignment)."""
    from scipy.optimize import linear_sum_assignment
    ref = [s for s in ref if s.get("start") is not None and s.get("speaker")]
    hyp = [s for s in hyp if s.get("start") is not None and s.get("speaker")]
    if not ref or not hyp:
        return None
    end = max(max(s["end"] for s in ref), max(s["end"] for s in hyp))
    n = int(end / step) + 2
    rs = sorted({s["speaker"] for s in ref})
    hs = sorted({s["speaker"] for s in hyp})
    R = np.zeros((len(rs), n), bool)
    H = np.zeros((len(hs), n), bool)
    for s in ref:
        R[rs.index(s["speaker"]), max(0, int(s["start"] / step)):max(0, int(s["end"] / step))] = True
    for s in hyp:
        H[hs.index(s["speaker"]), max(0, int(s["start"] / step)):max(0, int(s["end"] / step))] = True
    keep = np.ones(n, bool)                                     # no-score collar around reference boundaries
    c = int(collar / step)
    for s in ref:
        for b in (s["start"], s["end"]):
            k = max(0, int(b / step))
            keep[max(0, k - c):k + c] = False
    R, H = R[:, keep], H[:, keep]
    overlap = R.astype(np.int32) @ H.T.astype(np.int32)       # frames shared by ref i and hyp j
    ri, hj = linear_sum_assignment(-overlap)
    mapping = {hs[j]: rs[i] for i, j in zip(ri, hj) if overlap[i, j] > 0}
    nr, nh = R.sum(0), H.sum(0)
    correct = np.zeros(R.shape[1], np.int32)
    for i, j in zip(ri, hj):
        correct += R[i] & H[j]
    total = nr.sum()
    miss = np.maximum(0, nr - nh).sum()
    fa = np.maximum(0, nh - nr).sum()
    conf = (np.minimum(nr, nh) - correct).sum()
    return {
        "der": round(float((miss + fa + conf) / max(1, total)), 4),
        "missed": round(float(miss / max(1, total)), 4),
        "false_alarm": round(float(fa / max(1, total)), 4),
        "confusion": round(float(conf / max(1, total)), 4),
        "right_speaker_share": round(float(correct.sum() / max(1, total)), 4),
        "reference_speakers": len(rs), "our_speakers": len(hs),
        "reference_speech_s": round(float(total * step), 1),
        "mapping": mapping, "collar_s": collar,
    }


def line_speakers(ref, hyp, mapping):
    """What a reader sees: for each of our lines, is the person we name the one who spoke most during it in the
    reference? Weighted by words. Lines with no reference speech at their time are counted separately."""
    ref = [s for s in ref if s.get("start") is not None and s.get("speaker")]
    right = wrong = none = 0
    for t in hyp:
        n = len(words(t["text"]))
        talk = {}
        for s in ref:
            o = min(t["end"], s["end"]) - max(t["start"], s["start"])
            if o > 0:
                talk[s["speaker"]] = talk.get(s["speaker"], 0) + o
        if not talk:
            none += n
        elif mapping.get(t["speaker"]) == max(talk, key=talk.get):
            right += n
        else:
            wrong += n
    total = right + wrong
    return {"right_share": round(right / max(1, total), 4), "words_right": right, "words_wrong": wrong,
            "words_without_reference_speech": none}


def _first(name):
    return re.sub(r"[^a-z]", "", (name or "").lower().split()[0]) if name and name.strip() else ""


_TITLES = {"dr", "mr", "mrs", "ms", "mx", "prof", "hon", "sen", "rep", "sir", "lcdr", "cdr", "capt", "col", "lt", "maj", "gen"}


def _name_parts(name):
    return [w for w in re.findall(r"[a-z]+", (name or "").lower()) if w not in _TITLES and len(w) >= 3]


def _same_name(a, b):
    """Same person by name: any first or last name in common ("John Torous" = "Dr. Torous"), or the same name
    spelled differently (Iain / Ian, Jessy / Jessie): audio has no spelling."""
    from rapidfuzz.fuzz import ratio
    pa, pb = _name_parts(a), _name_parts(b)
    if set(pa) & set(pb):
        return "right"
    if any(ratio(x, y) >= 75 for x in pa for y in pb):
        return "right, spelled differently"
    return "wrong"


def name_scores(mapping, hyp_turns, ref):
    """For each reference speaker matched to one of ours: did we give the right name, a wrong name, or none?"""
    ours = {t["speaker"]: t["speaker_name"] for t in hyp_turns}
    rows = []
    for h, r in mapping.items():
        mine = ours.get(h) or ""
        named = mine and not re.match(r"^Speaker \d+$", mine)
        real = not re.match(r"^Speaker [A-Z0-9]+$", r or "")
        rows.append({"reference": r, "ours": mine, "result": ("not checked" if not real else "no name" if not named
                                                             else _same_name(mine, r))})
    checked = [x for x in rows if x["result"] != "not checked"]
    return {"right": sum(x["result"].startswith("right") for x in checked), "wrong": sum(x["result"] == "wrong" for x in checked),
            "spelled_differently": sum(x["result"] == "right, spelled differently" for x in checked),
            "unnamed": sum(x["result"] == "no name" for x in checked), "checked": len(checked), "rows": rows}


# ------------------------------------------------------------------ run + report
ANCHOR_N = 4          # words in a row that must match to tie a reference time to one of ours
MIN_ANCHORS = 8


def _timed_pairs(segs):
    out = []
    for s in segs:
        ws = words(s["text"])
        dur = max(0.0, (s["end"] or 0) - (s["start"] or 0))
        out += [((s["start"] or 0) + dur * (i + 0.5) / max(1, len(ws)), w) for i, w in enumerate(ws)]
    return sorted(out, key=lambda x: x[0])


def _unique_ngrams(pairs, n=ANCHOR_N):
    seen = defaultdict(list)
    for i in range(len(pairs) - n + 1):
        g = tuple(w for _, w in pairs[i:i + n])
        if sum(len(w) for w in g) >= 14:                      # skip runs of tiny words ("and it is a")
            seen[g].append(pairs[i][0])
    return {g: ts[0] for g, ts in seen.items() if len(ts) == 1}


def align(ref, hyp, duration=None):
    """Where in the reference is our recording? Reference transcripts often cover a whole day (with the clock
    restarting after breaks), while the processed video is an excerpt. Word runs that occur once in both give the
    time shift (median); the session with most of them wins. The reference is then cut to the video's length."""
    hp = _unique_ngrams(_timed_pairs(hyp))
    sessions = sorted({s.get("session", 0) for s in ref})
    best = None
    for ses in sessions:
        part = [s for s in ref if s.get("session", 0) == ses]
        diffs = [hp[g] - t for g, t in _unique_ngrams(_timed_pairs(part)).items() if g in hp]
        if diffs:
            off = float(np.median(diffs))
            support = sum(abs(x - off) <= 20 for x in diffs)
            if not best or support > best["anchors"]:
                best = {"session": ses, "offset_s": round(off, 2), "anchors": support}
    if not best or best["anchors"] < MIN_ANCHORS:
        return ref, {"applied": False, "anchors": best["anchors"] if best else 0,
                     "note": "too few matching word runs to place the recording in the reference; compared as is"}
    end = duration or max(t["end"] for t in hyp)
    out = []
    for s in ref:
        if s.get("session", 0) != best["session"]:
            continue
        a, b = s["start"] + best["offset_s"], s["end"] + best["offset_s"]
        if b <= 0 or a >= end:
            continue
        if a < 0 or b > end:          # a turn cut by the start or end of the video: keep only its words inside
            ws = s["text"].split()
            dur = max(1e-6, b - a)
            keep = [w for i, w in enumerate(ws) if 0 <= a + dur * (i + 0.5) / len(ws) <= end]
            if not keep:
                continue
            s = {**s, "text": " ".join(keep)}
            a, b = max(a, 0.0), min(b, end)
        out.append({**s, "start": round(a, 2), "end": round(b, 2)})
    return out, {"applied": True, **best, "reference_turns_used": len(out), "reference_turns_total": len(ref),
                 "multi_session": len(sessions) > 1}


def run(file_id: int, reference: Path, collar=0.25, offset="auto"):
    ref = load_reference(reference)
    d, hyp = hypothesis(file_id)
    m = d["manifest"]["source"]
    timed = all(s.get("start") is not None and s.get("end") is not None for s in ref)
    alignment = None
    if timed and offset == "auto":
        ref, alignment = align(ref, hyp, m.get("duration_s"))
    elif timed and offset not in (None, "", "0", 0):
        ref = [{**s, "start": s["start"] + float(offset), "end": s["end"] + float(offset)} for s in ref]
        alignment = {"applied": True, "offset_s": float(offset), "anchors": None, "note": "offset given by hand"}
    res = {
        "kind": "accuracy", "at": time.time(), "file_id": file_id, "recording": m["name"], "folder": m["folder"],
        "duration_s": m.get("duration_s"), "reference": Path(reference).name, "alignment": alignment,
        "asr_model": d["manifest"].get("asr", {}).get("model") if isinstance(d["manifest"].get("asr"), dict) else None,
        "words": (word_scores(timed_words(ref), timed_words(hyp)) if timed
                  else word_scores(" ".join(s["text"] for s in ref), " ".join(t["text"] for t in hyp))),
        "word_order": "by time" if timed else "as written",
        "speakers": None, "names": None,
    }
    if all(s.get("start") is not None for s in ref) and any(s.get("speaker") for s in ref):
        res["speakers"] = der(ref, hyp, collar)
        if res["speakers"]:
            res["names"] = name_scores(res["speakers"]["mapping"], hyp, ref)
            res["speakers"]["lines"] = line_speakers(ref, hyp, res["speakers"]["mapping"])
    return save(res)


def save(res):
    out = config.OUTPUT_DIR / "benchmarks"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{res['kind']}-{res['file_id']}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(res['at']))}"
    (out / f"{stem}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / f"{stem}.md").write_text(markdown(res), encoding="utf-8")
    res["files"] = [str(out / f"{stem}.json"), str(out / f"{stem}.md")]
    return res


def _alignment_md(a):
    if not a:
        return []
    if not a.get("applied"):
        return [f"Alignment: {a.get('note')} ({a.get('anchors', 0)} matching word runs).", ""]
    if a.get("anchors") is None:
        return [f"Alignment: reference shifted by {a['offset_s']:+.1f} s (given by hand).", ""]
    s = (f"Alignment: the recording starts {_clock(-a['offset_s'])} into the reference"
         + (f" (session {a['session'] + 1}; the reference clock restarts after breaks)" if a.get("multi_session") else "")
         + f", found from {a['anchors']} matching word runs; {a['reference_turns_used']} of {a['reference_turns_total']} "
           "reference turns fall inside the recording and are scored.")
    return [s, ""]


def _clock(t):
    sign = "-" if t < 0 else ""
    t = abs(int(round(t)))
    return f"{sign}{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def _pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def markdown(res):
    if res["kind"] == "questions":
        from .questions import markdown as qmd
        return qmd(res)
    w, s, n = res["words"], res["speakers"], res["names"]
    L = [f"# Accuracy: {res['recording']}", "",
         f"Reference: `{res['reference']}` · recording {res['folder']} / {res['recording']}"
         + (f" · {res['duration_s'] / 60:.1f} min" if res.get("duration_s") else ""), "",
         *_alignment_md(res.get("alignment")),
         "| Measure | Result |", "|---|---|",
         f"| Word error rate (fillers ignored) | **{_pct(w['wer'])}** ({w['substituted']} wrong, {w['deleted']} missed, "
         f"{w['inserted']} extra of {w['reference_words']} reference words) |",
         f"| Word accuracy | {_pct(w['word_accuracy'])} |",
         f"| Content words found (recall) | {_pct(w['content_recall'])} |",
         f"| Our content words that are right (precision) | {_pct(w['content_precision'])} |"]
    if s:
        L += [f"| Who-spoke-when error (DER, {s['collar_s']} s collar) | **{_pct(s['der'])}** (missed {_pct(s['missed'])}, "
              f"false alarm {_pct(s['false_alarm'])}, wrong speaker {_pct(s['confusion'])}) |",
              f"| Speech given to the right person | {_pct(s['right_speaker_share'])} |",
              *([f"| Our transcript words under the right person's name | **{_pct(s['lines']['right_share'])}** "
                 f"({s['lines']['words_wrong']} of {s['lines']['words_right'] + s['lines']['words_wrong']} words under a wrong name) |"]
                if s.get("lines") else []),
              f"| Speakers (reference / ours) | {s['reference_speakers']} / {s['our_speakers']} |"]
    if n and n["checked"]:
        L.append(f"| Names | {n['right']} of {n['checked']} right"
                 + (f" ({n['spelled_differently']} spelled differently)" if n.get("spelled_differently") else "")
                 + f", {n['wrong']} wrong, {n['unnamed']} not named |")
    L += ["", "## Most frequent mistakes", "",
          "Wrong word (reference → ours): " + (", ".join(f"{k} ({v})" for k, v in w["top_substitutions"]) or "none"), "",
          "Missed: " + (", ".join(f"{k} ({v})" for k, v in w["top_missed"]) or "none"), "",
          "Added: " + (", ".join(f"{k} ({v})" for k, v in w["top_added"]) or "none"), ""]
    if n and n["rows"]:
        L += ["## Speakers", "", "| Reference | Ours | Name |", "|---|---|---|"]
        L += [f"| {r['reference']} | {r['ours']} | {r['result']} |" for r in n["rows"]]
    return "\n".join(L) + "\n"
