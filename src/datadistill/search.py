"""Hybrid search (R3, UC-S1): keywords + meaning, fused by rank, then re-ranked.

A recording chunk = one slide segment (speech during it + slide text; a diagram slide contributes who-points-to-whom).
Talk without slides is chunked in ~45 s windows. Document chunk = one page. A hit returns the best sentence + timestamp.

How a query is answered (13 Nov):
  1. understanding  "what did Lisa say about the budget" -> speaker Lisa, query "budget" (English / Swedish / French
                    patterns); accents are ignored everywhere (Asa = Åsa) and a misspelt word is matched to the
                    closest words that occur in the library (Thuvjorn ~ Torbjörn).
  2. two rankings   BM25 on the words, and a multilingual sentence model on the meaning (Swedish and French questions find
                    English talk and the other way round).
  3. fusion         reciprocal rank fusion of the two rankings: no tuned weights, a hit needs only to be good in one.
  4. re-ranking     the best 30 are re-read together with the query by a small multilingual cross-encoder.
  5. cache          chunk embeddings are kept on disk by content: a new recording only embeds its own chunks.
"""
import hashlib
import json
import re
import threading

import numpy as np
from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz, process

from . import db, namefold
from .config import EMBED_MODEL, OUTPUT_DIR, RERANK_MODEL
from .textutil import DOC_NO_RE, LABEL_RE, REV_RE

_lock = threading.Lock()
_index = None
_dirty = True
_model = None
_reranker = None
_reranker_failed = False
TOKEN = re.compile(r"\w+", re.U)
RRF_K = 60
RERANK_TOP = 30
NO_ANSWER_BELOW = 0.05                    # re-ranker probability under which a hit without a matching word is not an answer
CACHE = OUTPUT_DIR / "search-cache.npz"


def tok(s):
    """Lower-case words without accents: 'Åsa Lindqvist' -> ['asa', 'lindqvist']."""
    return TOKEN.findall(namefold.fold(s or ""))


def invalidate():
    global _dirty
    _dirty = True


def warm_up():
    """Load the models and build the index in the background (first search otherwise ~10 s)."""
    def go():
        get_index()
        try:                                              # the re-ranker and the query encoder load on the first question otherwise
            model().encode(["warm up"], normalize_embeddings=True)
            rr = reranker()
            if rr is not None:
                rr.predict([("warm up", "warm up")], show_progress_bar=False)
        except Exception:
            pass
    threading.Thread(target=go, name="search-warmup", daemon=True).start()


def model():
    global _model
    if _model is None:
        import torch
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBED_MODEL, device="cuda" if torch.cuda.is_available() else "cpu")
    return _model


def reranker():
    """The cross-encoder, or None if it cannot be loaded (search still works without it)."""
    global _reranker, _reranker_failed
    if _reranker is None and not _reranker_failed:
        try:
            import torch
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(RERANK_MODEL, device="cuda" if torch.cuda.is_available() else "cpu", max_length=384)
        except Exception:
            _reranker_failed = True
    return _reranker


