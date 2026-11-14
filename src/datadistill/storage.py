"""Storage metrics: what was ingested, what is stored now, and where the savings come from.

ingested   = every library entry at its original size (shortcuts count at the size of the copy they replace)
stored     = originals still on disk + distilled output (transcripts, snapshots, voice samples)
saved      = ingested - stored
"""
import json
from pathlib import Path

from . import db
from .config import DATA_DIR, OUTPUT_DIR


def _dir_bytes(p: Path, skip=("fingerprint.json",)):
    return sum(x.stat().st_size for x in p.rglob("*") if x.is_file() and x.name not in skip) if p.exists() else 0


def distilled_parts(file_id):
    """Bytes of the distilled form of a recording: transcript (JSON + text), snapshots, voice samples."""
    base = OUTPUT_DIR / "recordings" / str(file_id)
    parts = {"transcript": sum((base / n).stat().st_size for n in ("transcript.json", "transcript.md") if (base / n).exists()),
             "snapshots": _dir_bytes(base / "snapshots"), "voices": _dir_bytes(base / "voices")}
    parts["total"] = sum(parts.values())
    return parts


def distilled_bytes(file_id):
    return distilled_parts(file_id)["total"]


def _doc_bytes(file_id):
    r = db.q1("SELECT SUM(LENGTH(COALESCE(text,''))) n FROM pages WHERE file_id=?", (file_id,))
    return int(r["n"] or 0)


def _files(folder=None):
    sql = "SELECT * FROM files WHERE kind IN ('video','document') AND status NOT IN ('removed')"
    p = ()
    if folder:
        sql += " AND system=?"
        p = (folder,)
    rows = db.q(sql, p)
    for r in rows:
        r["meta"] = json.loads(r["meta"] or "{}")
    return rows


def _entry(f):
    shortcut = f["status"] == "shortcut"
    deleted = bool(f["meta"].get("original_deleted"))
    on_disk = not shortcut and not deleted and (DATA_DIR / f["path"]).exists()
    distilled = {"transcript": 0, "snapshots": 0, "voices": 0, "total": 0}
    if not shortcut and f["status"] == "processed":
        distilled = distilled_parts(f["id"]) if f["kind"] == "video" else {"transcript": _doc_bytes(f["id"]), "snapshots": 0,
                                                                            "voices": 0, "total": _doc_bytes(f["id"])}
    return {"shortcut": shortcut, "deleted": deleted, "on_disk": on_disk, "original": f["size"],
            "disk": f["size"] if on_disk else 0, "distilled": distilled}


def folder_usage(folder):
    """Bytes a folder occupies now (originals on disk + distilled output)."""
    es = [_entry(f) for f in _files(folder)]
    return {"bytes": sum(e["disk"] + e["distilled"]["total"] for e in es),
            "originals": sum(e["disk"] for e in es), "distilled": sum(e["distilled"]["total"] for e in es)}


def summary():
    files = _files()
    rows, folders = [], {}
    tot = {"ingested": 0, "originals_on_disk": 0, "distilled": 0, "shortcut_count": 0, "shortcut_bytes": 0,
           "deleted_count": 0, "deleted_bytes": 0, "recordings": 0, "documents": 0, "hours": 0.0, "words": 0,
           "snapshots": 0, "snapshot_bytes": 0, "voice_bytes": 0, "transcript_bytes": 0,
           "potential_bytes": 0, "potential_count": 0}
    words = {r["file_id"]: r["n"] for r in db.q(
        "SELECT file_id, SUM(LENGTH(text) - LENGTH(REPLACE(text, ' ', '')) + 1) n FROM segments GROUP BY file_id")}
    nsnap = {r["file_id"]: r["n"] for r in db.q("SELECT file_id, COUNT(*) n FROM snapshots GROUP BY file_id")}
    for f in files:
        e = _entry(f)
        fo = folders.setdefault(f["system"], {"folder": f["system"], "files": 0, "ingested": 0, "originals_on_disk": 0,
                                              "distilled": 0, "shortcuts": 0, "deleted": 0})
        fo["files"] += 1
        fo["ingested"] += e["original"]
        fo["originals_on_disk"] += e["disk"]
        fo["distilled"] += e["distilled"]["total"]
        fo["shortcuts"] += e["shortcut"]
        fo["deleted"] += e["deleted"]
        tot["ingested"] += e["original"]
        tot["originals_on_disk"] += e["disk"]
        tot["distilled"] += e["distilled"]["total"]
        if e["shortcut"]:
            tot["shortcut_count"] += 1
            tot["shortcut_bytes"] += e["original"]
            continue
        tot["recordings" if f["kind"] == "video" else "documents"] += 1
        if e["deleted"]:
            tot["deleted_count"] += 1
            tot["deleted_bytes"] += e["original"]
        if f["kind"] == "video" and f["status"] == "processed":
            d = e["distilled"]
            tot["hours"] += (f["meta"].get("duration") or 0) / 3600
            tot["words"] += words.get(f["id"], 0)
            tot["snapshots"] += nsnap.get(f["id"], 0)
            tot["snapshot_bytes"] += d["snapshots"]
            tot["voice_bytes"] += d["voices"]
            tot["transcript_bytes"] += d["transcript"]
            if e["on_disk"] and words.get(f["id"], 0) > 0:  # a video without speech is kept on purpose
                tot["potential_count"] += 1
                tot["potential_bytes"] += max(0, e["original"] - d["total"])
            rows.append({"id": f["id"], "name": f["name"], "folder": f["system"], "duration_s": f["meta"].get("duration"),
                         "original": e["original"], "on_disk": e["on_disk"], "deleted": e["deleted"],
                         "transcript": d["transcript"], "snapshots": d["snapshots"], "snapshot_count": nsnap.get(f["id"], 0),
                         "voices": d["voices"], "distilled": d["total"],
                         "reduction": round(1 - d["total"] / e["original"], 4) if e["original"] else None})
    tot["stored"] = tot["originals_on_disk"] + tot["distilled"]
    tot["saved"] = max(0, tot["ingested"] - tot["stored"])
    tot["saved_share"] = round(tot["saved"] / tot["ingested"], 4) if tot["ingested"] else 0
    tot["hours"] = round(tot["hours"], 2)
    for fo in folders.values():
        fo["stored"] = fo["originals_on_disk"] + fo["distilled"]
        fo["saved"] = max(0, fo["ingested"] - fo["stored"])
    return {"totals": tot, "folders": sorted(folders.values(), key=lambda x: x["folder"].lower()),
            "recordings": sorted(rows, key=lambda r: (r["folder"].lower(), r["name"].lower()))}
