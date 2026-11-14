"""Meeting summary written by a local language model, checked against the transcript.

Model: Ministral 3 8B (Mistral AI, Apache-2.0) served by Ollama on this computer (http://127.0.0.1:11434);
nothing leaves the machine. The model reads the speaker-attributed transcript (with the titles of the
slides shown) and returns minutes as JSON: title, purpose, summary, discussion points, decisions, action
items (owner, task, due), open questions. Every item must cite the time it was said.

Guard against invented content (small models can make things up): every item is checked against the
transcript around the time it cites. An item is dropped when its time is outside the meeting or when the
transcript there does not share enough content words with it; an owner who never appears becomes "unclear".
What was dropped is counted and shown in the report. Long meetings are summarised in parts first.
Results are cached per transcript version (an edited line or renamed speaker gives a new summary).
"""
import hashlib
import json
import os
import re
import time

import requests

from . import db, package
from .config import OUTPUT_DIR

OLLAMA = "http://127.0.0.1:11434"
MODEL = os.getenv("DD_LLM", "ministral-3:8b")      # Mistral only (EU rules); vision-capable: also reads diagram slides
MODEL_NAME = "Ministral 3 8B (Mistral AI, Apache-2.0), local via Ollama"
PART_WORDS = 6500          # one pass up to ~45 min of talk (16k context on an 8 GB GPU); longer meetings are summarised in parts
CHECK_WINDOW_S = 90        # an item is checked against the transcript within +-90 s of the time it cites
MIN_SHARED_WORDS = 2

STOP = set("""about after again also although always another because been before being between both cannot could
does doing done during each either enough even every from further going have having here into itself just know like
made make many maybe might more most much must need never next none nothing only other over please quite rather
really said same should since some something still such sure than that their them then there these they thing
things think this those though through today under until very want well were what when where whether which while
will with within without would yeah your okay right yes about meeting discuss discussed discussion talk talked
mentioned said says speaker""".split())
WORD = re.compile(r"[a-zà-ÿ0-9][a-zà-ÿ0-9'-]{2,}")

ITEM_TIME = {"type": "string", "description": "time in the meeting where this is said, hh:mm:ss"}
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "purpose": {"type": "string"},
        "summary": {"type": "string"},
        "discussion": {"type": "array", "items": {"type": "object", "properties": {
            "point": {"type": "string"}, "speakers": {"type": "array", "items": {"type": "string"}}, "time": ITEM_TIME},
            "required": ["point", "time"]}},
        "decisions": {"type": "array", "items": {"type": "object", "properties": {
            "decision": {"type": "string"}, "time": ITEM_TIME}, "required": ["decision", "time"]}},
        "action_items": {"type": "array", "items": {"type": "object", "properties": {
            "owner": {"type": "string"}, "task": {"type": "string"}, "due": {"type": "string"}, "time": ITEM_TIME},
            "required": ["owner", "task", "time"]}},
        "open_questions": {"type": "array", "items": {"type": "object", "properties": {
            "question": {"type": "string"}, "time": ITEM_TIME}, "required": ["question", "time"]}},
    },
    "required": ["title", "purpose", "summary", "discussion", "decisions", "action_items", "open_questions"],
}

SYSTEM = """You write accurate meeting minutes from a transcript produced by speech recognition.
Rules:
- Use ONLY what is said in the transcript. Never add facts, names, numbers or dates that are not in it.
- Every discussion point, decision, action item and open question MUST give ONE time (hh:mm:ss, not a range) of the line where it is said, copied from the transcript.
- A decision is only something the participants actually agreed, approved or voted on. If nothing was decided, return an empty list.
- Every formal motion that was voted on (moved, seconded, "all in favor", approved, passed, adjourned) is a decision: list each one, with who moved and seconded it if that is said.
- An action item is a concrete task someone committed to. Use the speaker label or the name used in the transcript as owner; if unclear, write "unclear". Leave "due" empty unless a date or deadline is said.
- Never expand an abbreviation or complete a name unless the transcript itself does (write "TARC", not what it might stand for).
- The transcript comes from speech recognition: ignore filler words and obvious recognition errors; do not quote them.
- Lines by "Music / video sound" are music or a video played in the meeting, not said by a participant: use them only as context (for example "a video about X was shown"), never as decisions or action items.
- Write in plain, neutral English. Summary: 4 to 7 sentences. Discussion: the main topics in time order, at most 8.
Answer with JSON only."""