def _chunks():
    """Recording chunk = ~45 s of dialogue within one shared screen (or without one) + that screen's text.
    Document chunk = one page."""
    from .package import speaker_label
    items = []
    for f in db.q("SELECT id, name, system, status FROM files WHERE kind='video' AND status='processed'"):
        rec = db.q1("SELECT speakers FROM recordings WHERE file_id=?", (f["id"],))
        if not rec:
            continue
        meta = json.loads(rec["speakers"] or "{}")
        screens = {s["idx"]: s for s in db.q(
            """SELECT s.idx, s.kind, s.slide_uid, s.t_start, s.ocr_text, s.image, p.id page_id, p.thumb page_thumb, p.text page_text, p.diagram page_diagram,
                      p.page, pf.name doc_name
               FROM rec_slides s LEFT JOIN pages p ON p.id=s.page_id LEFT JOIN files pf ON pf.id=p.file_id
               WHERE s.file_id=?""", (f["id"],))}
        segs = db.q("SELECT id, start, end, speaker, text, slide_idx FROM segments WHERE file_id=? ORDER BY idx", (f["id"],))
        groups = []
        for g in segs:
            if groups and groups[-1][0]["slide_idx"] == g["slide_idx"] and g["start"] - groups[-1][0]["start"] <= 45:
                groups[-1].append(g)
            else:
                groups.append([g])
        for w in groups:
            s = screens.get(w[0]["slide_idx"])
            screen_text = (s["page_text"] or s["ocr_text"] or "") if s and s["kind"] != "non_slide" else ""
            if s and s.get("page_diagram"):
                screen_text += "\n" + json.loads(s["page_diagram"])["text"]            # who points to whom is searchable too
            items.append({"type": "recording", "file_id": f["id"], "name": f["name"], "folder": f["system"],
                          "t": w[0]["start"], "slide_uid": s["slide_uid"] if s and s["kind"] != "non_slide" else None,
                          "source": {"doc": s["doc_name"], "page": s["page"]} if s and s["doc_name"] else None,
                          # UC-S1: the frame that was on screen (key frame, or the matched document page)
                          "image": (f"/api/recordings/{f['id']}/files/{s['image']}" if s and s["image"] else
                                    f"/api/pages/{s['page_id']}/image?thumb=1" if s and s["page_id"] and s["page_thumb"] else None),
                          "lines": [{"t": g["start"], "seg_id": g["id"], "speaker": speaker_label(g["speaker"], meta),
                                     "text": g["text"]} for g in w],
                          "slide_text": screen_text,
                          "text": " ".join(g["text"] for g in w) + "\n" + screen_text})
        # A slide shown in the recording that is not a page of an uploaded deck (it was shared from somewhere else) is found
        # by its own text: "jvm" on the slide is a hit even when nobody says it. Matched deck pages are searched as pages.
        first_seg = {}
        for g in segs:
            first_seg.setdefault(g["slide_idx"], g["id"])
        seen_uid = set()
        for idx_, s in sorted(screens.items()):
            text = (s["ocr_text"] or "").strip()
            if s["kind"] == "non_slide" or s["page_id"] or sum(c.isalnum() for c in text) < 4:
                continue
            key = s["slide_uid"] or f"screen{idx_}"
            if key in seen_uid:
                continue
            seen_uid.add(key)
            items.append({"type": "screen", "file_id": f["id"], "name": f["name"], "folder": f["system"], "t": s["t_start"] or 0,
                          "seg_id": first_seg.get(idx_), "slide_uid": s["slide_uid"],
                          "image": f"/api/recordings/{f['id']}/files/{s['image']}" if s["image"] else None,
                          "slide_text": text, "text": text, "lines": []})
    pages = db.q("""SELECT p.id, p.file_id, p.page, p.text, p.slide_uid, p.thumb, p.diagram, f.name, f.system, f.status
                    FROM pages p JOIN files f ON f.id=p.file_id WHERE f.status='processed' ORDER BY f.system, f.name, p.page""")
    for p in pages:
        if (p["text"] or "").strip():
            items.append({"type": "page", "file_id": p["file_id"], "name": p["name"], "folder": p["system"],
                          "page": p["page"], "slide_uid": p["slide_uid"],
                          "image": f"/api/pages/{p['id']}/image?thumb=1" if p["thumb"] else None,
                          "slide_text": p["text"],
                          "text": p["text"] + ("\n" + json.loads(p["diagram"])["text"] if p.get("diagram") else ""),
                          "lines": []})
    return items


