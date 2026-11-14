"""Question test: is the meeting's information still there after the video is gone?

    datadistill questions <file_id> <questions.json> [--no-ai]

A person who watched the ORIGINAL (or has its script / official minutes) writes questions with the facts a correct
answer must contain. Never write them from DataDistill's output, or the test proves nothing.

    {"written_from": "the original video", "questions": [
      {"q": "By how much did traffic grow?", "facts": [["18 percent", "eighteen percent"]], "time": "0:05"},
      {"q": "Who negotiates with the supplier?", "facts": ["Maria", ["partial delivery", "supplier"]], "source": "speech"}]}

facts:  every fact must be found; a fact is a string or a list of alternative wordings.
source: "speech", "screen" (slide text) or "either" (default).  time: optional, the answer must be within 2 min of it.

Two results per question:
  * kept  - the facts are in the distilled output (transcript lines within 60 s of each other, or the text of one shared
            screen). This is the storage claim: what was deleted, was not needed to answer.
  * AI    - Ministral 3 8B (local, Ollama) answers from the distilled output only; correct when its answer contains the
            facts. This is the "AI-ready" claim. Skipped when Ollama is not running.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import package
from .evaluate import hypothesis, save, words

WINDOW_S = 60
NEAR_S = 120


def _secs(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    parts = [float(p) for p in str(v).split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def _norm(text):
    # hyphens split, so "television-related" contains the fact "television"
    return " " + " ".join(words(text, join_hyphens=False)) + " "


def _alts(fact):
    return [fact] if isinstance(fact, str) else list(fact)


def _same_word(a, b):
    """'metacognition' = 'metacognitive', 'sycophancy' = 'sycophantic': long words that differ only in the ending."""
    import os
    return len(a) >= 8 and len(b) >= 6 and len(os.path.commonprefix([a, b])) >= max(7, len(a) - 3)


def _has(norm_text, fact):
    for a in _alts(fact):
        na = _norm(a)
        if not na.strip():
            continue
        if na in norm_text:
            return True
        one = na.split()
        if len(one) == 1 and any(_same_word(one[0], w) for w in norm_text.split()):
            return True
    return False


def load(path: Path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    qs = d["questions"] if isinstance(d, dict) else d
    for i, q in enumerate(qs, 1):
        q.setdefault("id", i)
        q.setdefault("source", "either")
        if not q.get("facts"):
            raise ValueError(f"question {q['id']} has no facts")
    return (d if isinstance(d, dict) else {}), qs


def windows(d, turns):
    """Places an answer can come from: runs of transcript lines spanning <= 60 s, and each shared screen's text."""
    out = []
    for i, t in enumerate(turns):
        j, parts = i, []
        while j < len(turns) and turns[j]["end"] - t["start"] <= WINDOW_S:
            parts.append(turns[j])
            j += 1
        parts = parts or [t]
        out.append({"source": "speech", "start": t["start"], "end": parts[-1]["end"],
                    "where": f"{package.clock(t['start'])} {t['speaker_name']}",
                    # with the speaker names, so "who will send the minutes?" can be answered from who said it
                    "text": " ".join(f"{p['speaker_name']}: {p['text']}" for p in parts)})
    for s in d["screens"]:
        if s["kind"] == "non_slide" or not s.get("text"):
            continue
        where = f"{package.clock(s['t_start'])} screen \"{s.get('title') or 'untitled'}\""
        if s.get("source"):
            where += f" ({s['source']['doc']} p.{s['source']['page']})"
        out.append({"source": "screen", "start": s["t_start"], "end": s["t_end"], "where": where, "text": s["text"]})
    for w in out:
        w["norm"] = _norm(w["text"])
    return out


