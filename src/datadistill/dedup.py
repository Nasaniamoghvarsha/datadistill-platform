"""Duplicate detection across systems (R2, R5, UC-D1, UC-D3, SD3, SD4). Spec §4.5.

L0  exact bytes (sha256)              -> exact copies, e.g. "deck (1).pdf"
LV  video fingerprint (1 fps dHash)   -> re-encoded copies of the same recording
L2/L3 slide store (text hash + pHash) -> shared slides, revisions (document families)
"""
import json
from collections import defaultdict
from itertools import combinations

from . import db
from .config import OUTPUT_DIR, is_personal, system_rank
from .textutil import normalize_name

LV_DURATION_TOL = 0.01
LV_MIN_SIM = 0.9
LV_BITS = 8
FAMILY_JACCARD = 0.5


def active_files(kinds=("video", "document")):
    rows = db.q(f"SELECT * FROM files WHERE status NOT IN ('removed','quarantined','shortcut') AND kind IN "
                f"({','.join('?' * len(kinds))})", kinds)
    for r in rows:
        r["meta"] = json.loads(r["meta"] or "{}")
    return rows


def _rank(m):
    """Canonical preference: shared system, has a doc number, not a '(1)' copy name, oldest."""
    return (system_rank(m["system"]), not m["meta"].get("doc_no"),
            normalize_name(m["name"]) != m["name"].lower(), m["added_at"], m["id"])


def _canonical(key, members, level="L0"):
    o = db.q1("SELECT file_id FROM canonical WHERE group_key=?", (key,))
    ids = {m["id"] for m in members}
    if o and o["file_id"] in ids:
        return o["file_id"], "manual"
    if level == "LV":  # re-encoded copies: keep the best quality (resolution, then bytes)
        best = min(members, key=lambda m: (-(m["meta"].get("width", 0) * m["meta"].get("height", 0)), -m["size"]) + _rank(m))
    else:
        best = min(members, key=_rank)
    return best["id"], "rule"


def _group(key, level, members, extra=None):
    can, how = _canonical(key, members, level)
    reclaim = sum(m["size"] for m in members if m["id"] != can) if level in ("L0", "LV") else 0
    return {"key": key, "level": level, "canonical": can, "canonical_by": how, "reclaimable_bytes": reclaim,
            "members": [{"id": m["id"], "name": m["name"], "system": m["system"], "size": m["size"],
                         "personal": is_personal(m["system"]), "kind": m["kind"], "status": m["status"],
                         "doc_no": m["meta"].get("doc_no", ""), "rev": m["meta"].get("rev", "")} for m in members],
            **(extra or {})}


def exact_groups(files=None):
    files = files or active_files()
    by = defaultdict(list)
    for f in files:
        by[f["sha256"]].append(f)
    return [_group("L0:" + sha[:16], "L0", ms) for sha, ms in by.items() if len(ms) > 1]


def load_fingerprint(file_id):
    p = OUTPUT_DIR / "recordings" / str(file_id) / "fingerprint.json"
    return json.loads(p.read_text()) if p.exists() else None


def fingerprint_similarity(a, b, max_offset=3):
    ha, hb = a["dhash64"], b["dhash64"]
    if not ha or not hb:
        return 0.0
    best = 0.0
    for off in range(-max_offset, max_offset + 1):
        pairs = [(ha[i], hb[i + off]) for i in range(len(ha)) if 0 <= i + off < len(hb)]
        if not pairs:
            continue
        ok = sum(bin(x ^ y).count("1") <= LV_BITS for x, y in pairs)
        best = max(best, ok / max(len(ha), len(hb)))
    return best


def video_groups(files=None):
    vids = [f for f in (files or active_files(("video",))) if f["kind"] == "video"]
    fps = {v["id"]: load_fingerprint(v["id"]) for v in vids}
    vids = [v for v in vids if fps[v["id"]]]
    parent = {v["id"]: v["id"] for v in vids}
    sims = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in combinations(vids, 2):
        if a["sha256"] == b["sha256"]:
            continue  # exact copies are L0
        da, dbb = fps[a["id"]]["duration"], fps[b["id"]]["duration"]
        if abs(da - dbb) > max(1.0, LV_DURATION_TOL * max(da, dbb)):
            continue
        s = fingerprint_similarity(fps[a["id"]], fps[b["id"]])
        if s >= LV_MIN_SIM:
            parent[find(a["id"])] = find(b["id"])
            sims[(a["id"], b["id"])] = round(s, 3)
    groups = defaultdict(list)
    for v in vids:
        groups[find(v["id"])].append(v)
    out = []
    for ms in groups.values():
        shas = {m["sha256"] for m in ms}
        if len(shas) > 1:
            ids = {m["id"] for m in ms}
            out.append(_group(f"LV:{min(ids)}", "LV", ms,
                              {"similarity": {f"{a}-{b}": s for (a, b), s in sims.items() if a in ids}}))
    return out