def unload_model():
    """Ask Ollama to drop the model from memory now (keep_alive 0): the next video needs the GPU for speech recognition,
    and the next summary loads the model again. Never raises."""
    try:
        requests.post(f"{OLLAMA}/api/generate", json={"model": MODEL, "keep_alive": 0}, timeout=15)
    except Exception:
        pass


def available():
    """(ok, message): is Ollama running and the model present?"""
    try:
        tags = requests.get(f"{OLLAMA}/api/tags", timeout=3).json().get("models", [])
    except (requests.RequestException, ValueError):
        return False, "Ollama is not running (start Ollama to get AI summaries)"
    if not any(t.get("name", "").startswith(MODEL) for t in tags):
        return False, f"the model {MODEL} is not installed in Ollama (ollama pull {MODEL})"
    return True, "ok"


def _clock(t):
    return package.clock(t)


def _secs(s):
    """Seconds of the first time in the text: "00:31:58", "31:58", or a range "31:58–32:35" (its start)."""
    m = re.search(r"(?<!\d)(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?!\d)", str(s or ""))
    if not m:
        return None
    h, mi, se = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return h * 3600 + mi * 60 + se


def _lines(turns):
    """Transcript as the model reads it: one line per speaker turn (consecutive lines of one speaker joined)."""
    out, cur = [], None
    for t in turns:
        if cur and cur["speaker"] == t["speaker_name"] and t["start"] - cur["end"] < 3 and len(cur["text"].split()) < 120:
            cur["text"] += " " + t["text"]
            cur["end"] = t["end"]
        else:
            cur = {"speaker": t["speaker_name"], "start": t["start"], "end": t["end"], "text": t["text"]}
            out.append(cur)
    return [f"[{_clock(c['start'])}] {c['speaker']}: {c['text']}" for c in out]


def _chat(messages, schema=SCHEMA, num_ctx=16384):
    r = requests.post(f"{OLLAMA}/api/chat", timeout=900, json={
        "model": MODEL, "messages": messages, "stream": False, "format": schema, "keep_alive": "5m",
        "options": {"temperature": 0.2, "seed": 7, "num_ctx": num_ctx}})
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def _context(d):
    m = d["manifest"]
    sp = ", ".join(f"{s['label']}" for s in d["speakers"])
    slides = list(dict.fromkeys(s["title"] for s in d["screens"] if s["kind"] != "non_slide" and s.get("title")))
    return (f"Recording: {m['source']['name']} (folder {m['source']['folder']}), length {_clock(m['source']['duration_s'])}.\n"
            f"Speakers: {sp or 'unknown'}.\n" + (f"Slides shown: {'; '.join(slides[:30])}.\n" if slides else ""))


def _words(text):
    return {w for w in WORD.findall((text or "").lower()) if w not in STOP}


CAP = re.compile(r"(?<![.!?:]\s)(?<!^)\b([A-Z][a-zA-Z]{3,})\b")


def _unknown_names(text, known):
    """Capitalised words (names, places, expansions) that occur nowhere in the transcript, slides or speaker list."""
    return [w for w in CAP.findall(text or "") if w.lower() not in known]


def _plain(t):
    return " ".join(re.findall(r"[a-zà-ÿ0-9]+", (t or "").lower()))


def _strip_unknown_brackets(text, known, said_plain=""):
    """Remove "(…)" that the transcript does not support: a phrase of 2+ words must occur as that phrase
    (an invented expansion like "TARC (Target Network)" uses common words, so single words are not enough)."""
    def keep(m):
        inner = m.group(1)
        words = re.findall(r"[A-Za-z]{2,}", inner)
        if len(words) >= 2 and not re.search(r"\d|e\.g|i\.e|via|see", inner):
            return m.group(0) if _plain(inner) in said_plain else ""
        return "" if _unknown_names(inner, known) else m.group(0)
    return re.sub(r"\s*\(([^)]*)\)", keep, text or "")


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_YEAR = re.compile(rf"\b({MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b")


