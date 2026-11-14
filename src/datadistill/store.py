"""Content-addressed slide store: the single source of truth for slides (UC-F4, UC-D2, UC-D3).

The same slide anywhere (in any deck, revision or recording) resolves to the same slide_uid.
Exact match: same normalised-text hash and pHash distance <= 10.
Text-less slides: pHash distance <= 4 only (never merged on weak evidence).
"""
import time

from . import db
from .textutil import hamming, make_uid, text_hash

TEXT_PHASH_MAX = 10
NOTEXT_PHASH_MAX = 4
MIN_TEXT = 10


def assign_uid(norm: str, ph: str, image: str, source: str) -> str:
    th = text_hash(norm)
    if len(norm) >= MIN_TEXT:
        for r in db.q("SELECT uid, phash FROM slide_store WHERE text_hash=?", (th,)):
            if hamming(r["phash"], ph) <= TEXT_PHASH_MAX:
                return r["uid"]
    else:
        for r in db.q("SELECT uid, phash, text_norm FROM slide_store"):
            if len(r["text_norm"] or "") < MIN_TEXT and hamming(r["phash"], ph) <= NOTEXT_PHASH_MAX:
                return r["uid"]
    uid = make_uid(norm, ph)
    db.ex("INSERT OR IGNORE INTO slide_store(uid, text_hash, phash, text_norm, image, source, created_at) "
          "VALUES (?,?,?,?,?,?,?)", (uid, th, ph, norm, image, source, time.time()))
    return uid


def occurrences(uid: str) -> dict:
    pages = db.q("""SELECT p.id page_id, p.file_id, p.page, p.thumb, f.name, f.system, f.status
                    FROM pages p JOIN files f ON f.id=p.file_id
                    WHERE p.slide_uid=? AND f.status NOT IN ('removed') ORDER BY f.system, f.name, p.page""", (uid,))
    recs = db.q("""SELECT s.file_id, s.idx, s.t_start, s.t_end, f.name, f.system, f.status
                   FROM rec_slides s JOIN files f ON f.id=s.file_id
                   WHERE s.slide_uid=? AND f.status NOT IN ('removed') ORDER BY f.name, s.t_start""", (uid,))
    return {"pages": pages, "recordings": recs}