# ------------------------------------------------------------------ index with an embedding cache
def _embed_cached(texts):
    """Embeddings of the chunk texts; chunks seen before (same text, same model) are read from the cache file."""
    keys = [hashlib.sha1((EMBED_MODEL + "\n" + t[:2000]).encode("utf-8")).hexdigest() for t in texts]
    cache = {}
    if CACHE.exists():
        try:
            z = np.load(CACHE, allow_pickle=False)
            cache = dict(zip(z["keys"].tolist(), z["vecs"]))
        except Exception:
            cache = {}
    missing = [i for i, k in enumerate(keys) if k not in cache]
    if missing:
        vecs = model().encode([texts[i][:2000] for i in missing], normalize_embeddings=True, batch_size=64, show_progress_bar=False)
        for i, v in zip(missing, vecs):
            cache[keys[i]] = np.asarray(v, dtype=np.float32)
        live = {k: cache[k] for k in keys}                 # chunks that no longer exist drop out of the cache
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        np.savez(CACHE, keys=np.array(list(live)), vecs=np.stack(list(live.values())))
    return np.stack([cache[k] for k in keys]) if keys else np.zeros((0, 384), np.float32)


def get_index():
    global _index, _dirty
    with _lock:
        if _dirty or _index is None:
            items = _chunks()
            if items:
                for it in items:
                    it["_tok"] = tok(it["text"]) or ["_"]
                bm = BM25Okapi([it["_tok"] for it in items])
                emb = _embed_cached([i["text"] for i in items])
            else:
                bm, emb = None, np.zeros((0, 384), np.float32)
            vocab = sorted({w for it in items for w in it["_tok"] if len(w) >= 4})
            speakers = sorted({ln["speaker"] for it in items for ln in it["lines"] if ln.get("speaker")})
            _index = {"items": items, "bm25": bm, "emb": emb, "vocab": vocab, "speakers": speakers}
            _dirty = False
        return _index


# ------------------------------------------------------------------ understanding the query
_ASK = [  # "what did Lisa say about the budget", "vad sa Lisa om budgeten", "qu'a dit Lisa sur le budget", "Lisa: budget"
    re.compile(r"^(?:what|where|when|how|why|did|does|do|has|have)\s+(?:did\s+|does\s+|has\s+|have\s+)?(?P<who>[^?]{2,40}?)\s+"
               r"(?:say|said|mention(?:ed)?|talk(?:ed)?|discuss(?:ed)?|ask(?:ed)?|think|explain(?:ed)?|propos(?:e|ed)|suggest(?:ed)?)\s*"
               r"(?:about|on|regarding|that)?\s*(?P<rest>.*)$", re.I),
    re.compile(r"^vad\s+(?:sa|sade|tyckte|sa att)\s+(?P<who>[^?]{2,40}?)\s+(?:om|angående)\s+(?P<rest>.*)$", re.I),
    re.compile(r"^(?:qu'?est-ce que|que|qu')\s*(?:a\s+dit|dit|a\s+d[ée]clar[ée])\s+(?P<who>[^?]{2,40}?)\s+(?:sur|à propos de|de)\s+(?P<rest>.*)$", re.I),
    re.compile(r"^(?P<who>[A-ZÅÄÖÉÈ][\w'’-]+(?: [A-ZÅÄÖÉÈ][\w'’-]+)?)\s*:\s*(?P<rest>.+)$"),
]


def understand(query, speakers):
    """-> (query to search for, speaker label or None). Only a name that is really a speaker in the library counts."""
    q = query.strip()
    for pat in _ASK:
        m = pat.match(q)
        if m and m.group("rest").strip():
            who = m.group("who").strip(" ?.,'")
            hit = _speaker_for(who, speakers)
            if hit:
                return m.group("rest").strip(" ?."), hit
    return q, None


def _speaker_for(who, speakers):
    f = namefold.fold(who)
    exact = [s for s in speakers if namefold.fold(s) == f or f in namefold.fold(s).split()]
    if len(exact) == 1:
        return exact[0]
    return namefold.match_known(who, speakers) if not exact else None


def expand(tokens, vocab, per_word=3):
    """Words that are not in the library are extended by the closest ones that are (spelling, hearing errors)."""
    vset = set(vocab)
    out, added = list(tokens), {}
    for w in tokens:
        if len(w) >= 4 and w not in vset and vocab:
            near = [m[0] for m in process.extract(w, vocab, scorer=fuzz.ratio, score_cutoff=82, limit=per_word)]
            if near:
                added[w] = near
                out += near
    return out, added


