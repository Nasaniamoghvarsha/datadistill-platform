"""Answer a question about one meeting from its distilled output, with the lines the answer comes from.

Measured on FDA's verbatim transcript of the 6 Nov 2025 DHAC morning (20 questions, Ministral 3 3B, before the switch to 8B on 13 Nov; not re-measured with 8B):
  * plain answering (the record, or for long meetings its 10 best-matching 2-minute stretches): 20 of 20;
  * making the model cite numbered lines itself: 15-18 of 20, and it often left the citations out.
So the model only answers. The program then
  1. finds the lines that support the answer (the answer's numbers, names and content words, plus the question's,
     matched against the record) and returns the best 1-3 with time and speaker, so a reader can check;
  2. checks every number and name in the answer against the record: one that does not occur there is reported
     ("not verified: 50 million is not in the record"), which catches invented figures. It cannot catch a figure
     that was misheard in the transcript itself; the source line makes that visible.
"""
from __future__ import annotations

import re

from . import package
from .evaluate import STOP, words

FULL_WORDS = 6000
CHUNK_S = 120
TOP_CHUNKS = 10
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
SYSTEM = ("You answer questions about one meeting, using ONLY the meeting record below (transcript lines with time and "
          "speaker, and the text of the slides shown). Answer in one or two short sentences with the concrete facts "
          "(names, numbers, dates). If the record does not contain the answer, answer exactly: not in the record.")


def _lines(d, turns):
    out = [{"t": t["start"], "who": t.get("speaker_name") or "", "text": t["text"], "kind": "line"} for t in turns]
    for s in d.get("screens") or []:
        if s.get("kind") != "non_slide" and (s.get("text") or "").strip():
            out.append({"t": s["t_start"], "who": "", "kind": "slide", "title": s.get("title") or "",
                        "text": re.sub(r"\s+", " ", s["text"])[:700]})
    return out


def record(d, turns, question):
    """The text the model reads: everything for a short meeting; else the 2-minute stretches sharing most words
    with the question (plus all slide text)."""
    lines = [f"[{package.clock(t['start'])}] {t.get('speaker_name') or ''}: {t['text']}" for t in turns]
    slides = [f"[slide shown {package.clock(s['t_start'])}-{package.clock(s['t_end'])}] " + re.sub(r"\s+", " ", s["text"])[:600]
              for s in d.get("screens") or [] if s.get("kind") != "non_slide" and (s.get("text") or "").strip()]
    if sum(len(x.split()) for x in lines) > FULL_WORDS:
        qw = {w for w in words(question) if w not in STOP}
        chunks, cur, t0 = [], [], None
        for t, line in zip(turns, lines):
            if t0 is None or t["start"] - t0 > CHUNK_S:
                cur, t0 = [], t["start"]
                chunks.append(cur)
            cur.append(line)
        best = sorted(range(len(chunks)), key=lambda i: -len(qw & set(words(" ".join(chunks[i])))))[:TOP_CHUNKS]
        lines = ["…"] + [x for i in sorted(best) for x in chunks[i] + ["…"]]
    return "MEETING RECORD\n\nSlides:\n" + ("\n".join(slides) or "(none)") + "\n\nTranscript:\n" + "\n".join(lines)


_NUM = re.compile(r"\d[\d,.:]*\d|\d")
_CAP = re.compile(r"\b[A-Z][a-zA-Z'’\-]{2,}\b")
_COMMON = {"the", "this", "that", "they", "there", "yes", "not", "none", "also", "according", "both", "however",
           "record", "meeting", "slide", "speaker", "transcript", "january", "february", "march", "april", "may", "june",
           "july", "august", "september", "october", "november", "december", "monday", "tuesday", "wednesday",
           "thursday", "friday"}


def _pieces(text, n=60):
    sents = re.split(r"(?<=[.!?])\s+", text.strip())
    out, cur = [], []
    for s in sents:
        if cur and len(" ".join(cur + [s]).split()) > n:
            out.append(" ".join(cur))
            cur = []
        cur.append(s)
    return out + [" ".join(cur)] if cur else out


def sources(lines, question, ans, k=3):
    """The record lines (cut into ~60-word pieces) that best support the answer."""
    aw = [w for w in words(ans) if w not in STOP and len(w) > 2 or w.isdigit()]
    qw = {w for w in words(question) if w not in STOP and len(w) > 2}
    nums = {w for w in aw if any(c.isdigit() for c in w)}
    scored = []
    for ln in lines:
        start = ln["t"]
        for p in _pieces(ln["text"]) if ln["kind"] == "line" else [ln["text"]]:
            pw = set(words(f"{ln['who']} {ln.get('title', '')} {p}"))
            s = 3 * len(nums & pw) + 2 * len((set(aw) - qw) & pw) + len(qw & pw)
            if s:
                scored.append((s, start, ln, p))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out, seen = [], set()
    for s, start, ln, p in scored[:k]:
        if p in seen:
            continue
        seen.add(p)
        out.append({"time": start, "speaker": ln["who"], "kind": ln["kind"], "text": p, "score": s})
    return out


def unsupported(ans, question, lines):
    """Numbers and names in the answer that occur nowhere in the record or the question."""
    allw = set(words(question))
    for ln in lines:
        allw |= set(words(f"{ln['who']} {ln.get('title', '')} {ln['text']}"))
    allw |= {re.sub(r"'s$", "", x) for x in allw}
    probs = []
    for n in _NUM.findall(ans):
        nw = words(n)
        if nw and not set(nw) <= allw:
            probs.append(n)
    for c in _CAP.findall(ans):
        w = re.sub(r"['’]s$", "", c.lower()).strip("'’-")
        parts = [x for x in re.split(r"[-’']", w) if x]
        if w in _COMMON or w.replace("-", "") in allw or all(x in allw or x.rstrip("s") in allw for x in parts):
            continue
        probs.append(c)
    return probs


def ask(d, turns, question):
    """-> {answer, found, sources: [{time, speaker, kind, text}], verified, unsupported}"""
    from . import summary
    out = summary._chat([{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": record(d, turns, question) + "\n\nQUESTION: " + question}],
                        schema=SCHEMA)
    ans = re.sub(r"\*\*", "", (out.get("answer") or "").strip())
    found = not re.fullmatch(r"\s*not in the record\.?\s*", ans, re.I)
    lines = _lines(d, turns)
    bad = unsupported(ans, question, lines) if found else []
    return {"answer": ans if found else "not in the record", "found": found,
            "sources": sources(lines, question, ans) if found else [], "verified": found and not bad, "unsupported": bad}


def check(r, q):
    """Grade an answer against a question's facts (question test): correct, and do its sources hold the facts?"""
    from .questions import _alts, _has, _norm
    n, qn = _norm(r["answer"]), _norm(q["q"])
    missing = [_alts(f)[0] for f in q["facts"] if not _has(qn, f) and not _has(n, f)]
    if not r["found"]:
        missing = [_alts(f)[0] for f in q["facts"]]
    src = _norm(" ".join(f"{e['speaker']} {e['text']}" for e in r["sources"]))
    return {**r, "correct": not missing, "missing": missing,
            "sources_hold_facts": bool(r["sources"]) and all(_has(src, f) or _has(qn, f) for f in q["facts"])}