def find(q, wins):
    t = _secs(q.get("time"))
    best, best_n = None, -1
    for w in wins:
        if q["source"] != "either" and w["source"] != q["source"]:
            continue
        if t is not None and (w["end"] < t - NEAR_S or w["start"] > t + NEAR_S):
            continue
        n = sum(_has(w["norm"], f) for f in q["facts"])
        if n > best_n or (n == best_n and best is not None and len(w["norm"]) < len(best["norm"])):
            best, best_n = w, n          # equal hits: the tightest place, so "where" points at the answer itself
    missing = [_alts(f)[0] for f in q["facts"] if not (best and _has(best["norm"], f))]
    return {"kept": best_n == len(q["facts"]), "where": best["where"] if best and best_n > 0 else None, "missing": missing}


# ------------------------------------------------------------------ AI answers: answer.py (local Ministral via Ollama)


def run(file_id: int, path: Path, use_ai=True):
    info, qs = load(path)
    d, turns = hypothesis(file_id)
    wins = windows(d, turns)
    ai_ok, ai_msg = (False, "skipped (--no-ai)")
    if use_ai:
        from . import summary
        ai_ok, ai_msg = summary.available()
    rows = []
    for q in qs:
        r = {"id": q["id"], "q": q["q"], "facts": q["facts"], "source": q["source"], "time": q.get("time"), **find(q, wins)}
        if ai_ok:
            from . import answer
            r["ai"] = answer.check(answer.ask(d, turns, q["q"]), q)
        rows.append(r)
    m = d["manifest"]["source"]
    from . import summary
    res = {"kind": "questions", "at": time.time(), "file_id": file_id, "recording": m["name"], "folder": m["folder"],
           "questions_file": Path(path).name, "written_from": info.get("written_from"),
           "total": len(rows), "kept": sum(r["kept"] for r in rows),
           "ai_model": summary.MODEL_NAME if ai_ok else None, "ai_note": None if ai_ok else ai_msg,
           "ai_correct": sum(r["ai"]["correct"] for r in rows) if ai_ok else None,
           "ai_verified": sum(r["ai"]["verified"] for r in rows) if ai_ok else None,
           "ai_sources_hold_facts": sum(r["ai"]["sources_hold_facts"] for r in rows) if ai_ok else None,
           "rows": rows}
    return save(res)


def markdown(res):
    n = res["total"]
    L = [f"# Question test: {res['recording']}", "",
         f"Questions: `{res['questions_file']}`" + (f", written from {res['written_from']}" if res.get("written_from") else ""), "",
         "| Measure | Result |", "|---|---|",
         f"| Answer still in the distilled output | **{res['kept']} of {n}** ({res['kept'] / max(1, n) * 100:.0f}%) |"]
    if res["ai_correct"] is not None:
        L.append(f"| Answered correctly by local AI from the distilled output | **{res['ai_correct']} of {n}** "
                 f"({res['ai_correct'] / max(1, n) * 100:.0f}%) · {res['ai_model']} |")
        if res.get("ai_verified") is not None:
            L.append(f"| AI answers whose numbers and names all occur in the record | {res['ai_verified']} of {n} |")
            L.append(f"| AI answers whose source lines hold the facts | {res['ai_sources_hold_facts']} of {n} |")
    else:
        L.append(f"| Local AI answers | not run: {res['ai_note']} |")
    L += ["", "| # | Question | Kept | Where | AI answer |", "|---|---|---|---|---|"]
    for r in res["rows"]:
        kept = "yes" if r["kept"] else "no (missing: " + ", ".join(r["missing"]) + ")"
        ai = "—"
        if r.get("ai"):
            ai = ("✔ " if r["ai"]["correct"] else "✘ ") + r["ai"]["answer"].replace("|", "/")
            if r["ai"].get("unsupported"):
                ai += " ⚠ not in the record: " + ", ".join(r["ai"]["unsupported"])
            if r["ai"].get("sources"):
                s = r["ai"]["sources"][0]
                ai += f" · source {package.clock(s['time'])} {s['speaker']}".rstrip()
        L.append(f"| {r['id']} | {r['q'].replace('|', '/')} | {kept} | {r['where'] or '—'} | {ai} |")
    return "\n".join(L) + "\n"