def _fix_years(text, said_plain, fixes):
    """A date with a year must occur with that year in the transcript or slides; speakers often say only
    "May 31st" and a small model guesses the year. Unsupported years are removed: "May 31, 2026" -> "May 31"."""
    def sub(m):
        mo, day, yr = m.group(1), m.group(2), m.group(3)
        forms = [f"{mo} {day} {yr}", f"{mo} {day}st {yr}", f"{mo} {day}nd {yr}", f"{mo} {day}rd {yr}", f"{mo} {day}th {yr}"]
        if any(_plain(f) in said_plain for f in forms):
            return m.group(0)
        fixes.append(f"{m.group(0)} -> {mo} {day}")
        return f"{mo} {day}"
    return DATE_YEAR.sub(sub, text or "")


def check(result, turns, duration, speaker_labels, extra_text=""):
    """Keep only what the transcript supports. Returns (result, removed list).
    1. time: an item must cite a time inside the meeting, and the transcript there must share its content words;
    2. names: a capitalised word that occurs nowhere in the transcript, slides or speaker list is invented:
       inside brackets the bracket is removed, elsewhere the item is dropped (summary text: the sentence is dropped)."""
    removed = []
    norm_names = {n.lower() for n in speaker_labels} | {p.lower() for n in speaker_labels for p in n.split() if len(p) > 2}
    said = " ".join(t["text"] for t in turns).lower()
    tokens = re.findall(r"[a-zà-ÿ0-9'’-]+", (said + " " + extra_text).lower())
    known = set(tokens) | {p for t in tokens for p in re.split(r"[-'’]", t) if p} | norm_names | {"speaker", "unclear"}
    said_plain = _plain(said + " " + extra_text)

    fixes = []
    for key in ("title", "purpose", "summary"):
        txt = _fix_years(_strip_unknown_brackets(result.get(key) or "", known, said_plain), said_plain, fixes)
        sents = re.split(r"(?<=[.!?])\s+", txt)
        keep = []
        for s_ in sents:
            bad = _unknown_names(s_, known)
            if bad and key != "title":
                removed.append({"section": key, "text": s_, "time": "", "reason": f"names not in the transcript: {', '.join(bad)}"})
                continue
            keep.append(s_)
        result[key] = " ".join(keep).strip()

    line_words = [(x, _words(x["text"])) for x in turns]

    def supported(text, ts):
        """(ok, reason, time of the line that says it). The time is re-anchored to the best-matching line: near the
        cited time, or anywhere in the meeting if a line clearly says it (the model's times can be imprecise)."""
        t0 = _secs(ts)
        if t0 is None or t0 > duration + 5:
            return False, "time not in the meeting", None
        want = _words(text)
        if not want:
            return False, "no content", None
        # The model sometimes drops the hour in long meetings ("26:36" for [02:26:36]): a time without hours is
        # tried at every hour of the meeting, and the one whose transcript shares most words wins.
        cands = [t0]
        if t0 < 3600 and not re.search(r"\d{1,2}:\d{1,2}:\d{2}", str(ts)):
            cands += [t0 + h * 3600 for h in range(1, int(duration // 3600) + 1) if t0 + h * 3600 <= duration + 5]
        scored = []
        for t in cands:
            near = [(x, w) for x, w in line_words if x["end"] >= t - CHECK_WINDOW_S and x["start"] <= t + CHECK_WINDOW_S]
            win = set().union(*(w for _, w in near)) if near else set()
            scored.append((len(want & win), -cands.index(t), t, near))
        shared, _, t, near = max(scored, key=lambda s: s[:2])
        if shared >= MIN_SHARED_WORDS:
            best = max(near, key=lambda xw: (len(want & xw[1]), -abs(xw[0]["start"] - t)))
            return True, "", best[0]["start"]
        best = max(line_words, key=lambda xw: len(want & xw[1]))
        if len(want & best[1]) >= max(3, len(want) // 2):
            return True, "", best[0]["start"]
        return False, f"not found in the transcript near {ts}", None

    for key, field in (("discussion", "point"), ("decisions", "decision"), ("action_items", "task"), ("open_questions", "question")):
        kept = []
        for it in result.get(key) or []:
            it[field] = _fix_years(_strip_unknown_brackets(it.get(field, ""), known, said_plain), said_plain, fixes)
            if key == "action_items" and it.get("due"):
                it["due"] = _fix_years(it["due"], said_plain, fixes)
            ok, why, at = supported(it.get(field, ""), it.get("time"))
            bad = _unknown_names(it.get(field, ""), known)
            if ok and bad:
                ok, why = False, f"names not in the transcript: {', '.join(bad)}"
            if not ok:
                removed.append({"section": key, "text": it.get(field, ""), "time": it.get("time"), "reason": why})
                continue
            it["model_time"] = it.get("time")
            it["t"] = round(at, 1)
            it["time"] = _clock(at)
            if key == "discussion" and it.get("speakers"):
                # only people who actually speak there (the model credits someone who is merely mentioned)
                there = {x["speaker_name"] for x in turns if x["end"] >= at - CHECK_WINDOW_S and x["start"] <= at + CHECK_WINDOW_S}
                it["speakers"] = [s for s in it["speakers"] if s in there] or sorted(
                    {x["speaker_name"] for x in turns if abs(x["start"] - at) < 1})
            if key == "action_items":
                owner = (it.get("owner") or "").strip()
                if owner and owner.lower() != "unclear" and owner.lower() not in norm_names and owner.lower() not in said:
                    it["owner"] = "unclear"
                it["due"] = (it.get("due") or "").strip()
            kept.append(it)
        result[key] = sorted(kept, key=lambda x: x["t"])
    result["corrections"] = fixes
    return result, removed


def _write(d):
    lines = _lines(d["turns"])
    ctx = _context(d)
    words = sum(len(ln.split()) for ln in lines)
    if words <= PART_WORDS:
        res = _chat([{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": ctx + "\nTranscript:\n" + "\n".join(lines)}])
        return res, 1
    # long meeting: minutes per part, then one summary of the parts (times stay those of the full meeting)
    parts, cur, n = [], [], 0
    for ln in lines:
        cur.append(ln)
        n += len(ln.split())
        if n >= PART_WORDS:
            parts.append(cur)
            cur, n = [], 0
    if cur:
        parts.append(cur)
    notes = []
    for k, p in enumerate(parts):
        notes.append(_chat([{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": ctx + f"\nThis is part {k + 1} of {len(parts)} of the meeting.\nTranscript:\n" + "\n".join(p)}]))
    merged = {key: [x for nt in notes for x in (nt.get(key) or [])] for key in ("discussion", "decisions", "action_items", "open_questions")}
    final = _chat([{"role": "system", "content": SYSTEM + "\nYou now get the minutes of each part of one meeting. Merge them into minutes "
                                                          "of the whole meeting: remove repeats, keep times as given."},
                   {"role": "user", "content": ctx + "\nMinutes of the parts:\n" + json.dumps(
                       {"parts": [{k: nt.get(k) for k in ("title", "purpose", "summary")} for nt in notes], **merged}, ensure_ascii=False)}])
    return final, len(parts)


def _key(d):
    h = hashlib.sha256()
    for t in d["turns"]:
        h.update(f"{t['start']}|{t['speaker_name']}|{t['text']}\n".encode("utf-8"))
    return f"{MODEL}:{h.hexdigest()[:16]}"


def _cache_path(file_id):
    return OUTPUT_DIR / "recordings" / str(file_id) / "summary.json"


_OUTCOME = re.compile(r"\b(motion (?:passes|passed|carries|carried|is approved|fails|failed)|(?:that'?s|it'?s|is|was) unanimous|"
                      r"so ordered|the ayes have it|(?:we are|we're|we stand|meeting is|this meeting is) (?:now )?adjourned)\b", re.I)
_MOTION = re.compile(r"\bmotion to ([a-z][^.?!]{3,90})|\b(?:move to|moved to|motion by [^,.]+? to) "
                     r"((?:approve|adopt|accept|adjourn|table|amend|endorse)[^.?!]{0,80})", re.I)
_MOVER = re.compile(r"\bmotion (?:by|from) ([A-Z][a-z]+(?: [A-Z][a-z]+)?)", re.I)
_SECOND = re.compile(r"\bseconded by ([A-Z][a-z]+(?: [A-Z][a-z]+)?)", re.I)


def formal_votes(turns, look_back_s=180):
    """Votes the transcript records: an outcome ("motion passes", "that's unanimous", "we are adjourned") with the
    motion stated shortly before ("a motion to approve the minutes of September 10th"). Deterministic, so a vote
    is never left out of the minutes even when the language model skips it."""
    out = []
    for i, t in enumerate(turns):
        m = _OUTCOME.search(t["text"])
        if not m or (out and t["start"] - out[-1]["t"] < 60):
            continue
        window = [x for x in turns[max(0, i - 80):i + 1] if t["start"] - x["start"] <= look_back_s]
        text = " ".join(x["text"] for x in window)
        motions = list(_MOTION.finditer(text))
        what = None
        if motions:
            mm = motions[-1]
            what = (mm.group(1) or mm.group(2)).strip(" ,")
            what = re.sub(r"\s+(?:if|and|so|please|is there|does anyone|any)\b.*$", "", what, flags=re.I).strip(" ,")
        adjourn = "adjourn" in m.group(1).lower() or (what or "").lower().startswith("adjourn")
        if not what and not adjourn:
            continue
        result = "the meeting was adjourned" if adjourn else ("failed" if "fail" in m.group(1).lower() else "passed")
        if re.search(r"unanimous", text, re.I) and not adjourn:
            result += " unanimously"
        mover, second = _MOVER.findall(text), _SECOND.findall(text)
        who = ", ".join(x for x in ((f"moved by {mover[-1]}" if mover else ""), (f"seconded by {second[-1]}" if second else "")) if x)
        label = ("Motion to adjourn: " + result) if adjourn else f"Motion to {what}: {result}"
        out.append({"decision": label + (f" ({who})" if who else ""), "time": _clock(t["start"]), "t": t["start"], "rule": True})
    return out


def _add_votes(res, turns):
    """Add formal votes the model left out (none within 2 minutes that already mentions the motion)."""
    have = res.get("decisions") or []
    added = 0
    for v in formal_votes(turns):
        near = [d for d in have if _secs(d.get("time")) is not None and abs(_secs(d.get("time")) - v["t"]) <= 120]
        if any(re.search(r"motion|minutes|adjourn|approv|vote", d.get("decision", ""), re.I) for d in near):
            continue
        have.append({"decision": v["decision"], "time": v["time"], "by": "found in the transcript"})
        added += 1
    have.sort(key=lambda d: _secs(d.get("time")) or 0)
    res["decisions"] = have
    return added


def get(file_id, create=True):
    """The checked summary of a recording (cached), or {'error': ...} when it cannot be made."""
    d = package.load_recording(file_id)
    if not d or not d["turns"]:
        return {"error": "no speech in this recording"}
    key = _key(d)
    p = _cache_path(file_id)
    if p.exists():
        c = json.loads(p.read_text(encoding="utf-8"))
        if c.get("key") == key:
            return c
    if not create:
        return None
    ok, msg = available()
    if not ok:
        return {"error": msg}
    t0 = time.time()
    raw, parts = _write(d)
    slides = " ".join((s.get("text") or "") + " " + (s.get("title") or "") for s in d["screens"])
    res, removed = check(json.loads(json.dumps(raw)), d["turns"], d["manifest"]["source"]["duration_s"] or 0,
                         [s["label"] for s in d["speakers"]] + d["participants"],
                         extra_text=slides + " " + d["manifest"]["source"]["name"] + " " + d["manifest"]["source"]["folder"])
    votes_added = _add_votes(res, d["turns"])
    out = {**res, "removed": removed, "votes_added": votes_added, "model": MODEL_NAME, "parts": parts, "key": key,
           "seconds": round(time.time() - t0, 1),
           "created_at": time.time(), "raw": raw}  # raw = exactly what the model wrote, before the check
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    db.audit("summary", file_id, model=MODEL, seconds=out["seconds"], removed=len(removed))
    return out