def doc_slides(files=None):
    docs = [f for f in (files or active_files(("document",))) if f["kind"] == "document" and f["status"] == "processed"]
    docs.sort(key=_rank)  # originals on shared systems first: they represent their copies in lists
    out = {}
    for d in docs:
        out[d["id"]] = (d, [r["slide_uid"] for r in db.q("SELECT slide_uid FROM pages WHERE file_id=? ORDER BY page", (d["id"],))])
    return out


def families(ds=None):
    """Revisions / variants of the same presentation (same doc_no, or >= 50% shared slides)."""
    ds = ds or doc_slides()
    ids = list(ds)
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    reps = {}
    for i in ids:  # exact copies collapse to one representative (ids are already in canonical order)
        reps.setdefault(ds[i][0]["sha256"], i)
    uniq = list(reps.values())
    for a, b in combinations(uniq, 2):
        fa, fb = ds[a][0], ds[b][0]
        sa, sb = set(ds[a][1]), set(ds[b][1])
        jac = len(sa & sb) / max(1, len(sa | sb))
        same_no = fa["meta"].get("doc_no") and fa["meta"].get("doc_no") == fb["meta"].get("doc_no")
        if same_no or jac >= FAMILY_JACCARD:
            parent[find(a)] = find(b)
    groups = defaultdict(list)
    for i in uniq:
        groups[find(i)].append(i)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda i: (ds[i][0]["meta"].get("rev", ""), ds[i][0]["added_at"]))
        base_uids = ds[members[0]][1]
        versions = []
        for i in members:
            uids = ds[i][1]
            versions.append({"id": i, "name": ds[i][0]["name"], "system": ds[i][0]["system"],
                             "rev": ds[i][0]["meta"].get("rev", ""), "doc_no": ds[i][0]["meta"].get("doc_no", ""),
                             "slides": [{"page": n + 1, "uid": u, "state": "same" if u in base_uids else "changed"}
                                        for n, u in enumerate(uids)],
                             # pages of the first version that this version no longer has
                             "removed": [n + 1 for n, u in enumerate(base_uids) if u not in set(uids)] if i != members[0] else []})
        all_uids = [u for i in members for u in ds[i][1]]
        uniq_uids = set(all_uids)
        out.append({"key": f"FAM:{min(members)}", "doc_no": ds[members[0]][0]["meta"].get("doc_no", ""),
                    "versions": versions, "slide_occurrences": len(all_uids), "unique_slides": len(uniq_uids),
                    "stored_once_saving_pct": round(100 * (1 - len(uniq_uids) / max(1, len(all_uids))), 1)})
    return out


