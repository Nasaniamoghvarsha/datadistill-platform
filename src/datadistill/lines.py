"""Repeated text across presentations and documents (line / sentence level).

Whole-slide duplicates are handled by the slide store (dedup.py). This finds the smaller repeats: the same title,
bullet or sentence on several slides, in several revisions of a deck, or in unrelated decks and PDFs. Each unit of
text gets an id (hash of its normalised form); every occurrence lists where else the same text is, so the text can be
stored once and referenced, and a reader sees "also in Network review v2.pptx, slide 4".

Unit:  one line of a slide; long lines (PDF paragraphs) are split into sentences.
Same:  equal after normalising case, bullets / numbering, quotes, dashes, spacing and end punctuation.
       Numbers are kept, so "42 sites upgraded" and "47 sites upgraded" are different text.
Kept:  units of at least 3 words or 15 letters ("Agenda" alone is not worth a reference).
Footer: a unit on at least 3 pages and at least half of the pages of one document (document number,
       "Ericsson Internal", a running title). Listed separately.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from . import db

MIN_WORDS = 3
MIN_CHARS = 15
FOOTER_SHARE = 0.5
FOOTER_MIN_PAGES = 3

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")
_BULLET = re.compile(r"^\s*(?:[-–—•·*▪►○◦]+|\(?\d{1,2}[.)]|\(?[a-zA-Z][.)])\s+")


def norm(s: str) -> str:
    s = (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    s = _BULLET.sub("", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s.strip(" .,;:!?\"'")


def units(text: str):
    """[(original text, normalised)] of a page, in reading order."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = _SENT.split(line) if len(line) > 80 else [line]
        for p in parts:
            n = norm(p)
            if len(n.split()) >= MIN_WORDS or len(re.sub(r"[^a-z]", "", n)) >= MIN_CHARS:
                out.append((_BULLET.sub("", p.strip()), n))
    return out


def line_id(n: str) -> str:
    return hashlib.sha1(n.encode("utf-8")).hexdigest()[:12]


def _documents():
    """Processed documents, one per identical file (exact copies are handled as file duplicates)."""
    rows = db.q("SELECT id, name, system, sha256 FROM files WHERE kind='document' AND status='processed' ORDER BY id")
    seen, out = set(), []
    for r in rows:
        if r["sha256"] in seen:
            continue
        seen.add(r["sha256"])
        out.append(r)
    return out


def index():
    """{line_id: {"text", "occurrences": [{file_id, name, folder, page}], "footer_in": {file_id}}} over all documents."""
    docs = {d["id"]: d for d in _documents()}
    if not docs:
        return {}
    marks = ",".join("?" * len(docs))
    pages = db.q(f"SELECT file_id, page, text FROM pages WHERE file_id IN ({marks}) ORDER BY file_id, page", tuple(docs))
    idx = {}
    per_doc_pages = defaultdict(lambda: defaultdict(set))   # file_id -> line_id -> pages
    n_pages = defaultdict(int)
    for p in pages:
        n_pages[p["file_id"]] += 1
        seen_here = set()
        for text, n in units(p["text"]):
            lid = line_id(n)
            if lid in seen_here:            # the same line twice on one slide counts once
                continue
            seen_here.add(lid)
            d = docs[p["file_id"]]
            e = idx.setdefault(lid, {"id": lid, "text": text, "occurrences": [], "footer_in": set()})
            e["occurrences"].append({"file_id": d["id"], "name": d["name"], "folder": d["system"], "page": p["page"]})
            per_doc_pages[p["file_id"]][lid].add(p["page"])
    for fid, lines in per_doc_pages.items():
        for lid, pg in lines.items():
            if len(pg) >= FOOTER_MIN_PAGES and len(pg) >= FOOTER_SHARE * n_pages[fid]:
                idx[lid]["footer_in"].add(fid)
    return idx


def page_lines(file_id: int, idx=None):
    """{page: [{text, line_id, also_in: [...], footer}]} for one document, with references to every other place."""
    idx = idx if idx is not None else index()
    out = {}
    for p in db.q("SELECT page, text FROM pages WHERE file_id=? ORDER BY page", (file_id,)):
        rows, seen_here = [], set()
        for text, n in units(p["text"]):
            lid = line_id(n)
            if lid in seen_here:
                continue
            seen_here.add(lid)
            e = idx.get(lid)
            also = [o for o in (e["occurrences"] if e else []) if not (o["file_id"] == file_id and o["page"] == p["page"])]
            rows.append({"text": text, "line_id": lid, "also_in": also, "footer": bool(e and file_id in e["footer_in"])})
        out[p["page"]] = rows
    return out


def repeated(idx=None):
    """Text found in more than one place, most widespread first; footers separately."""
    idx = idx if idx is not None else index()
    groups, footers = [], []
    occ_total = chars_total = chars_unique = 0
    for e in idx.values():
        n = len(e["occurrences"])
        occ_total += n
        chars_total += n * len(e["text"])
        chars_unique += len(e["text"])
        if n < 2:
            continue
        g = {"id": e["id"], "text": e["text"], "count": n,
             "documents": len({o["file_id"] for o in e["occurrences"]}), "occurrences": e["occurrences"]}
        (footers if e["footer_in"] else groups).append(g)
    key = lambda g: (-g["documents"], -g["count"], g["text"].lower())
    groups.sort(key=key)
    footers.sort(key=key)
    return {"groups": groups, "footers": footers,
            "totals": {"units": occ_total, "unique": len(idx), "repeated": len(groups), "footers": len(footers),
                       "repeated_occurrences": sum(g["count"] for g in groups + footers),
                       "chars": chars_total, "chars_unique": chars_unique,
                       "saving_pct": round(100 * (1 - chars_unique / max(1, chars_total)), 1)}}