# ------------------------------------------------------------------ search
def _snippet(it, qs, speaker):
    """The sentence to open: the best line (of the asked speaker, if one was asked) or the best slide line."""
    if it["lines"]:
        lines = it["lines"]
        if speaker:
            lines = [ln for ln in lines if ln.get("speaker") == speaker] or lines
        best = max(lines, key=lambda ln: len(qs & set(tok(ln["text"]))))
        if qs and not (qs & set(tok(best["text"]))) and it.get("slide_text"):
            on = [ln.strip(" •") for ln in it["slide_text"].splitlines() if ln.strip() and qs & set(tok(ln))]
            if on:                                       # nobody said it: it is on the slide that was shown
                return {"t": best["t"], "seg_id": best["seg_id"], "snippet": max(on, key=lambda ln: len(qs & set(tok(ln)))),
                        "speaker": None, "on_slide": True}
        return {"t": best["t"], "seg_id": best["seg_id"], "snippet": best["text"], "speaker": best["speaker"]}
    # never use footer lines (doc number, classification, revision, page number) as the snippet
    lines = [ln.strip(" •") for ln in (it["slide_text"] or "").splitlines()
             if ln.strip() and not (DOC_NO_RE.search(ln) or LABEL_RE.search(ln) or REV_RE.search(ln))
             and sum(c.isalpha() for c in ln) >= 3]
    return {"snippet": max(lines, key=lambda ln: len(qs & set(tok(ln)))) if lines else ""}


def search(query, k=20, types=None, folder=None, speaker=None, rerank=True):
    """-> {"results": [hit, ...], "interpreted": {"query", "speaker", "also_matched", "reranked"}}"""
    idx = get_index()
    items = idx["items"]
    if not items or not query.strip():
        return {"results": [], "interpreted": {"query": query, "speaker": speaker, "also_matched": {}, "reranked": False}}
    q, asked = understand(query, idx["speakers"])
    speaker = speaker or asked
    qt, added = expand(tok(q), idx["vocab"])
    n = len(items)
    if types and "page" in types:
        types = set(types) | {"screen"}                  # documents and the slides shown in recordings
    allowed = np.ones(n, bool)
    for i, it in enumerate(items):
        if (types and it["type"] not in types) or (folder and it["folder"] != folder):
            allowed[i] = False
        elif speaker and not any(ln.get("speaker") == speaker for ln in it["lines"]):
            allowed[i] = False
    bm = np.asarray(idx["bm25"].get_scores(qt)) if qt else np.zeros(n)
    cos = idx["emb"] @ model().encode([q], normalize_embeddings=True)[0]
    bm = np.where(allowed, bm, -1.0)
    cs = np.where(allowed, cos, -9.0)
    # reciprocal rank fusion: a hit has to rank well in one of the two lists
    score = np.zeros(n)
    for arr, ok in ((bm, bm > 0), (cs, cs >= 0.25)):
        for r, i in enumerate([int(i) for i in np.argsort(-arr, kind="stable") if ok[i]][:200]):
            score[i] += 1.0 / (RRF_K + r + 1)
    cand = [int(i) for i in np.argsort(-score, kind="stable") if score[i] > 0][:max(RERANK_TOP, k * 2)]
    final = {i: float(score[i]) for i in cand}
    rr = reranker() if rerank and len(cand) > 1 else None
    if rr is not None:
        top = cand[:RERANK_TOP]
        s = rr.predict([(q, items[i]["text"][:1500]) for i in top], batch_size=16, show_progress_bar=False)
        z = 1.0 / (1.0 + np.exp(-np.asarray(s, dtype=float)))              # 0..1
        mx = max(final[i] for i in top) or 1.0
        # the re-ranker orders; rank fusion breaks ties and keeps a keyword-only hit from vanishing
        final = {i: 0.75 * float(zz) + 0.25 * final[i] / mx for i, zz in zip(top, z)}
        # Nothing here contains a word of the query and the re-ranker says it does not answer it either: it got in only
        # because an unknown word (an acronym such as "jvm") sits vaguely near everything in embedding space. Better no
        # result than twelve unrelated slides. A hit that contains a query word stays (the re-ranker only orders those).
        no_answer = {i for i, zz in zip(top, z) if bm[i] <= 0 and zz < NO_ANSWER_BELOW}
        final = {i: v for i, v in final.items() if i not in no_answer}
        if not final:
            return {"results": [], "interpreted": {"query": q, "speaker": speaker, "also_matched": added, "reranked": True}}
    qs = set(qt)
    screen_hits = {(items[i]["file_id"], items[i]["slide_uid"]) for i in final if items[i]["type"] == "screen" and items[i]["slide_uid"]}
    out, seen = [], {}
    for i in sorted(final, key=lambda j: -final[j]):
        it = items[i]
        hit = {kk: v for kk, v in it.items() if kk not in ("text", "lines", "_tok")}
        hit["score"] = round(final[i], 3)
        hit.update(_snippet(it, qs, speaker))
        hit["matched_by"] = [name for name, ok in (("words", bm[i] > 0), ("meaning", cos[i] >= 0.35)) if ok]
        if hit.get("on_slide") and (hit["file_id"], hit.get("slide_uid")) in screen_hits:
            continue
        # collapse identical content living in several copies (exact or re-encoded) into one hit
        key = (hit["type"], hit.get("slide_uid") if hit["type"] in ("page", "screen") else re.sub(r"\W+", "", (hit.get("snippet") or "").lower())[:80])
        if key in seen:
            seen[key].setdefault("copies", []).append({"file_id": hit["file_id"], "name": hit["name"], "folder": hit["folder"]})
            continue
        seen[key] = hit
        out.append(hit)
        if len(out) >= k:
            break
    return {"results": out, "interpreted": {"query": q, "speaker": speaker, "also_matched": added, "reranked": rr is not None}}