def compare_documents(a_id, b_id, min_edit=60):
    """Page by page comparison of two documents (PDF, PPTX or picture): which pages are the same, which were edited, which only
    one of them has, which moved. Pages are the same when the slide store gave them one id (same text and picture); the pages
    that are left between two matching stretches are paired by how alike their text is (an edited page), the rest are only
    in one document. -> {"a", "b", "rows": [{"state", "a_page", "b_page", "similarity"}], "counts"}"""
    from difflib import SequenceMatcher

    from rapidfuzz import fuzz

    def load(fid):
        f = db.file_row(fid)
        if not f or f["kind"] != "document":
            raise ValueError("not a document")
        pgs = db.q("SELECT id, page, slide_uid, text_norm, thumb FROM pages WHERE file_id=? ORDER BY page", (fid,))
        return f, pgs

    fa, pa = load(a_id)
    fb, pb = load(b_id)
    ua = [p["slide_uid"] or f"a{p['page']}" for p in pa]
    ub = [p["slide_uid"] or f"b{p['page']}" for p in pb]
    set_b, set_a = set(ub), set(ua)
    rows = []

    def row(state, a=None, b=None, sim=None):
        rows.append({"state": state, "a_page": a["page"] if a else None, "b_page": b["page"] if b else None,
                     "a_page_id": a["id"] if a else None, "b_page_id": b["id"] if b else None,
                     "similarity": sim})

    for op, i1, i2, j1, j2 in SequenceMatcher(None, ua, ub, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                row("same", pa[i1 + k], pb[j1 + k], 100)
            continue
        left = [p for p in pa[i1:i2]]
        right = [p for p in pb[j1:j2]]
        # a page that exists on the other side, just elsewhere, moved
        moved_a = {p["page"] for p in left if (p["slide_uid"] or "") in set_b and p["slide_uid"]}
        moved_b = {p["page"] for p in right if (p["slide_uid"] or "") in set_a and p["slide_uid"]}
        for p in left:
            if p["page"] in moved_a:
                q = next(x for x in pb if x["slide_uid"] == p["slide_uid"])
                row("moved", p, q, 100)
        left = [p for p in left if p["page"] not in moved_a]
        right = [p for p in right if p["page"] not in moved_b]
        # the rest: pair by text, in order (an edited page stays near its place)
        used = set()
        for p in left:
            best, best_s = None, 0.0
            for q in right:
                if q["page"] in used:
                    continue
                s = fuzz.ratio(p["text_norm"] or "", q["text_norm"] or "")
                if s > best_s:
                    best, best_s = q, s
            if best and best_s >= min_edit:
                used.add(best["page"])
                row("edited", p, best, round(best_s))
            else:
                row("only_a", p)
        for q in right:
            if q["page"] not in used:
                row("only_b", None, q)
    order = {"same": 0, "edited": 0, "moved": 0, "only_a": 0, "only_b": 0}
    rows.sort(key=lambda r: (r["b_page"] if r["b_page"] is not None else 10 ** 6 + (r["a_page"] or 0), r["a_page"] or 0))
    counts = {k: sum(r["state"] == k for r in rows) for k in order}
    ident = fa["sha256"] == fb["sha256"]
    return {"a": {"id": fa["id"], "name": fa["name"], "system": fa["system"], "pages": len(pa)},
            "b": {"id": fb["id"], "name": fb["name"], "system": fb["system"], "pages": len(pb)},
            "identical_files": ident, "rows": rows, "counts": counts,
            "same_pct": round(100 * (counts["same"] + counts["moved"]) / max(1, max(len(pa), len(pb))))}


def shared_slides(ds=None):
    ds = ds or doc_slides()
    pairs = []
    seen_sha = set()
    for a, b in combinations(ds, 2):
        fa, fb = ds[a][0], ds[b][0]
        if fa["sha256"] == fb["sha256"]:
            continue
        common = [u for u in dict.fromkeys(ds[a][1]) if u in set(ds[b][1])]
        if common:
            key = tuple(sorted((fa["sha256"], fb["sha256"])))
            if key in seen_sha:
                continue
            seen_sha.add(key)
            pairs.append({"a": {"id": a, "name": fa["name"], "system": fa["system"], "pages": len(ds[a][1])},
                          "b": {"id": b, "name": fb["name"], "system": fb["system"], "pages": len(ds[b][1])},
                          "shared": len(common),
                          "a_pages": [n + 1 for n, u in enumerate(ds[a][1]) if u in common],
                          "b_pages": [n + 1 for n, u in enumerate(ds[b][1]) if u in common]})
    return sorted(pairs, key=lambda p: -p["shared"])


def slide_store_stats(ds=None):
    """Bytes saved if every unique slide is stored once (content-addressed store)."""
    ds = ds or doc_slides()
    occ, seen, saved = 0, set(), 0.0
    for i, (f, uids) in ds.items():
        per_page = f["size"] / max(1, len(uids))
        for u in uids:
            occ += 1
            if u in seen:
                saved += per_page
            seen.add(u)
    total = sum(f["size"] for f, _ in ds.values())
    return {"slide_occurrences": occ, "unique_slides": len(seen), "doc_bytes": total,
            "store_once_saved_bytes": int(saved), "store_once_saving_pct": round(100 * saved / max(1, total), 1)}


def summary():
    files = active_files()
    ds = doc_slides(files)
    ex = exact_groups(files)
    lv = video_groups(files)
    total = sum(f["size"] for f in files)
    return {"exact": ex, "video": lv, "families": families(ds), "shared": shared_slides(ds),
            "slide_store": slide_store_stats(ds), "totals": reclaim_totals(files, ex, lv)}


def _words(file_id):
    import re
    text = " ".join(r["text"] for r in db.q("SELECT text FROM segments WHERE file_id=?", (file_id,)))
    return {w for w in re.findall(r"[a-z0-9']{4,}", text.lower())}


def same_speech(a, b):
    """Share of shared content words between two transcripts (both transcribed): same meeting -> high."""
    wa, wb = _words(a), _words(b)
    if len(wa) < 20 or len(wb) < 20:
        return None
    return round(len(wa & wb) / len(wa | wb), 3)


def report():
    """Everything about duplicates, for the Duplicates page (R2, R5, SD3, SD4, UC-D1, UC-D3)."""
    files = active_files()
    ds = doc_slides(files)
    # 1. already handled: master copies and the shortcuts that point to them
    stored_once = {}
    for sc in db.q("SELECT * FROM files WHERE status='shortcut'"):
        sc["meta"] = json.loads(sc["meta"] or "{}")
        m = db.file_row(sc["meta"].get("master_id"))
        if not m:
            continue
        g = stored_once.setdefault(m["id"], {
            "master": {"id": m["id"], "name": m["name"], "folder": m["system"], "kind": m["kind"], "size": m["size"],
                       "status": m["status"], "original_deleted": bool(m["meta"].get("original_deleted"))},
            "copies": [], "saved_bytes": 0})
        g["copies"].append({"id": sc["id"], "name": sc["name"], "folder": sc["system"], "size": sc["size"],
                            "how": "exact copy" if sc["sha256"] == m["sha256"] else "same recording, other file"})
        g["saved_bytes"] += sc["size"]
    # 2. waiting for a decision
    pending = []
    for g in exact_groups(files):  # identical bytes stored more than once (e.g. copied into a folder in Explorer)
        pending.append({"type": "exact", "canonical": g["canonical"], "members": g["members"], "reclaimable_bytes": g["reclaimable_bytes"],
                        "evidence": "identical bytes (SHA-256)"})
    for f in db.q("SELECT * FROM files WHERE status='duplicate'"):  # found at upload, user has not decided yet
        meta = json.loads(f["meta"] or "{}")
        m = meta.get("similar_to") or {}
        mf = db.file_row(m.get("id"))
        if not mf:
            continue
        sp = m.get("speech") or []
        pending.append({"type": "same_recording", "canonical": mf["id"], "reclaimable_bytes": f["size"],
                        "evidence": f"{round(100 * m.get('frames', 0))}% of the picture"
                                    + (f", {round(100 * sum(sp) / len(sp))}% of sampled speech" if sp else "") + " match",
                        "members": [{"id": x["id"], "name": x["name"], "system": x["system"], "size": x["size"], "status": x["status"],
                                     "kind": "video"} for x in (mf, f)]})
    for g in video_groups(files):  # two transcribed recordings that look alike: confirm with their transcripts
        ids = [x["id"] for x in g["members"]]
        sims = [same_speech(g["canonical"], i) for i in ids if i != g["canonical"]]
        if all(s is not None and s >= 0.5 for s in sims):
            pending.append({"type": "same_recording", "canonical": g["canonical"], "members": g["members"],
                            "reclaimable_bytes": g["reclaimable_bytes"],
                            "evidence": f"picture fingerprint + {round(100 * min(sims))}% of transcript words shared"})
    fam = families(ds)
    sh = shared_slides(ds)
    from . import lines
    rt = lines.repeated()
    return {"stored_once": sorted(stored_once.values(), key=lambda g: -g["saved_bytes"]), "pending": pending,
            "families": fam, "shared": sh,
            "documents": [{"id": i, "name": d[0]["name"], "system": d[0]["system"], "pages": len(d[1])} for i, d in ds.items()], "slide_store": slide_store_stats(ds), "repeated_text": rt,
            "totals": {"shortcuts": sum(len(g["copies"]) for g in stored_once.values()),
                       "saved_bytes": sum(g["saved_bytes"] for g in stored_once.values()),
                       "pending": len(pending), "pending_bytes": sum(p["reclaimable_bytes"] for p in pending),
                       "families": len(fam), "shared_pairs": len(sh), "repeated_lines": rt["totals"]["repeated"]}}


def reclaim_totals(files, ex, lv):
    """Count each file once, even if it sits in an exact-copy group AND a re-encoded group.
    Every group keeps at least its canonical copy."""
    size = {f["id"]: f["size"] for f in files}
    keep = {g["canonical"] for g in lv}
    drop = set()
    for g in ex + lv:
        drop |= {m["id"] for m in g["members"] if m["id"] != g["canonical"]}
    drop -= keep
    total = sum(size.values())
    reclaim = sum(size.get(i, 0) for i in drop)
    return {"files": len(files), "bytes": total, "reclaimable_bytes": reclaim, "reclaimable_files": len(drop),
            "reclaimable_pct": round(100 * reclaim / max(1, total), 1)}
