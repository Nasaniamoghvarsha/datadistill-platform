"""People: find a person by name and give back everything that person said, recording by recording.

A person here is a speaker name as shown in the transcripts (from a name label, an introduction, captions, or a name you typed).
The same name in several recordings is one person for this search; two different people with one first name are told apart
because a first name alone only matches when exactly one person has it. Unnamed voices ("Speaker 3") are found only when you
type exactly that, because that label means a different voice in every recording.
"""
import json
import re

from rapidfuzz import fuzz

from . import db, namefold, package


def _fold_tokens(s):
    return [t for t in re.split(r"[^a-z0-9]+", namefold.fold(s)) if t]


def _all():
    """{fold name: {"name", "files": [{file_id, name, folder, sids, talk_s}]}} for every processed recording."""
    people = {}
    for rec in db.q("SELECT r.file_id, r.speakers, f.name, f.system FROM recordings r JOIN files f ON f.id=r.file_id "
                    "WHERE f.status IN ('processed')"):
        meta = json.loads(rec["speakers"] or "{}")
        for sid, m in meta.items():
            name = (m.get("name") or "").strip()
            if not name or name.lower().startswith("music"):
                continue
            key = " ".join(_fold_tokens(name))
            p = people.setdefault(key, {"name": name, "files": {}})
            f = p["files"].setdefault(rec["file_id"], {"file_id": rec["file_id"], "name": rec["name"], "folder": rec["system"], "sids": [], "talk_s": 0.0})
            f["sids"].append(sid)
            f["talk_s"] += float(m.get("talk_time_s") or 0)
    return people


def _unnamed(label):
    """'Speaker 3' typed exactly: the voice with that label in every recording where it has no name."""
    m = re.fullmatch(r"speaker\s*(\d+)", label.strip().lower())
    if not m:
        return None
    n = m.group(1)
    p = {"name": f"Speaker {n}", "files": {}}
    for rec in db.q("SELECT r.file_id, r.speakers, f.name, f.system FROM recordings r JOIN files f ON f.id=r.file_id WHERE f.status='processed'"):
        meta = json.loads(rec["speakers"] or "{}")
        m_ = meta.get(f"S{n}")
        if m_ is not None and not (m_.get("name") or "").strip():
            p["files"][rec["file_id"]] = {"file_id": rec["file_id"], "name": rec["name"], "folder": rec["system"], "sids": [f"S{n}"],
                                          "talk_s": float(m_.get("talk_time_s") or 0)}
    return p if p["files"] else None


def _score(qt, name):
    """How well does the typed text name this person? 0 when it does not."""
    nt = _fold_tokens(name)
    if not qt or not nt:
        return 0
    if qt == nt:
        return 100
    if all(any(t == n for n in nt) for t in qt):
        return 95                                          # every typed word is a word of the name ("woodham", "thomas woodham")
    if all(any(n.startswith(t) for n in nt) for t in qt) and all(len(t) >= 3 for t in qt):
        return 88                                          # a start of the name ("tho")
    if len(qt) == len(nt) or len(qt) == 1:
        if all(max(fuzz.ratio(t, n) for n in nt) >= 86 for t in qt) and all(len(t) >= 4 for t in qt):
            return 80                                      # a small typo ("andres")
    return 0


def find(q, limit=4):
    """People whose name the query names: [{"name", "lines", "talk_s", "files": [...]}], best first. A first name that fits two
    different people matches both (the reader sees both and picks)."""
    q = (q or "").strip()
    if not q:
        return []
    out = []
    un = _unnamed(q)
    if un:
        out.append(un)
    qt = _fold_tokens(q)
    scored = [(s, p) for p in _all().values() if (s := _score(qt, p["name"])) > 0]
    scored.sort(key=lambda x: -x[0])
    out += [p for _, p in scored]
    res = []
    for p in out[:limit]:
        files = sorted(p["files"].values(), key=lambda f: -f["talk_s"])
        for f in files:
            f["lines"] = _count_lines(f["file_id"], f["sids"])
        res.append({"name": p["name"], "files": files, "lines": sum(f["lines"] for f in files), "talk_s": round(sum(f["talk_s"] for f in files), 1)})
    return res


def _count_lines(file_id, sids):
    marks = ",".join("?" * len(sids))
    return db.q1(f"SELECT COUNT(*) n FROM segments WHERE file_id=? AND speaker IN ({marks})", (file_id, *sids))["n"]


def detail(name, file_id):
    """Everything one person said in one recording, with the slide on screen at each line, and where others mention the name."""
    d = package.load_recording(file_id)
    if d is None:
        return None
    key = namefold.fold(name).strip()
    mine = [t for t in d["turns"] if namefold.fold(t["speaker_name"]).strip() == key]
    screens = {s["idx"]: s for s in d["screens"]}
    first = namefold.fold(name).split()[0] if namefold.fold(name).split() else ""
    others = {n for n in (p["name"] for p in _all().values()) if n and namefold.fold(n).split()[:1] == [first]}
    pattern = re.compile(r"\b" + re.escape(first) + r"\b") if len(first) >= 3 and len(others) <= 1 else None
    full = re.compile(r"\b" + re.escape(key) + r"\b") if " " in key else None
    lines, shown = [], None
    for t in mine:
        s = screens.get(t["slide_idx"])
        slide = None
        if s and s["kind"] != "non_slide":
            img = (f"/api/recordings/{file_id}/files/{s['image']}" if s.get("image")
                   else f"/api/pages/{s['page_id']}/image?thumb=1" if s.get("page_id") and s.get("page_thumb") else None)
            slide = {"idx": s["idx"], "title": s["title"], "image": img, "change": s["idx"] != shown}
            shown = s["idx"]
        else:
            shown = None
        lines.append({"id": t["id"], "idx": t["idx"], "start": t["start"], "end": t["end"], "text": t["text"], "confidence": t["confidence"], "slide": slide})
    mentions = []
    for t in d["turns"]:
        if t in mine:
            continue
        txt = namefold.fold(t["text"])
        if (full and full.search(txt)) or (pattern and pattern.search(txt)):
            mentions.append({"id": t["id"], "idx": t["idx"], "start": t["start"], "speaker": t["speaker_name"], "text": t["text"]})
    return {"name": name, "file": {"id": file_id, "name": d["file"]["name"], "folder": d["file"]["system"]},
            "lines": lines, "mentions": mentions[:60], "talk_s": round(sum(t["end"] - t["start"] for t in mine), 1),
            "slides": len({ln["slide"]["idx"] for ln in lines if ln["slide"]})}


def as_text(name, file_id):
    """Plain text of the above (for a download)."""
    det = detail(name, file_id)
    if det is None:
        return ""
    out = [f"{det['name']} in {det['file']['name']}", f"{len(det['lines'])} lines, {det['talk_s']} s of speech", ""]
    for ln in det["lines"]:
        t = int(ln["start"])
        out.append(f"[{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}] {ln['text']}")
        if ln["slide"] and ln["slide"]["change"] and ln["slide"]["title"]:
            out.append(f"    (slide: {ln['slide']['title']})")
    if det["mentions"]:
        out += ["", "Said about this person by others:"]
        for m in det["mentions"]:
            t = int(m["start"])
            out.append(f"[{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}] {m['speaker']}: {m['text']}")
    return "\n".join(out) + "\n"