# ------------------------------------------------------------------ the ranking used until 12 Nov (for the benchmark only)
_legacy = {"model": None, "index": None, "stamp": None}
LEGACY_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _legacy_model():
    if _legacy["model"] is None:
        import torch
        from sentence_transformers import SentenceTransformer
        _legacy["model"] = SentenceTransformer(LEGACY_MODEL, device="cuda" if torch.cuda.is_available() else "cpu")
    return _legacy["model"]


def _plain(s):
    return re.findall(r"\w+", (s or "").lower())


def _legacy_index():
    idx = get_index()
    if _legacy["index"] is None or _legacy["stamp"] is not idx:
        items = idx["items"]
        bm = BM25Okapi([_plain(i["text"]) or ["_"] for i in items]) if items else None
        emb = (_legacy_model().encode([i["text"][:2000] for i in items], normalize_embeddings=True, batch_size=64, show_progress_bar=False)
               if items else np.zeros((0, 384)))
        _legacy.update(index={"items": items, "bm25": bm, "emb": np.asarray(emb)}, stamp=idx)
    return _legacy["index"]


def search_legacy(query, k=20):
    """BM25 + all-MiniLM-L6-v2, 50/50, as in the version of 12 Nov. Same hit fields (without the extras)."""
    idx = _legacy_index()
    items = idx["items"]
    if not items or not query.strip():
        return []
    qt = _plain(query)
    bm = np.asarray(idx["bm25"].get_scores(qt)) if qt else np.zeros(len(items))
    bmn = bm / bm.max() if bm.max() > 0 else bm
    cos = idx["emb"] @ _legacy_model().encode([query], normalize_embeddings=True)[0]
    score = 0.5 * bmn + 0.5 * np.clip(cos, 0, None)
    out = []
    for i in np.argsort(-score, kind="stable"):
        if score[i] < 0.2 or len(out) >= k:
            break
        it = items[i]
        hit = {kk: v for kk, v in it.items() if kk not in ("text", "lines", "_tok")}
        hit["score"] = round(float(score[i]), 3)
        if it["lines"]:
            best = max(it["lines"], key=lambda ln: len(set(qt) & set(_plain(ln["text"]))))
            hit.update(t=best["t"], seg_id=best["seg_id"], snippet=best["text"], speaker=best["speaker"])
        out.append(hit)
    return out
